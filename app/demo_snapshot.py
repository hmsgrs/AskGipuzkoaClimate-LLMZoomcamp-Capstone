"""Build the committed, scoped historical reviewer snapshot."""

import argparse
import json
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

from app.euskalmet_scope import GIPUZKOA_ALERT_AREAS, REPRESENTATIVE_LOCATIONS
from app.portable_embeddings import export_embeddings, validate_export
from app.snapshot import create_snapshot, open_readonly_database


DEMO_SNAPSHOT_ID = "gipuzkoa-demo-2026-07-27"
DEMO_EFFECTIVE_DATE = "2026-07-27"
EXPECTED_COUNTS = {
    "sources": 9,
    "documents": 9,
    "chunks": 161,
    "evaluation_questions": 6,
    "weather_stations": 45,
    "weather_forecasts": 18,
    "weather_api_snapshots": 30,
    "aemet_daily_observations": 0,
    "hazard_alerts": 2,
    "ingestion_runs": 0,
}
def _table_counts(database, expected_counts):
    connection = open_readonly_database(database)
    try:
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in expected_counts
        }
    finally:
        connection.close()


def _forecast_date(value):
    for date_format in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return date.fromisoformat(value) if date_format == "%Y-%m-%d" else date(
                int(value[6:10]), int(value[3:5]), int(value[0:2])
            )
        except (ValueError, IndexError):
            continue
    return None


