import json
import sqlite3
from pathlib import Path

from app.demo_snapshot import EXPECTED_COUNTS
from app.portable_embeddings import validate_export
from app.snapshot import verify_snapshot


SNAPSHOT = (
    Path(__file__).parents[1]
    / "data"
    / "snapshots"
    / "gipuzkoa-demo-2026-07-22"
)


def test_committed_demo_snapshot_is_complete_and_verifiable():
    verification = verify_snapshot(SNAPSHOT)
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))

    assert verification["snapshot_id"] == "gipuzkoa-demo-2026-07-22"
    assert manifest["effective_date"] == "2026-07-22"
    assert manifest["producer"]["source_dirty"] is False
    assert manifest["coverage"]["scope"] == "historical_reviewer_demo"
    assert manifest["coverage"]["table_counts"] == EXPECTED_COUNTS
    assert manifest["database"]["table_counts"] == {
        **EXPECTED_COUNTS,
        "snapshot_metadata": 1,
    }
    assert manifest["coverage"]["excluded"] == [
        "AEMET daily observations",
        "hazard alerts",
        "current conditions",
    ]


def test_committed_embeddings_match_every_active_chunk():
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    artifact = SNAPSHOT / manifest["artifacts"][0]["path"]

    result = validate_export(artifact, SNAPSHOT / "snapshot.sqlite")

    assert result["embedding_model"] == "text-embedding-3-small"
    assert result["dimensions"] == 1536
    assert result["vector_count"] == 161


def test_committed_authenticated_forecast_has_auditable_request_metadata():
    connection = sqlite3.connect(
        f"{(SNAPSHOT / 'snapshot.sqlite').resolve().as_uri()}?mode=ro", uri=True
    )
    request_json = connection.execute(
        "SELECT request_json FROM weather_api_snapshots"
    ).fetchone()[0]
    connection.close()

    assert json.loads(request_json) == {
        "issued_date": "2026-07-21",
        "location": "donostia",
        "region": "basque_country",
        "target_date": "2026-07-21",
        "zone": "donostialdea",
    }
