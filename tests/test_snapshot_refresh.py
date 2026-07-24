import json
from pathlib import Path

import pytest

from app.snapshot import verify_snapshot
from app.snapshot_refresh import SnapshotRefreshConfig, produce_all_source_snapshot


def config(tmp_path):
    questions = tmp_path / "questions.json"
    questions.write_text("[]\n", encoding="utf-8")
    return SnapshotRefreshConfig(
        snapshot_id="all-source-test",
        output_root=tmp_path / "snapshots",
        as_of="2026-07-24",
        aemet_station="1012P",
        aemet_start="2024-01-01",
        aemet_end="2024-01-02",
        euskalmet_region="basque_country",
        euskalmet_zone="donostialdea",
        euskalmet_location="donostia",
        forecast_horizon_days=2,
        alert_zone="GIPUZKOA_COAST",
        era5_year=2024,
        era5_month=1,
        questions=questions,
    )


def fake_capture(connection, climate_root, capture_config, **kwargs):
    retrieved = "2026-07-24T10:00:00+00:00"
    connection.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "official-source",
            "Euskalmet",
            "https://example.test/source",
            "Official source",
            "es",
            "text/html",
            "guidance",
            None,
            retrieved,
            "content-hash",
        ),
    )
    connection.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "document",
            "official-source",
            "Official source",
            "Official evidence",
            "content-hash",
            None,
            retrieved,
            1,
        ),
    )
    connection.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
        ("chunk", "document", 0, "Official evidence", 17),
    )
    connection.execute(
        "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?)",
        ("chunk", "Official evidence", "Official source", "Euskalmet", "es"),
    )
    connection.execute(
        "INSERT INTO weather_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "euskalmet",
            "station",
            "Station",
            "Donostia",
            "Gipuzkoa",
            43.3,
            -2.0,
            1,
            "https://example.test/stations",
            "{}",
            retrieved,
        ),
    )
    connection.execute(
        "INSERT INTO weather_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "euskalmet",
            "donostia",
            "2026-07-25",
            "Donostia",
            15,
            20,
            "Nubes",
            "Hodeiak",
            "https://example.test/forecast",
            retrieved,
        ),
    )
    connection.execute(
        """
        INSERT INTO weather_api_snapshots
            (provider, snapshot_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-location-forecast",
            "forecast-snapshot",
            "{}",
            "https://example.test/authenticated",
            retrieved,
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO aemet_daily_observations VALUES (?, ?, ?, ?)",
        ("1012P", "2024-01-01", "{}", retrieved),
    )
    connection.execute(
        """
        INSERT INTO hazard_alerts
            (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-alerts",
            "alert-snapshot",
            "{}",
            "https://example.test/alerts",
            retrieved,
            "{}",
        ),
    )
    connection.commit()
    climate_root.mkdir(parents=True)
    data = climate_root / "era5.nc"
    manifest = climate_root / "era5.json"
    data.write_bytes(b"netcdf")
    manifest.write_text(json.dumps({"sha256": "fixture"}), encoding="utf-8")
    return [data, manifest], {"fake": "complete"}


def test_all_source_producer_publishes_only_verified_snapshot(tmp_path):
    receipt = produce_all_source_snapshot(
        config(tmp_path), capture_function=fake_capture
    )

    verification = verify_snapshot(Path(receipt["path"]))

    assert receipt["status"] == "created"
    assert receipt["sources"] == {"fake": "complete"}
    assert verification["artifacts"] == 2
    assert verification["table_counts"]["aemet_daily_observations"] == 1


def test_all_source_producer_does_not_publish_failed_capture(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        produce_all_source_snapshot(config(tmp_path), capture_function=fail)

    assert list((tmp_path / "snapshots").iterdir()) == []


def test_all_source_configuration_requires_explicit_valid_bounds(tmp_path):
    invalid = config(tmp_path)
    invalid = SnapshotRefreshConfig(
        **{**invalid.__dict__, "aemet_start": "2024-02-01", "aemet_end": "2024-01-01"}
    )

    with pytest.raises(ValueError, match="start date"):
        invalid.validate()
