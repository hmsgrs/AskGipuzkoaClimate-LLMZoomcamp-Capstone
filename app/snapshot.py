"""Create and verify immutable, WAL-safe data snapshot bundles."""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from filelock import FileLock


SNAPSHOT_SCHEMA_VERSION = 1
DATABASE_NAME = "snapshot.sqlite"
MANIFEST_NAME = "manifest.json"
MANIFEST_CHECKSUM_NAME = "manifest.sha256"
SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOGICAL_TABLES = (
    "sources",
    "documents",
    "chunks",
    "evaluation_questions",
    "weather_stations",
    "weather_forecasts",
    "weather_api_snapshots",
    "aemet_daily_observations",
    "hazard_alerts",
    "ingestion_runs",
    "snapshot_metadata",
)
RETRIEVAL_TIME_TABLES = (
    "sources",
    "documents",
    "weather_stations",
    "weather_forecasts",
    "weather_api_snapshots",
    "aemet_daily_observations",
    "hazard_alerts",
)


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be created, verified, or installed."""


def utc_now():
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _validate_snapshot_id(snapshot_id: str):
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise SnapshotError(
            "Snapshot ID must contain only letters, numbers, '.', '_', or '-'"
        )


def open_readonly_database(path: Path):
    """Open an existing SQLite database without permitting writes or creation."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def _table_counts(connection):
    tables = _table_names(connection)
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in LOGICAL_TABLES
        if table in tables
    }


def _capture_window(connection):
    tables = _table_names(connection)
    timestamps = []
    for table in RETRIEVAL_TIME_TABLES:
        if table not in tables:
            continue
        columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if "retrieved_at" not in columns:
            continue
        minimum, maximum = connection.execute(
            f'SELECT MIN(retrieved_at), MAX(retrieved_at) FROM "{table}"'
        ).fetchone()
        if minimum:
            timestamps.append(minimum)
        if maximum:
            timestamps.append(maximum)
    return {
        "started_at": min(timestamps) if timestamps else None,
        "completed_at": max(timestamps) if timestamps else None,
    }


def _git_state(project_root: Path):
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.CalledProcessError):
        return os.getenv("SOURCE_REVISION"), None


def _initialize_snapshot_metadata(
    connection,
    *,
    snapshot_id: str,
    created_at: str,
    capture_window: dict,
    effective_date: str | None,
    source_revision: str | None,
    source_dirty: bool | None,
):
    if "snapshot_metadata" in _table_names(connection):
        raise SnapshotError("Source database is already a snapshot")
    if "ingestion_runs" in _table_names(connection):
        # Provider transport errors can contain transient signed URLs or credentials.
        connection.execute("UPDATE ingestion_runs SET error = NULL WHERE error IS NOT NULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshot_metadata (
            snapshot_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            effective_date TEXT,
            capture_started_at TEXT,
            capture_completed_at TEXT,
            source_revision TEXT,
            source_dirty INTEGER
        );

        CREATE TRIGGER IF NOT EXISTS snapshot_metadata_no_update
        BEFORE UPDATE ON snapshot_metadata
        BEGIN
            SELECT RAISE(ABORT, 'snapshot metadata is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS snapshot_metadata_no_delete
        BEFORE DELETE ON snapshot_metadata
        BEGIN
            SELECT RAISE(ABORT, 'snapshot metadata is immutable');
        END;
        """
    )
    connection.execute(
        """
        INSERT INTO snapshot_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            SNAPSHOT_SCHEMA_VERSION,
            created_at,
            effective_date,
            capture_window["started_at"],
            capture_window["completed_at"],
            source_revision,
            None if source_dirty is None else int(source_dirty),
        ),
    )
    connection.execute(f"PRAGMA user_version = {SNAPSHOT_SCHEMA_VERSION}")
    connection.commit()


def _validate_relative_path(value: str):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SnapshotError(f"Unsafe snapshot artifact path: {value}")
    return path


