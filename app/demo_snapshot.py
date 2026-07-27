"""Build the committed, scoped historical reviewer snapshot."""

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.portable_embeddings import export_embeddings
from app.snapshot import create_snapshot, open_readonly_database


DEMO_SNAPSHOT_ID = "gipuzkoa-demo-2026-07-22"
DEMO_EFFECTIVE_DATE = "2026-07-22"
EXPECTED_COUNTS = {
    "sources": 9,
    "documents": 9,
    "chunks": 161,
    "evaluation_questions": 6,
    "weather_stations": 45,
    "weather_forecasts": 18,
    "weather_api_snapshots": 1,
    "aemet_daily_observations": 0,
    "hazard_alerts": 0,
    "ingestion_runs": 0,
}


def _table_counts(database):
    connection = open_readonly_database(database)
    try:
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in EXPECTED_COUNTS
        }
    finally:
        connection.close()


def curate_database(source: Path, destination: Path):
    source_connection = open_readonly_database(source)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        rows = target_connection.execute(
            """
            SELECT snapshot_id, source_url, request_json
            FROM weather_api_snapshots
            WHERE provider = 'euskalmet-location-forecast'
            """
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("Demo source must contain exactly one location forecast")
        snapshot_id, source_url, request_json = rows[0]
        if json.loads(request_json or "{}") == {}:
            expected_path = (
                "/regions/basque_country/zones/donostialdea/locations/donostia/"
                "forecast/at/2026/07/21/for/20260721"
            )
            if expected_path not in source_url:
                raise ValueError("Location forecast URL does not match demo provenance")
            target_connection.execute(
                "UPDATE weather_api_snapshots SET request_json = ? WHERE snapshot_id = ?",
                (
                    json.dumps(
                        {
                            "region": "basque_country",
                            "zone": "donostialdea",
                            "location": "donostia",
                            "issued_date": "2026-07-21",
                            "target_date": "2026-07-21",
                        },
                        sort_keys=True,
                    ),
                    snapshot_id,
                ),
            )
        target_connection.execute(
            "UPDATE ingestion_runs SET error = NULL WHERE error IS NOT NULL"
        )
        target_connection.commit()
        target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        target_connection.close()
        source_connection.close()
    counts = _table_counts(destination)
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected demo coverage: {counts}")
    return counts


def build_demo_snapshot(
    source: Path,
    output_root: Path,
    *,
    snapshot_id: str = DEMO_SNAPSHOT_ID,
    embedding_client=None,
):
    temporary_root = Path(tempfile.mkdtemp(prefix="askgipuzkoa-demo-"))
    try:
        curated = temporary_root / "curated.sqlite"
        embeddings = temporary_root / "embeddings.sqlite"
        counts = curate_database(Path(source), curated)
        export_embeddings(
            curated,
            embeddings,
            embedding_client=embedding_client,
        )
        return create_snapshot(
            curated,
            Path(output_root),
            snapshot_id=snapshot_id,
            artifacts=[embeddings],
            notes=(
                "Scoped historical reviewer demo. AEMET daily observations and "
                "hazard alerts are not included. Do not interpret it as current weather."
            ),
            required_tables=list(EXPECTED_COUNTS),
            require_nonempty=[
                table for table, count in EXPECTED_COUNTS.items() if count > 0
            ],
            effective_date=DEMO_EFFECTIVE_DATE,
            coverage={
                "scope": "historical_reviewer_demo",
                "table_counts": counts,
                "included": [
                    "official knowledge corpus",
                    "retrieval evaluation questions",
                    "station metadata",
                    "public city forecasts",
                    "one authenticated Donostia forecast",
                ],
                "excluded": [
                    "AEMET daily observations",
                    "hazard alerts",
                    "current conditions",
                ],
            },
        )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", default=DEMO_SNAPSHOT_ID)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = build_demo_snapshot(
        args.source,
        args.output_root,
        snapshot_id=args.snapshot_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
