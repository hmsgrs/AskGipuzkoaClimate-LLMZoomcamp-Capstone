import json
import sqlite3
from pathlib import Path

import pytest

from app.ingest import get_database
from app.snapshot import (
    SnapshotError,
    create_snapshot,
    install_snapshot,
    verify_snapshot,
)
from app.weather_api import CachedWeatherRepository


def populated_database(path: Path):
    connection = get_database(path)
    connection.execute(
        """
        INSERT INTO weather_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet",
            "station-one",
            "Station One",
            "Donostia",
            "Gipuzkoa",
            43.3,
            -2.0,
            1,
            "https://example.test/stations",
            "{}",
            "2026-07-24T09:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO weather_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet",
            "donostia",
            "2026-07-25",
            "Donostia",
            15,
            22,
            "Nubes",
            "Hodeiak",
            "https://example.test/forecast",
            "2026-07-24T09:05:00+00:00",
        ),
    )
    connection.commit()
    return connection


def test_snapshot_create_verify_and_install(tmp_path):
    source = tmp_path / "working.sqlite"
    connection = populated_database(source)
    connection.close()
    climate = tmp_path / "era5.nc"
    climate.write_bytes(b"netcdf-fixture")

    receipt = create_snapshot(
        source,
        tmp_path / "snapshots",
        snapshot_id="test-snapshot",
        artifacts=[climate],
        required_tables=["weather_stations", "weather_forecasts"],
        require_nonempty=["weather_stations", "weather_forecasts"],
        notes="Test acquisition window",
    )
    snapshot = Path(receipt["path"])
    verification = verify_snapshot(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))

    assert verification["status"] == "verified"
    assert verification["table_counts"]["weather_stations"] == 1
    assert manifest["acquisition_window"] == {
        "started_at": "2026-07-24T09:00:00+00:00",
        "completed_at": "2026-07-24T09:05:00+00:00",
    }
    assert manifest["artifacts"][0]["name"] == "era5.nc"
    assert Path(manifest["artifacts"][0]["path"]).name.endswith("-era5.nc")

    installed = tmp_path / "installed.sqlite"
    install_receipt = install_snapshot(snapshot, installed)
    with sqlite3.connect(installed) as restored:
        assert restored.execute("SELECT COUNT(*) FROM weather_stations").fetchone()[0] == 1
        metadata = restored.execute(
            "SELECT snapshot_id, schema_version FROM snapshot_metadata"
        ).fetchone()
    assert metadata == ("test-snapshot", 1)
    assert install_receipt["snapshot_id"] == "test-snapshot"


def test_snapshot_online_backup_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "working.sqlite"
    connection = populated_database(source)
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        """
        INSERT INTO weather_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "aemet",
            "station-two",
            "Station Two",
            "Hondarribia",
            "Gipuzkoa",
            43.36,
            -1.79,
            1,
            "https://example.test/aemet",
            "{}",
            "2026-07-24T09:10:00+00:00",
        ),
    )
    connection.commit()

    receipt = create_snapshot(
        source, tmp_path / "snapshots", snapshot_id="wal-safe"
    )
    with sqlite3.connect(Path(receipt["path"]) / "snapshot.sqlite") as snapshot:
        count = snapshot.execute("SELECT COUNT(*) FROM weather_stations").fetchone()[0]
    connection.close()

    assert count == 2


def test_snapshot_is_immutable_and_detects_tampering(tmp_path):
    source = tmp_path / "working.sqlite"
    connection = populated_database(source)
    connection.close()
    extra = tmp_path / "artifact.json"
    extra.write_text('{"safe": true}\n', encoding="utf-8")
    receipt = create_snapshot(
        source,
        tmp_path / "snapshots",
        snapshot_id="immutable",
        artifacts=[extra],
    )
    snapshot = Path(receipt["path"])

    with pytest.raises(SnapshotError, match="already exists"):
        create_snapshot(
            source, tmp_path / "snapshots", snapshot_id="immutable"
        )

    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    artifact = snapshot / manifest["artifacts"][0]["path"]
    artifact.write_text("tampered", encoding="utf-8")
    with pytest.raises(SnapshotError, match="size does not match|checksum does not match"):
        verify_snapshot(snapshot)


def test_snapshot_rejects_missing_coverage_and_active_wal_install(tmp_path):
    source = tmp_path / "working.sqlite"
    connection = populated_database(source)
    connection.close()
    with pytest.raises(SnapshotError, match="Required tables are empty"):
        create_snapshot(
            source,
            tmp_path / "snapshots",
            snapshot_id="incomplete",
            require_nonempty=["aemet_daily_observations"],
        )

    receipt = create_snapshot(
        source, tmp_path / "snapshots", snapshot_id="install-source"
    )
    destination = tmp_path / "destination.sqlite"
    destination.write_bytes(b"old")
    Path(f"{destination}-wal").write_bytes(b"active")
    with pytest.raises(SnapshotError, match="active SQLite WAL"):
        install_snapshot(Path(receipt["path"]), destination, replace=True)


def test_snapshot_weather_uses_acquisition_date_and_marks_archived_alerts(tmp_path):
    source = tmp_path / "working.sqlite"
    connection = populated_database(source)
    connection.execute(
        """
        INSERT INTO hazard_alerts
            (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-homepage",
            "archived-alert",
            '{"warning": "heavy rain"}',
            "https://example.test/warnings",
            "2026-07-24T09:04:00+00:00",
            "{}",
        ),
    )
    connection.commit()
    connection.close()
    receipt = create_snapshot(
        source, tmp_path / "snapshots", snapshot_id="weather-history"
    )
    repository = CachedWeatherRepository(
        Path(receipt["path"]) / "snapshot.sqlite", snapshot_mode=True
    )

    forecast = repository.search("What was tomorrow's weather in Donostia?")
    warnings = repository.search("What warnings were captured?")

    assert forecast[0]["publication_date"] == "2026-07-25"
    assert forecast[0]["source_type"] == "snapshot_weather_forecast"
    assert warnings[0]["source_type"] == "snapshot_weather_alert"
    assert warnings[0]["stale"] is True
    assert "Historical warning snapshot" in warnings[0]["text"]
