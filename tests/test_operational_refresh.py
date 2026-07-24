from datetime import date, timedelta
from pathlib import Path

import pytest
import requests
import yaml

from app.aemet import AemetClient
from app.ingest import (
    date_chunks,
    get_database,
    refresh_aemet_daily,
    refresh_euskalmet_alerts,
    refresh_euskalmet_forecasts,
    save_alert_snapshot,
    save_weather_snapshot,
)


class FakeEuskalmetClient:
    def __init__(self):
        self.alert_calls = []
        self.forecast_calls = []

    def alert_forecast(self, zone, issued_date, target_date=None):
        self.alert_calls.append((zone, issued_date, target_date))
        return []

    def location_forecast(self, region, zone, location, issued_date, target_date):
        self.forecast_calls.append((region, zone, location, issued_date, target_date))
        return {"target": target_date.isoformat()}


class FakeAemetClient:
    def __init__(self):
        self.calls = []

    def daily_observations(self, station, start, end):
        self.calls.append((station, start, end))
        current = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        rows = []
        while current <= end_date:
            rows.append({"indicativo": station, "fecha": current.isoformat()})
            current += timedelta(days=1)
        return rows


def test_database_enables_wal_busy_timeout_and_run_storage(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 60000
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'ingestion_runs'"
    ).fetchone()[0] == "ingestion_runs"


def test_request_scope_prevents_identical_payload_collisions(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    payload = {"status": "same"}

    first = save_weather_snapshot(
        connection, "provider", payload, "https://example.test/one", {"location": "one"}
    )
    second = save_weather_snapshot(
        connection, "provider", payload, "https://example.test/two", {"location": "two"}
    )
    save_alert_snapshot(
        connection, "alerts", [], "https://example.test/coast", {"zone": "coast"}
    )
    save_alert_snapshot(
        connection, "alerts", [], "https://example.test/interior", {"zone": "interior"}
    )

    assert first != second
    assert connection.execute("SELECT COUNT(*) FROM weather_api_snapshots").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM hazard_alerts").fetchone()[0] == 2


def test_refreshes_validated_euskalmet_units_and_records_receipts(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    client = FakeEuskalmetClient()

    alert = refresh_euskalmet_alerts(
        connection, "GIPUZKOA_COAST", "2026-07-22", client
    )
    forecast = refresh_euskalmet_forecasts(
        connection,
        "basque_country",
        "donostialdea",
        "donostia",
        3,
        "2026-07-22",
        client,
    )

    assert alert["status"] == "ok"
    assert forecast["requested"] == forecast["succeeded"] == 3
    assert len(client.alert_calls) == 1
    assert [call[-1] for call in client.forecast_calls] == [
        date(2026, 7, 22),
        date(2026, 7, 23),
        date(2026, 7, 24),
    ]
    assert connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 2


def test_aemet_incremental_refresh_chunks_and_repairs_dates(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    client = FakeAemetClient()

    result = refresh_aemet_daily(
        connection,
        "1012P",
        as_of="2026-07-10",
        lag_days=2,
        lookback_days=7,
        chunk_days=3,
        initial_start="2026-07-01",
        client=client,
    )

    assert result["requested"] == 3
    assert result["max_observed_date"] == "2026-07-08"
    assert client.calls == [
        ("1012P", "2026-07-01", "2026-07-03"),
        ("1012P", "2026-07-04", "2026-07-06"),
        ("1012P", "2026-07-07", "2026-07-08"),
    ]

    client.calls.clear()
    refresh_aemet_daily(
        connection,
        "1012P",
        as_of="2026-07-12",
        lag_days=2,
        lookback_days=7,
        chunk_days=31,
        initial_start="2026-07-01",
        client=client,
    )
    assert client.calls == [("1012P", "2026-07-04", "2026-07-10")]
    assert connection.execute(
        "SELECT COUNT(*) FROM aemet_daily_observations WHERE station_id = '1012P'"
    ).fetchone()[0] == 10


def test_date_chunks_reject_invalid_sizes():
    assert date_chunks(date(2026, 1, 1), date(2026, 1, 5), 2) == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 4)),
        (date(2026, 1, 5), date(2026, 1, 5)),
    ]
    with pytest.raises(ValueError, match="chunk_days must be positive"):
        date_chunks(date(2026, 1, 1), date(2026, 1, 5), 0)


