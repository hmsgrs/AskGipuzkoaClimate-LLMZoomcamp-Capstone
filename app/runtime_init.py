"""Initialize the reviewer PostgreSQL runtime and seed committed embeddings."""

import json
import os
from pathlib import Path

from psycopg import sql

from app.db_init import (
    connect,
    embedding_dimensions,
    embedding_model,
    initialize_application_schema,
    initialize_pgvector,
)
from app.portable_embeddings import import_embeddings, validate_export
from app.snapshot import MANIFEST_NAME, read_snapshot_metadata, verify_snapshot


def _required_env(name, default=None):
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"Set {name} before runtime initialization")
    return value


def _ensure_role(connection, role, password):
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    identifier = sql.Identifier(role)
    if exists:
        connection.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD %s").format(identifier),
            (password,),
        )
    else:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(identifier),
            (password,),
        )


def _embedding_artifact(snapshot):
    manifest = json.loads((snapshot / MANIFEST_NAME).read_text(encoding="utf-8"))
    matches = [
        snapshot / artifact["path"]
        for artifact in manifest.get("artifacts", [])
        if artifact.get("name") == "embeddings.sqlite"
    ]
    if len(matches) != 1:
        raise RuntimeError("Snapshot must declare exactly one embeddings.sqlite artifact")
    return matches[0]


def provision_roles(connection, app_role, app_password, grafana_role, grafana_password):
    database = connection.execute("SELECT current_database()").fetchone()[0]
    _ensure_role(connection, app_role, app_password)
    _ensure_role(connection, grafana_role, grafana_password)
    database_id = sql.Identifier(database)
    app_id = sql.Identifier(app_role)
    grafana_id = sql.Identifier(grafana_role)
    connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database_id))
    connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            database_id, app_id, grafana_id
        )
    )
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(app_id, grafana_id)
    )
    connection.execute(
        sql.SQL("GRANT SELECT ON chunk_embeddings TO {}").format(app_id)
    )
    connection.execute(
        sql.SQL("GRANT SELECT, INSERT ON conversations TO {}").format(app_id)
    )
    connection.execute(
        sql.SQL("GRANT SELECT, INSERT, UPDATE ON feedback TO {}").format(app_id)
    )
    connection.execute(
        sql.SQL(
            "GRANT USAGE, SELECT ON SEQUENCE conversations_id_seq, feedback_id_seq TO {}"
        ).format(app_id)
    )
    connection.execute(
        sql.SQL("GRANT SELECT ON conversations, feedback TO {}").format(grafana_id)
    )
    connection.commit()


def initialize_runtime(
    snapshot: Path,
    connection,
    *,
    app_role: str,
    app_password: str,
    grafana_role: str,
    grafana_password: str,
):
    snapshot = Path(snapshot)
    verification = verify_snapshot(snapshot)
    metadata = read_snapshot_metadata(snapshot / "snapshot.sqlite")
    if metadata["source_dirty"] is True:
        raise RuntimeError("Reviewer snapshot was produced from a dirty source revision")
    artifact = _embedding_artifact(snapshot)
    export = validate_export(artifact, snapshot / "snapshot.sqlite")
    if export["embedding_model"] != embedding_model():
        raise RuntimeError("Embedding artifact model does not match runtime configuration")
    if export["dimensions"] != embedding_dimensions():
        raise RuntimeError("Embedding artifact dimensions do not match runtime configuration")
    initialize_pgvector(connection, export["dimensions"])
    initialize_application_schema(connection)
    imported = import_embeddings(artifact, snapshot / "snapshot.sqlite", connection)
    provision_roles(
        connection,
        app_role,
        app_password,
        grafana_role,
        grafana_password,
    )
    return {
        "snapshot_id": verification["snapshot_id"],
        "database_sha256": verification["database_sha256"],
        "vectors": imported["imported"],
        "embedding_model": imported["embedding_model"],
    }


def main():
    snapshot = Path(_required_env("SNAPSHOT_PATH"))
    with connect(_required_env("BOOTSTRAP_DATABASE_URL")) as connection:
        result = initialize_runtime(
            snapshot,
            connection,
            app_role=_required_env("APP_DATABASE_USER", "askgipuzkoa_app"),
            app_password=_required_env("APP_DATABASE_PASSWORD"),
            grafana_role=_required_env("GRAFANA_DATABASE_USER", "askgipuzkoa_grafana"),
            grafana_password=_required_env("GRAFANA_DATABASE_PASSWORD"),
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
