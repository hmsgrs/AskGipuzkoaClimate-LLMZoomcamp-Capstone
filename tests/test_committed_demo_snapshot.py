import json
import sqlite3
from pathlib import Path

from app.demo_snapshot import EXPECTED_COUNTS
from app.portable_embeddings import validate_export
from app.snapshot import verify_snapshot
from app.weather_api import CachedWeatherRepository


SNAPSHOT = (
    Path(__file__).parents[1]
    / "data"
    / "snapshots"
    / "gipuzkoa-demo-2026-07-27"
)


def test_committed_demo_snapshot_is_complete_and_verifiable():
    verification = verify_snapshot(SNAPSHOT)
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))

    assert verification["snapshot_id"] == "gipuzkoa-demo-2026-07-27"
    assert manifest["effective_date"] == "2026-07-27"
    assert manifest["producer"]["source_dirty"] is False
    assert manifest["coverage"]["scope"] == "historical_reviewer_demo"
    assert manifest["coverage"]["table_counts"] == EXPECTED_COUNTS
    assert manifest["database"]["table_counts"] == {
        **EXPECTED_COUNTS,
        "snapshot_metadata": 1,
    }
    assert manifest["coverage"]["excluded"] == [
        "AEMET daily observations",
        "current conditions",
    ]


def test_committed_embeddings_match_every_active_chunk():
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    artifact = SNAPSHOT / manifest["artifacts"][0]["path"]

    result = validate_export(artifact, SNAPSHOT / "snapshot.sqlite")

    assert result["embedding_model"] == "text-embedding-3-small"
    assert result["dimensions"] == 1536
    assert result["vector_count"] == 161


def test_committed_authenticated_forecasts_have_complete_request_matrix():
    connection = sqlite3.connect(
        f"{(SNAPSHOT / 'snapshot.sqlite').resolve().as_uri()}?mode=ro", uri=True
    )
    requests = {
        tuple(json.loads(row)[key] for key in ("region", "zone", "location", "issued_date", "target_date"))
        for (row,) in connection.execute(
            "SELECT request_json FROM weather_api_snapshots"
        ).fetchall()
    }
    connection.close()

    locations = {
        "donostia": "donostialdea",
        "irun": "coast_zone",
        "hondarribia": "coast_zone",
        "hernani": "cantabrian_valleys",
        "lasarte": "cantabrian_valleys",
        "zarautz": "coast_zone",
        "tolosa": "cantabrian_valleys",
        "eibar": "cantabrian_valleys",
        "arrasate": "cantabrian_valleys",
        "beasain": "cantabrian_valleys",
    }
    assert requests == {
        ("basque_country", zone, location, "2026-07-27", target)
        for location, zone in locations.items()
        for target in ("2026-07-27", "2026-07-28", "2026-07-29")
    }


def test_committed_alerts_cover_coast_and_interior():
    connection = sqlite3.connect(
        f"{(SNAPSHOT / 'snapshot.sqlite').resolve().as_uri()}?mode=ro", uri=True
    )
    rows = connection.execute(
        "SELECT request_json, payload_json FROM hazard_alerts"
    ).fetchall()
    connection.close()

    assert {json.loads(request)["zone"] for request, _ in rows} == {
        "GIPUZKOA_COAST",
        "GIPUZKOA_INTERIOR",
    }
    assert all(json.loads(request)["issued_date"] == "2026-07-27" for request, _ in rows)
    assert all(len(json.loads(payload)) == 3 for _, payload in rows)


def test_committed_weather_retrieval_isolates_locations_dates_and_alert_areas():
    repository = CachedWeatherRepository(
        SNAPSHOT / "snapshot.sqlite", snapshot_mode=True
    )

    hernani = repository.search("¿Qué previsión muestra hoy para Hernani?")
    lasarte = repository.search("Forecast tomorrow in Lasarte-Oria")
    irun = repository.search("Tiempo pasado mañana en Irun")
    coast = repository.search("Avisos capturados para la costa de Gipuzkoa")
    interior = repository.search("Warnings captured for inland Gipuzkoa")

    assert len(hernani) == len(lasarte) == len(irun) == 1
    assert "Hernani on 2026-07-27" in hernani[0]["title"]
    assert "Lasarte-Oria on 2026-07-28" in lasarte[0]["title"]
    assert "Irun on 2026-07-29" in irun[0]["title"]
    assert len(coast) == len(interior) == 1
    assert "Gipuzkoa coast" in coast[0]["title"]
    assert "Gipuzkoa interior" in interior[0]["title"]
    assert all(row["stale"] is True for row in (*hernani, *lasarte, *irun, *coast, *interior))