def test_aemet_two_stage_success_and_date_validation(tmp_path: Path):
    class Response:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.responses = [
                Response({"estado": 200, "datos": "https://example.test/data"}),
                Response([{"indicativo": "1012P", "fecha": "2026-01-01"}]),
            ]

        def get(self, *args, **kwargs):
            return self.responses.pop(0)

    key = tmp_path / "api.pem"
    key.write_text("test-key", encoding="utf-8")
    client = AemetClient(key, Session())

    assert client.daily_observations("1012P", "2026-01-01", "2026-01-01")[0][
        "indicativo"
    ] == "1012P"
    with pytest.raises(ValueError, match="start date"):
        client.daily_observations("1012P", "2026-01-02", "2026-01-01")


def test_aemet_transport_errors_do_not_disclose_the_api_key(tmp_path: Path):
    secret = "sensitive-aemet-token"

    class Session:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError(f"failed URL ?api_key={secret}")

    key = tmp_path / "api.pem"
    key.write_text(secret, encoding="utf-8")

    with pytest.raises(RuntimeError, match="transport error") as error:
        AemetClient(key, Session()).station_inventory()
    assert secret not in str(error.value)


def test_invalid_aemet_backfill_does_not_leave_a_running_receipt(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")

    with pytest.raises(ValueError, match="start date"):
        refresh_aemet_daily(
            connection,
            "1012P",
            start_date="2026-01-02",
            end_date="2026-01-01",
            client=FakeAemetClient(),
        )

    assert connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 0


def test_empty_aemet_backfill_boundary_is_rejected(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")

    with pytest.raises(ValueError, match="non-empty start and end"):
        refresh_aemet_daily(
            connection,
            "1012P",
            start_date="",
            end_date="2026-01-01",
            client=FakeAemetClient(),
        )
    assert connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 0


def test_invalid_euskalmet_operation_date_does_not_leave_a_receipt(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")

    with pytest.raises(ValueError):
        refresh_euskalmet_alerts(
            connection, "GIPUZKOA_COAST", "not-a-date", FakeEuskalmetClient()
        )
    assert connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 0


def test_all_kestra_flows_define_runtime_safety_properties():
    flow_directory = Path(__file__).parents[1] / "kestra"
    enabled_secret_flows = {
        "ingest_era5_land_monthly",
        "ingest_euskalmet_authenticated_alerts",
        "ingest_euskalmet_authenticated_forecasts",
        "ingest_official_documents",
    }
    disabled_secret_flows = {
        "ingest_aemet_daily_incremental",
        "refresh_aemet_station_catalogue",
    }
    for path in flow_directory.glob("*.yaml"):
        flow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert flow["concurrency"] == {"limit": 1, "behavior": "QUEUE"}
        for trigger in flow.get("triggers", []):
            assert trigger["timezone"] == "Europe/Madrid"
        for task in flow["tasks"]:
            assert task["containerImage"] == "gipuzkoa-askbot-ingestion:0.2.0"
            assert task["taskRunner"]["type"].endswith("runner.docker.Docker")
            assert "askgipuzkoa-ingestion-data:/data" in task["taskRunner"]["volumes"]
            assert "timeout" in task
            commands = task["commands"]
            if isinstance(commands, str):
                commands = [commands]
            assert all(
                "PYTHONPATH=/app /app/.venv/bin/python" in command
                for command in commands
            )
        text = path.read_text(encoding="utf-8")
        if flow["id"] in enabled_secret_flows:
            assert flow.get("disabled") is False
        if flow["id"] in disabled_secret_flows:
            assert flow.get("disabled") is True
        assert "PRIVATE KEY" not in text
        assert "api_key=" not in text.casefold()

    compose = (Path(__file__).parents[1] / "compose.yaml").read_text(encoding="utf-8")
    assert "${KESTRA_PASSWORD:?Set KESTRA_PASSWORD in .env}" in compose
    assert "KestraLocal1" not in compose

    era5_flow = (flow_directory / "ingest_era5_land_monthly.yaml").read_text(
        encoding="utf-8"
    )
    assert '--as-of "{{ execution.startDate' in era5_flow