def _verify_database(path: Path, expected: dict | None = None):
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(f"Snapshot database is missing or unsafe: {path}")
    connection = open_readonly_database(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotError(f"SQLite integrity check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise SnapshotError("SQLite foreign key check failed")
        counts = _table_counts(connection)
        metadata = None
        if "snapshot_metadata" in _table_names(connection):
            rows = connection.execute(
                """
                SELECT snapshot_id, schema_version, created_at, effective_date,
                       capture_started_at, capture_completed_at, source_revision,
                       source_dirty
                FROM snapshot_metadata
                """
            ).fetchall()
            if len(rows) != 1:
                raise SnapshotError("Snapshot database must contain exactly one metadata row")
            if rows:
                row = rows[0]
                metadata = {
                    "snapshot_id": row[0],
                    "schema_version": row[1],
                    "created_at": row[2],
                    "effective_date": row[3],
                    "capture_started_at": row[4],
                    "capture_completed_at": row[5],
                    "source_revision": row[6],
                    "source_dirty": None if row[7] is None else bool(row[7]),
                }
    finally:
        connection.close()
    actual = {
        "path": DATABASE_NAME,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "integrity_check": "ok",
        "table_counts": counts,
    }
    if expected:
        for field in ("path", "bytes", "sha256", "table_counts"):
            if actual[field] != expected.get(field):
                raise SnapshotError(f"Snapshot database {field} does not match manifest")
    return actual, metadata


def create_snapshot(
    database: Path,
    output_root: Path,
    *,
    snapshot_id: str | None = None,
    artifacts: tuple[Path, ...] | list[Path] = (),
    notes: str | None = None,
    required_tables: tuple[str, ...] | list[str] = (),
    require_nonempty: tuple[str, ...] | list[str] = (),
    effective_date: str | None = None,
    coverage: dict | None = None,
):
    database = Path(database)
    output_root = Path(output_root)
    if not database.is_file() or database.is_symlink():
        raise SnapshotError(f"Source database does not exist or is unsafe: {database}")
    created_at = utc_now()
    if effective_date is not None:
        datetime.strptime(effective_date, "%Y-%m-%d")
    _validate_public_metadata(coverage or {})
    snapshot_id = snapshot_id or (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + f"-{uuid.uuid4().hex[:8]}"
    )
    _validate_snapshot_id(snapshot_id)
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / snapshot_id
    if destination.exists():
        raise SnapshotError(f"Snapshot already exists: {destination}")

    project_root = Path(__file__).resolve().parents[1]
    source_revision, source_dirty = _git_state(project_root)
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.partial-", dir=output_root))
    snapshot_database = staging / DATABASE_NAME
    try:
        source = open_readonly_database(database)
        target = sqlite3.connect(snapshot_database)
        try:
            source.backup(target)
            capture_window = _capture_window(target)
            _initialize_snapshot_metadata(
                target,
                snapshot_id=snapshot_id,
                created_at=created_at,
                capture_window=capture_window,
                effective_date=effective_date,
                source_revision=source_revision,
                source_dirty=source_dirty,
            )
            target.execute("PRAGMA journal_mode = DELETE")
            target.execute("VACUUM")
        finally:
            target.close()
            source.close()

        database_entry, metadata = _verify_database(snapshot_database)
        counts = database_entry["table_counts"]
        missing = sorted(set(required_tables) - set(counts))
        if missing:
            raise SnapshotError(f"Required tables are missing: {', '.join(missing)}")
        empty = sorted(table for table in require_nonempty if counts.get(table, 0) == 0)
        if empty:
            raise SnapshotError(f"Required tables are empty: {', '.join(empty)}")

        artifact_entries = []
        artifact_directory = staging / "artifacts"
        for artifact in artifacts:
            artifact = Path(artifact)
            if artifact.is_symlink() or not artifact.is_file():
                raise SnapshotError(f"Additional artifact is missing or unsafe: {artifact}")
            digest = sha256_file(artifact)
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact.name).strip("-")
            safe_name = safe_name or "artifact"
            relative = Path("artifacts") / f"{digest}-{safe_name}"
            destination_artifact = staging / relative
            artifact_directory.mkdir(exist_ok=True)
            if not destination_artifact.exists():
                shutil.copyfile(artifact, destination_artifact)
            media_type = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            artifact_entries.append(
                {
                    "name": artifact.name,
                    "path": relative.as_posix(),
                    "media_type": media_type,
                    "bytes": destination_artifact.stat().st_size,
                    "sha256": digest,
                }
            )

        manifest = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "created_at": created_at,
            "acquisition_window": {
                "started_at": metadata["capture_started_at"],
                "completed_at": metadata["capture_completed_at"],
            },
            "effective_date": effective_date,
            "producer": {
                "project": "gipuzkoa-weather-climate-askbot",
                "source_revision": source_revision,
                "source_dirty": source_dirty,
                "python": sys.version.split()[0],
            },
            "source_database": database.name,
            "database": database_entry,
            "artifacts": sorted(artifact_entries, key=lambda item: item["path"]),
            "coverage": coverage or {},
            "notes": notes,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        manifest_digest = sha256_file(manifest_path)
        (staging / MANIFEST_CHECKSUM_NAME).write_text(
            f"{manifest_digest}  {MANIFEST_NAME}\n", encoding="ascii"
        )
        verify_snapshot(staging)
        staging.rename(destination)
        return {
            "status": "created",
            "snapshot_id": snapshot_id,
            "path": str(destination),
            "manifest_sha256": manifest_digest,
            "database_sha256": database_entry["sha256"],
            "table_counts": counts,
            "artifacts": len(artifact_entries),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _load_manifest(snapshot: Path):
    manifest_path = snapshot / MANIFEST_NAME
    checksum_path = snapshot / MANIFEST_CHECKSUM_NAME
    if manifest_path.is_symlink() or checksum_path.is_symlink():
        raise SnapshotError("Snapshot manifest files must not be symlinks")
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise SnapshotError("Snapshot manifest or checksum is missing")
    checksum_parts = checksum_path.read_text(encoding="ascii").strip().split()
    if len(checksum_parts) != 2 or checksum_parts[1] != MANIFEST_NAME:
        raise SnapshotError("Snapshot manifest checksum file is invalid")
    actual_digest = sha256_file(manifest_path)
    if not re.fullmatch(r"[0-9a-f]{64}", checksum_parts[0]):
        raise SnapshotError("Snapshot manifest checksum is invalid")
    if actual_digest != checksum_parts[0]:
        raise SnapshotError("Snapshot manifest checksum does not match")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotError("Snapshot manifest is not valid UTF-8 JSON") from error
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError("Unsupported snapshot schema version")
    snapshot_id = manifest.get("snapshot_id", "")
    _validate_snapshot_id(snapshot_id)
    if manifest.get("database", {}).get("path") != DATABASE_NAME:
        raise SnapshotError("Snapshot database path is invalid")
    return manifest, actual_digest


def _validate_public_metadata(value, path="coverage"):
    sensitive = ("token", "secret", "password", "api_key", "private_key", "authorization")
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(term in normalized for term in sensitive):
                raise SnapshotError(f"Sensitive metadata key is not allowed: {path}.{key}")
            _validate_public_metadata(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_public_metadata(item, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise SnapshotError(f"Snapshot metadata is not JSON-safe: {path}")


def read_snapshot_metadata(database: Path):
    """Return the single metadata row required of a snapshot database."""
    _, metadata = _verify_database(Path(database))
    if metadata is None:
        raise SnapshotError("Snapshot mode requires a database with snapshot metadata")
    return metadata


def verify_snapshot(snapshot: Path):
    snapshot = Path(snapshot)
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise SnapshotError(f"Snapshot directory does not exist or is unsafe: {snapshot}")
    manifest, manifest_digest = _load_manifest(snapshot)
    declared_files = {
        MANIFEST_NAME,
        MANIFEST_CHECKSUM_NAME,
        DATABASE_NAME,
        *(item.get("path", "") for item in manifest.get("artifacts", [])),
    }
    actual_files = set()
    for path in snapshot.rglob("*"):
        if path.is_symlink():
            raise SnapshotError(f"Snapshot contains a symlink: {path.relative_to(snapshot)}")
        if path.is_file():
            actual_files.add(path.relative_to(snapshot).as_posix())
        elif path.is_dir() and path != snapshot / "artifacts":
            raise SnapshotError(
                f"Snapshot contains an undeclared directory: {path.relative_to(snapshot)}"
            )
    if actual_files != declared_files:
        unexpected = sorted(actual_files - declared_files)
        missing = sorted(declared_files - actual_files)
        raise SnapshotError(
            f"Snapshot file inventory does not match manifest; unexpected={unexpected}, missing={missing}"
        )
    database_entry, metadata = _verify_database(
        snapshot / DATABASE_NAME, manifest["database"]
    )
    if metadata is None or metadata["snapshot_id"] != manifest["snapshot_id"]:
        raise SnapshotError("Snapshot database metadata does not match manifest")
    if metadata["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError("Snapshot database metadata version is unsupported")
    for artifact in manifest.get("artifacts", []):
        relative = _validate_relative_path(artifact.get("path", ""))
        path = snapshot.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise SnapshotError(f"Snapshot artifact is missing or unsafe: {relative}")
        if path.stat().st_size != artifact.get("bytes"):
            raise SnapshotError(f"Snapshot artifact size does not match: {relative}")
        if sha256_file(path) != artifact.get("sha256"):
            raise SnapshotError(f"Snapshot artifact checksum does not match: {relative}")
    return {
        "status": "verified",
        "snapshot_id": manifest["snapshot_id"],
        "path": str(snapshot),
        "manifest_sha256": manifest_digest,
        "database_sha256": database_entry["sha256"],
        "table_counts": database_entry["table_counts"],
        "artifacts": len(manifest.get("artifacts", [])),
    }


def inspect_snapshot(snapshot: Path):
    verification = verify_snapshot(snapshot)
    manifest, _ = _load_manifest(Path(snapshot))
    return {**verification, "manifest": manifest}


def install_snapshot(snapshot: Path, database: Path, *, replace: bool = False):
    snapshot = Path(snapshot)
    database = Path(database)
    verification = verify_snapshot(snapshot)
    manifest, _ = _load_manifest(snapshot)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(f".{database.name}.install-{uuid.uuid4().hex}")
    try:
        shutil.copyfile(snapshot / DATABASE_NAME, temporary)
        _, metadata = _verify_database(temporary, manifest["database"])
        if metadata is None or metadata["snapshot_id"] != manifest["snapshot_id"]:
            raise SnapshotError("Copied snapshot metadata does not match manifest")
        lock = FileLock(f"{database}.lock", timeout=60)
        with lock:
            if database.exists() and not replace:
                raise SnapshotError(f"Destination database already exists: {database}")
            sidecars = [
                Path(f"{database}-wal"),
                Path(f"{database}-shm"),
                Path(f"{database}-journal"),
            ]
            if any(path.exists() for path in sidecars):
                raise SnapshotError(
                    "Destination has active SQLite WAL or journal files; stop writers and checkpoint it first"
                )
            if replace:
                os.replace(temporary, database)
            else:
                try:
                    os.link(temporary, database)
                except FileExistsError as error:
                    raise SnapshotError(
                        f"Destination database already exists: {database}"
                    ) from error
                temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "installed",
        "snapshot_id": verification["snapshot_id"],
        "snapshot": str(snapshot),
        "database": str(database),
        "database_sha256": sha256_file(database),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create, verify, inspect, or install immutable data snapshots."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--output-root", type=Path, default=Path("data/snapshots"))
    create.add_argument("--snapshot-id")
    create.add_argument("--artifact", action="append", type=Path, default=[])
    create.add_argument("--notes")
    create.add_argument("--effective-date", help="Snapshot reference date, YYYY-MM-DD")
    create.add_argument("--required-table", action="append", default=[])
    create.add_argument("--require-nonempty", action="append", default=[])

    for command in ("verify", "inspect"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--snapshot", type=Path, required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--snapshot", type=Path, required=True)
    install.add_argument("--database", type=Path, required=True)
    install.add_argument("--replace", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "create":
        result = create_snapshot(
            args.database,
            args.output_root,
            snapshot_id=args.snapshot_id,
            artifacts=args.artifact,
            notes=args.notes,
            required_tables=args.required_table,
            require_nonempty=args.require_nonempty,
            effective_date=args.effective_date,
        )
    elif args.command == "verify":
        result = verify_snapshot(args.snapshot)
    elif args.command == "inspect":
        result = inspect_snapshot(args.snapshot)
    else:
        result = install_snapshot(args.snapshot, args.database, replace=args.replace)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