def _curate_representative_weather(connection, effective_date):
    issued_date = date.fromisoformat(effective_date)
    target_dates = tuple(issued_date + timedelta(days=offset) for offset in range(3))
    expected_forecasts = {
        (
            location.region,
            location.zone,
            location.location,
            issued_date.isoformat(),
            target.isoformat(),
        )
        for location in REPRESENTATIVE_LOCATIONS
        for target in target_dates
    }
    keep_forecasts = {}
    rows = connection.execute(
        """
        SELECT provider, snapshot_id, request_json
        FROM weather_api_snapshots
        ORDER BY retrieved_at DESC
        """
    ).fetchall()
    for provider, snapshot_id, request_json in rows:
        request = json.loads(request_json or "{}")
        key = (
            request.get("region"),
            request.get("zone"),
            request.get("location"),
            request.get("issued_date"),
            request.get("target_date"),
        )
        if provider == "euskalmet-location-forecast" and key in expected_forecasts:
            keep_forecasts.setdefault(key, snapshot_id)
    if set(keep_forecasts) != expected_forecasts:
        missing = sorted(expected_forecasts - set(keep_forecasts))
        raise ValueError(f"Missing authenticated forecast acquisitions: {missing}")
    keep_ids = set(keep_forecasts.values())
    for _, snapshot_id, _ in rows:
        if snapshot_id not in keep_ids:
            connection.execute(
                "DELETE FROM weather_api_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            )

    public_rows = connection.execute(
        "SELECT rowid, location_id, forecast_date FROM weather_forecasts"
    ).fetchall()
    for rowid, _, forecast_date in public_rows:
        if _forecast_date(forecast_date) not in target_dates:
            connection.execute("DELETE FROM weather_forecasts WHERE rowid = ?", (rowid,))

    expected_zones = {area.zone for area in GIPUZKOA_ALERT_AREAS}
    keep_alerts = {}
    alert_rows = connection.execute(
        """
        SELECT provider, alert_id, request_json
        FROM hazard_alerts
        ORDER BY retrieved_at DESC
        """
    ).fetchall()
    for provider, alert_id, request_json in alert_rows:
        request = json.loads(request_json or "{}")
        zone = request.get("zone")
        if (
            provider == "euskalmet-alerts"
            and request.get("issued_date") == issued_date.isoformat()
            and zone in expected_zones
        ):
            keep_alerts.setdefault(zone, alert_id)
    if set(keep_alerts) != expected_zones:
        raise ValueError(
            f"Missing authenticated alert acquisitions: {sorted(expected_zones - set(keep_alerts))}"
        )
    keep_alert_ids = set(keep_alerts.values())
    for _, alert_id, _ in alert_rows:
        if alert_id not in keep_alert_ids:
            connection.execute("DELETE FROM hazard_alerts WHERE alert_id = ?", (alert_id,))

    connection.execute("DELETE FROM aemet_daily_observations")
    connection.execute("DELETE FROM ingestion_runs")
    return {
        "issued_date": issued_date.isoformat(),
        "target_dates": [target.isoformat() for target in target_dates],
        "locations": [
            {
                "region": location.region,
                "zone": location.zone,
                "location": location.location,
                "display_name": location.display_name,
            }
            for location in REPRESENTATIVE_LOCATIONS
        ],
        "alert_zones": sorted(expected_zones),
    }


def curate_database(
    source: Path,
    destination: Path,
    *,
    effective_date: str = DEMO_EFFECTIVE_DATE,
):
    source_connection = open_readonly_database(source)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        for trigger in ("snapshot_metadata_no_update", "snapshot_metadata_no_delete"):
            target_connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
        target_connection.execute("DROP TABLE IF EXISTS snapshot_metadata")
        acquisition = _curate_representative_weather(
            target_connection, effective_date
        )
        target_connection.execute(
            "UPDATE ingestion_runs SET error = NULL WHERE error IS NOT NULL"
        )
        target_connection.commit()
        target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        target_connection.close()
        source_connection.close()
    counts = _table_counts(destination, EXPECTED_COUNTS)
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected demo coverage: {counts}")
    return counts, acquisition


def build_demo_snapshot(
    source: Path,
    output_root: Path,
    *,
    snapshot_id: str = DEMO_SNAPSHOT_ID,
    embedding_client=None,
    effective_date: str = DEMO_EFFECTIVE_DATE,
    embedding_artifact: Path | None = None,
):
    temporary_root = Path(tempfile.mkdtemp(prefix="askgipuzkoa-demo-"))
    try:
        curated = temporary_root / "curated.sqlite"
        embeddings = temporary_root / "embeddings.sqlite"
        counts, acquisition = curate_database(
            Path(source),
            curated,
            effective_date=effective_date,
        )
        if embedding_artifact is not None:
            shutil.copyfile(embedding_artifact, embeddings)
            validate_export(embeddings, curated)
        else:
            export_embeddings(
                curated,
                embeddings,
                embedding_client=embedding_client,
            )
        included = [
            "official knowledge corpus",
            "retrieval evaluation questions",
            "station metadata",
            "public city forecasts",
            "three authenticated forecast days for ten representative municipalities",
            "authenticated Gipuzkoa coast and interior warning responses",
        ]
        excluded = ["AEMET daily observations", "current conditions"]
        return create_snapshot(
            curated,
            Path(output_root),
            snapshot_id=snapshot_id,
            artifacts=[embeddings],
            notes=(
                "Scoped historical reviewer demo. Weather and warning responses are "
                "archived evidence and must not be interpreted as current conditions."
            ),
            required_tables=list(EXPECTED_COUNTS),
            require_nonempty=[
                table for table, count in EXPECTED_COUNTS.items() if count > 0
            ],
            effective_date=effective_date,
            coverage={
                "scope": "historical_reviewer_demo",
                "table_counts": counts,
                "included": included,
                "excluded": excluded,
                "euskalmet_acquisition": acquisition,
            },
        )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", default=DEMO_SNAPSHOT_ID)
    parser.add_argument("--effective-date", default=DEMO_EFFECTIVE_DATE)
    parser.add_argument("--embedding-artifact", type=Path)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = build_demo_snapshot(
        args.source,
        args.output_root,
        snapshot_id=args.snapshot_id,
        effective_date=args.effective_date,
        embedding_artifact=args.embedding_artifact,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
