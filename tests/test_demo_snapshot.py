import json
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.demo_snapshot import EXPANDED_EXPECTED_COUNTS, curate_database
from app.euskalmet_scope import GIPUZKOA_ALERT_AREAS, REPRESENTATIVE_LOCATIONS


OLD_SNAPSHOT = (
    Path(__file__).parents[1]
    / "data"
    / "snapshots"
    / "gipuzkoa-demo-2026-07-22"
    / "snapshot.sqlite"
)


def test_representative_demo_curation_keeps_exact_weather_matrix(tmp_path):
    source = tmp_path / "source.sqlite"
    curated = tmp_path / "curated.sqlite"
    shutil.copyfile(OLD_SNAPSHOT, source)
    connection = sqlite3.connect(source)
    effective = date(2026, 7, 27)
    retrieved_at = "2026-07-27T12:00:00+00:00"
    connection.execute("DELETE FROM weather_forecasts")
    for target_offset in range(3):
        target = effective + timedelta(days=target_offset)
        for index, name in enumerate(
            ("Bilbao", "Donostia", "Laguardia", "Mondragon", "Pamplona", "Vitoria")
        ):
            connection.execute(
                "INSERT INTO weather_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "euskalmet",
                    str(index),
                    target.strftime("%d/%m/%Y"),
                    name,
                    10,
                    20,
                    "Nubes",
                    "Hodeiak",
                    "https://example.test/public",
                    retrieved_at,
                ),
            )
    for location in REPRESENTATIVE_LOCATIONS:
        for target_offset in range(3):
            target = effective + timedelta(days=target_offset)
            request = {
                "region": location.region,
                "zone": location.zone,
                "location": location.location,
                "issued_date": effective.isoformat(),
                "target_date": target.isoformat(),
            }
            connection.execute(
                """
                INSERT INTO weather_api_snapshots
                    (provider, snapshot_id, payload_json, source_url, retrieved_at, request_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "euskalmet-location-forecast",
                    f"{location.location}-{target_offset}",
                    '{"forecastText":{"SPANISH":"Nubes"}}',
                    "https://example.test/authenticated",
                    retrieved_at,
                    json.dumps(request),
                ),
            )
    for area in GIPUZKOA_ALERT_AREAS:
        connection.execute(
            """
            INSERT INTO hazard_alerts
                (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "euskalmet-alerts",
                area.zone,
                "[]",
                "https://example.test/alerts",
                retrieved_at,
                json.dumps(
                    {"zone": area.zone, "issued_date": effective.isoformat()}
                ),
            ),
        )
    connection.commit()
    connection.close()

    counts, acquisition = curate_database(
        source,
        curated,
        effective_date=effective.isoformat(),
        representative_weather=True,
    )

    assert counts == EXPANDED_EXPECTED_COUNTS
    assert len(acquisition["locations"]) == 10
    assert acquisition["target_dates"] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
    ]
    connection = sqlite3.connect(curated)
    assert connection.execute(
        "SELECT COUNT(DISTINCT request_json) FROM weather_api_snapshots"
    ).fetchone()[0] == 30
    assert connection.execute(
        "SELECT COUNT(DISTINCT request_json) FROM hazard_alerts"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'snapshot_metadata'"
    ).fetchone()[0] == 0
    connection.close()
