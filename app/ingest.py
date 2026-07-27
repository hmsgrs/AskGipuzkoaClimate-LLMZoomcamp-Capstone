"""Course-style fetch, normalize, and load functions for project data sources."""

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
from filelock import FileLock

from app.aemet import AemetClient
from app.euskalmet import ALERT_ZONES, BASE_URL as EUSKALMET_BASE_URL, EuskalmetClient
from app.euskalmet_scope import GIPUZKOA_ALERT_AREAS, REPRESENTATIVE_LOCATIONS
from app.euskalmet_web import fetch_euskalmet_homepage
from app.embedding_sync import sync_embeddings
from app.knowledge_base import (
    ingest_corpus,
    ingest_source,
    initialize_knowledge_base,
    load_evaluation_questions,
)
from app.pgvector_repository import PgvectorRepository
from app.retrieval_evaluation import compare_retrieval, evaluate_fts
from app.source_registry import SOURCES_BY_ID
from app.sqlite_repository import SQLiteRepository


STATIONS_URL = (
    "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
    "estaciones_meteorologicas/opendata/estaciones.geojson"
)
FORECAST_URL = (
    "https://opendata.euskadi.eus/contenidos/prevision_tiempo/"
    "met_forecast/opendata/met_forecast.xml"
)
DEFAULT_DATABASE = Path("data/processed/ingestion.sqlite")
MADRID = ZoneInfo("Europe/Madrid")


def utc_now():
    return datetime.now(UTC).isoformat()


def get_database(path: Path = DEFAULT_DATABASE):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 60000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_stations (
            provider TEXT NOT NULL,
            station_id TEXT NOT NULL,
            name TEXT NOT NULL,
            municipality TEXT,
            province TEXT,
            latitude REAL,
            longitude REAL,
            active INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (provider, station_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_forecasts (
            provider TEXT NOT NULL,
            location_id TEXT NOT NULL,
            forecast_date TEXT NOT NULL,
            location_name TEXT NOT NULL,
            temperature_min REAL,
            temperature_max REAL,
            description_es TEXT,
            description_eu TEXT,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (provider, location_id, forecast_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS aemet_daily_observations (
            station_id TEXT NOT NULL,
            observed_date TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (station_id, observed_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hazard_alerts (
            provider TEXT NOT NULL,
            alert_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (provider, alert_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_api_snapshots (
            provider TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_url TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (provider, snapshot_id)
        )
        """
    )
    _ensure_column(connection, "hazard_alerts", "request_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(
        connection, "weather_api_snapshots", "request_json", "TEXT NOT NULL DEFAULT '{}'"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            run_id TEXT PRIMARY KEY,
            flow_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            requested INTEGER NOT NULL DEFAULT 0,
            succeeded INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            upserted INTEGER NOT NULL DEFAULT 0,
            max_observed_date TEXT,
            error TEXT
        )
        """
    )
    initialize_knowledge_base(connection)
    return connection


def _ensure_column(connection, table: str, column: str, definition: str):
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def load_euskalmet_stations(session=None):
    response = (session or requests).get(STATIONS_URL, timeout=60)
    response.raise_for_status()
    return response.json()["features"]


def save_euskalmet_stations(connection, features):
    retrieved_at = utc_now()
    rows = []
    for feature in features:
        properties = feature["properties"]
        if properties.get("provincia") != "Gipuzkoa":
            continue
        longitude, latitude = feature["geometry"]["coordinates"]
        rows.append(
            (
                "euskalmet",
                properties["codigo"],
                properties["nombre"],
                properties.get("municipio"),
                properties.get("provincia"),
                latitude,
                longitude,
                int(not properties.get("fechabaja")),
                STATIONS_URL,
                json.dumps(feature, ensure_ascii=True),
                retrieved_at,
            )
        )
    connection.executemany(
        """
        INSERT INTO weather_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, station_id) DO UPDATE SET
            name=excluded.name, municipality=excluded.municipality,
            province=excluded.province, latitude=excluded.latitude,
            longitude=excluded.longitude, active=excluded.active,
            source_url=excluded.source_url, payload_json=excluded.payload_json,
            retrieved_at=excluded.retrieved_at
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def load_euskalmet_forecast(session=None):
    response = (session or requests).get(FORECAST_URL, timeout=60)
    response.raise_for_status()
    return ElementTree.fromstring(response.content)


def save_euskalmet_forecast(connection, root):
    retrieved_at = utc_now()
    rows = []
    for forecast in root.findall("./forecasts/forecast"):
        description_es = forecast.findtext("./description/es")
        description_eu = forecast.findtext("./description/eu")
        for city in forecast.findall("./cityForecastDataList/cityForecastData"):
            rows.append(
                (
                    "euskalmet",
                    city.attrib["cityCode"],
                    forecast.attrib["forecastDate"],
                    city.attrib["cityName"],
                    city.findtext("tempMin"),
                    city.findtext("tempMax"),
                    description_es,
                    description_eu,
                    FORECAST_URL,
                    retrieved_at,
                )
            )
    connection.executemany(
        """
        INSERT INTO weather_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, location_id, forecast_date) DO UPDATE SET
            location_name=excluded.location_name,
            temperature_min=excluded.temperature_min,
            temperature_max=excluded.temperature_max,
            description_es=excluded.description_es,
            description_eu=excluded.description_eu,
            source_url=excluded.source_url, retrieved_at=excluded.retrieved_at
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def save_aemet_stations(connection, stations):
    retrieved_at = utc_now()
    rows = []
    for station in stations:
        if station.get("provincia", "").upper() != "GIPUZKOA":
            continue
        rows.append(
            (
                "aemet",
                station["indicativo"],
                station.get("nombre", station["indicativo"]),
                station.get("nombre"),
                station.get("provincia"),
                station.get("latitud_dec"),
                station.get("longitud_dec"),
                1,
                "https://opendata.aemet.es/centrodedescargas/inicio",
                json.dumps(station, ensure_ascii=True),
                retrieved_at,
            )
        )
    connection.executemany(
        """
        INSERT INTO weather_stations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, station_id) DO UPDATE SET
            name=excluded.name, municipality=excluded.municipality,
            province=excluded.province, latitude=excluded.latitude,
            longitude=excluded.longitude, payload_json=excluded.payload_json,
            retrieved_at=excluded.retrieved_at
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def save_aemet_daily_observations(connection, observations):
    retrieved_at = utc_now()
    rows = [
        (
            observation["indicativo"],
            observation["fecha"],
            json.dumps(observation, ensure_ascii=True),
            retrieved_at,
        )
        for observation in observations
    ]
    connection.executemany(
        """
        INSERT INTO aemet_daily_observations VALUES (?, ?, ?, ?)
        ON CONFLICT(station_id, observed_date) DO UPDATE SET
            payload_json=excluded.payload_json, retrieved_at=excluded.retrieved_at
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def _content_address(provider, request, payload):
    identity = json.dumps(
        {"provider": provider, "request": request or {}, "payload": payload},
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def save_alert_snapshot(connection, provider, payload, source_url, request=None):
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    request_json = json.dumps(request or {}, ensure_ascii=True, sort_keys=True)
    alert_id = _content_address(provider, request, payload)
    connection.execute(
        """
        INSERT INTO hazard_alerts
            (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, alert_id) DO UPDATE SET
            payload_json=excluded.payload_json, source_url=excluded.source_url,
            retrieved_at=excluded.retrieved_at, request_json=excluded.request_json
        """,
        (provider, alert_id, serialized, source_url, utc_now(), request_json),
    )
    connection.commit()
    return alert_id


def save_weather_snapshot(connection, provider, payload, source_url, request=None):
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    request_json = json.dumps(request or {}, ensure_ascii=True, sort_keys=True)
    snapshot_id = _content_address(provider, request, payload)
    connection.execute(
        """
        INSERT INTO weather_api_snapshots
            (provider, snapshot_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, snapshot_id) DO UPDATE SET
            payload_json=excluded.payload_json, source_url=excluded.source_url,
            retrieved_at=excluded.retrieved_at, request_json=excluded.request_json
        """,
        (provider, snapshot_id, serialized, source_url, utc_now(), request_json),
    )
    connection.commit()
    return snapshot_id


def print_snapshot_result(database, table, record_id, source_url):
    print(
        json.dumps(
            {
                "status": "ok",
                "upserted": 1,
                "table": table,
                "database": str(database),
                "record_id": record_id,
                "source_url": source_url,
            },
            indent=2,
        )
    )


def save_euskalmet_homepage(connection, homepage):
    for alert in homepage["alerts"]:
        save_alert_snapshot(
            connection,
            homepage["source_id"],
            alert,
            homepage["url"],
            {"source_id": homepage["source_id"]},
        )
    return len(homepage["alerts"])


def begin_run(connection, flow_id: str):
    run_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO ingestion_runs (run_id, flow_id, started_at, status) VALUES (?, ?, ?, ?)",
        (run_id, flow_id, utc_now(), "running"),
    )
    connection.commit()
    return run_id


def finish_run(
    connection,
    run_id: str,
    *,
    status: str,
    requested: int,
    succeeded: int,
    failed: int,
    upserted: int,
    max_observed_date: str | None = None,
    error: str | None = None,
):
    connection.execute(
        """
        UPDATE ingestion_runs SET
            finished_at=?, status=?, requested=?, succeeded=?, failed=?,
            upserted=?, max_observed_date=?, error=?
        WHERE run_id=?
        """,
        (
            utc_now(),
            status,
            requested,
            succeeded,
            failed,
            upserted,
            max_observed_date,
            error,
            run_id,
        ),
    )
    connection.commit()
    return {
        "run_id": run_id,
        "status": status,
        "requested": requested,
        "succeeded": succeeded,
        "failed": failed,
        "upserted": upserted,
        "max_observed_date": max_observed_date,
    }


def operation_date(value: str | None):
    return date.fromisoformat(value) if value else datetime.now(MADRID).date()


def date_chunks(start: date, end: date, chunk_days: int):
    if start > end:
        return []
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def refresh_euskalmet_alerts(connection, zone, as_of=None, client=None):
    return refresh_euskalmet_alert_scope(
        connection, (zone,), as_of=as_of, client=client
    )


def refresh_euskalmet_alert_scope(connection, zones, as_of=None, client=None):
    zones = tuple(zones)
    if not zones:
        raise ValueError("At least one Euskalmet alert zone is required")
    invalid = sorted(set(zones) - set(ALERT_ZONES))
    if invalid:
        raise ValueError(f"Unknown Euskalmet alert zones: {', '.join(invalid)}")
    issued_date = operation_date(as_of)
    client = client or EuskalmetClient()
    flow_id = "ingest_euskalmet_authenticated_alerts"
    run_id = begin_run(connection, flow_id)
    succeeded = 0
    try:
        for zone in zones:
            request = {"zone": zone, "issued_date": issued_date.isoformat()}
            payload = client.alert_forecast(zone, issued_date)
            if not isinstance(payload, (dict, list)):
                raise ValueError(f"Unexpected Euskalmet alert payload for {zone}")
            path = f"/euskalmet/alerts/zones/{zone}/forecast/at/{issued_date:%Y/%m/%d}"
            save_alert_snapshot(
                connection,
                "euskalmet-alerts",
                payload,
                f"{EUSKALMET_BASE_URL}{path}",
                request,
            )
            succeeded += 1
        return finish_run(
            connection,
            run_id,
            status="ok",
            requested=len(zones),
            succeeded=succeeded,
            failed=0,
            upserted=succeeded,
        )
    except Exception as error:
        finish_run(
            connection,
            run_id,
            status="failed",
            requested=len(zones),
            succeeded=succeeded,
            failed=len(zones) - succeeded,
            upserted=succeeded,
            error=str(error),
        )
        raise


def refresh_euskalmet_forecasts(
    connection,
    region,
    zone,
    location,
    horizon_days=3,
    as_of=None,
    client=None,
):
    location_scope = ((region, zone, location),)
    return refresh_euskalmet_forecast_scope(
        connection,
        location_scope,
        horizon_days=horizon_days,
        as_of=as_of,
        client=client,
    )


def refresh_euskalmet_forecast_scope(
    connection,
    locations,
    horizon_days=3,
    as_of=None,
    client=None,
):
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    locations = tuple(locations)
    if not locations:
        raise ValueError("At least one Euskalmet forecast location is required")
    issued_date = operation_date(as_of)
    client = client or EuskalmetClient()
    flow_id = "ingest_euskalmet_authenticated_forecasts"
    run_id = begin_run(connection, flow_id)
    succeeded = 0
    try:
        for location_scope in locations:
            region, zone, location = location_scope
            for offset in range(horizon_days):
                target_date = issued_date + timedelta(days=offset)
                payload = client.location_forecast(
                    region, zone, location, issued_date, target_date
                )
                if not isinstance(payload, dict) or not payload:
                    raise ValueError(
                        f"Unexpected Euskalmet forecast payload for {location} "
                        f"on {target_date.isoformat()}"
                    )
                path = (
                    f"/euskalmet/weather/regions/{region}/zones/{zone}/"
                    f"locations/{location}/forecast/at/{issued_date:%Y/%m/%d}/"
                    f"for/{target_date:%Y%m%d}"
                )
                save_weather_snapshot(
                    connection,
                    "euskalmet-location-forecast",
                    payload,
                    f"{EUSKALMET_BASE_URL}{path}",
                    {
                        "region": region,
                        "zone": zone,
                        "location": location,
                        "issued_date": issued_date.isoformat(),
                        "target_date": target_date.isoformat(),
                    },
                )
                succeeded += 1
        requested = len(locations) * horizon_days
        return finish_run(
            connection,
            run_id,
            status="ok",
            requested=requested,
            succeeded=succeeded,
            failed=0,
            upserted=succeeded,
        )
    except Exception as error:
        finish_run(
            connection,
            run_id,
            status="failed",
            requested=len(locations) * horizon_days,
            succeeded=succeeded,
            failed=len(locations) * horizon_days - succeeded,
            upserted=succeeded,
            error=str(error),
        )
        raise


def refresh_aemet_daily(
    connection,
    station,
    as_of=None,
    lag_days=2,
    lookback_days=7,
    chunk_days=31,
    initial_start="2024-01-01",
    start_date=None,
    end_date=None,
    client=None,
):
    if min(lag_days, lookback_days) < 0 or lookback_days == 0:
        raise ValueError("lag_days must be non-negative and lookback_days must be positive")
    is_backfill = start_date is not None or end_date is not None
    if is_backfill:
        if not start_date or not end_date:
            raise ValueError("AEMET backfill requires non-empty start and end dates")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    else:
        end = operation_date(as_of) - timedelta(days=lag_days)
        watermark = connection.execute(
            "SELECT MAX(observed_date) FROM aemet_daily_observations WHERE station_id = ?",
            (station,),
        ).fetchone()[0]
        repair_start = end - timedelta(days=lookback_days - 1)
        start = (
            min(date.fromisoformat(watermark) + timedelta(days=1), repair_start)
            if watermark
            else date.fromisoformat(initial_start)
        )
    if start > end:
        raise ValueError("AEMET start date must not be after end date")
    ranges = date_chunks(start, end, chunk_days)
    client = client or AemetClient()
    flow_id = "backfill_aemet_daily" if is_backfill else "ingest_aemet_daily_incremental"
    run_id = begin_run(connection, flow_id)
    upserted = 0
    succeeded = 0
    try:
        for chunk_start, chunk_end in ranges:
            observations = client.daily_observations(
                station, chunk_start.isoformat(), chunk_end.isoformat()
            )
            for observation in observations:
                if observation.get("indicativo") != station:
                    raise ValueError("AEMET returned an unexpected station")
                observed = date.fromisoformat(observation["fecha"])
                if not chunk_start <= observed <= chunk_end:
                    raise ValueError("AEMET returned a date outside the requested range")
            upserted += save_aemet_daily_observations(connection, observations)
            succeeded += 1
        maximum = connection.execute(
            "SELECT MAX(observed_date) FROM aemet_daily_observations WHERE station_id = ?",
            (station,),
        ).fetchone()[0]
        return finish_run(
            connection,
            run_id,
            status="ok",
            requested=len(ranges),
            succeeded=succeeded,
            failed=0,
            upserted=upserted,
            max_observed_date=maximum,
        )
    except Exception as error:
        finish_run(
            connection,
            run_id,
            status="failed",
            requested=len(ranges),
            succeeded=succeeded,
            failed=len(ranges) - succeeded,
            upserted=upserted,
            error=str(error),
        )
        raise


def parse_args():
    parser = argparse.ArgumentParser(description="Ingest Gipuzkoa weather sources.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("euskalmet-stations")
    subparsers.add_parser("euskalmet-forecast")
    subparsers.add_parser("aemet-stations")

    daily = subparsers.add_parser("aemet-daily")
    daily.add_argument("--station", required=True)
    daily.add_argument("--start", required=True, help="YYYY-MM-DD")
    daily.add_argument("--end", required=True, help="YYYY-MM-DD")

    forecast = subparsers.add_parser("euskalmet-location-forecast")
    forecast.add_argument("--region", default="basque_country")
    forecast.add_argument("--zone", required=True)
    forecast.add_argument("--location", required=True)
    forecast.add_argument("--issued", required=True, help="YYYY-MM-DD")
    forecast.add_argument("--target", required=True, help="YYYY-MM-DD")

    station = subparsers.add_parser("euskalmet-station-current")
    station.add_argument("--station", required=True)

    readings = subparsers.add_parser("euskalmet-readings")
    readings.add_argument("--station", required=True)
    readings.add_argument("--sensor", required=True)
    readings.add_argument("--measure-type", required=True)
    readings.add_argument("--measure", required=True)
    readings.add_argument("--date", required=True, help="YYYY-MM-DD")
    readings.add_argument("--hour", required=True, type=int)

    alerts = subparsers.add_parser("euskalmet-alerts")
    alerts.add_argument("--zone", choices=ALERT_ZONES, required=True)
    alerts.add_argument("--issued", required=True, help="YYYY-MM-DD")
    alerts.add_argument("--target", help="Optional target date, YYYY-MM-DD")
    subparsers.add_parser("euskalmet-homepage-alerts")

    alert_refresh = subparsers.add_parser("refresh-euskalmet-alerts")
    alert_refresh.add_argument("--zone", choices=ALERT_ZONES, default="GIPUZKOA_COAST")
    alert_refresh.add_argument(
        "--scope", choices=("single", "gipuzkoa"), default="single"
    )
    alert_refresh.add_argument("--as-of", help="Europe/Madrid date, YYYY-MM-DD")

    forecast_refresh = subparsers.add_parser("refresh-euskalmet-forecasts")
    forecast_refresh.add_argument("--region", default="basque_country")
    forecast_refresh.add_argument("--zone", default="donostialdea")
    forecast_refresh.add_argument("--location", default="donostia")
    forecast_refresh.add_argument("--horizon-days", type=int, default=3)
    forecast_refresh.add_argument(
        "--scope", choices=("single", "representative"), default="single"
    )
    forecast_refresh.add_argument("--as-of", help="Europe/Madrid date, YYYY-MM-DD")

    aemet_refresh = subparsers.add_parser("refresh-aemet-daily")
    aemet_refresh.add_argument("--station", default="1012P")
    aemet_refresh.add_argument("--as-of", help="Europe/Madrid date, YYYY-MM-DD")
    aemet_refresh.add_argument("--lag-days", type=int, default=2)
    aemet_refresh.add_argument("--lookback-days", type=int, default=7)
    aemet_refresh.add_argument("--chunk-days", type=int, default=31)
    aemet_refresh.add_argument("--initial-start", default="2024-01-01")

    aemet_backfill = subparsers.add_parser("backfill-aemet-daily")
    aemet_backfill.add_argument("--station", default="1012P")
    aemet_backfill.add_argument("--start", required=True, help="YYYY-MM-DD")
    aemet_backfill.add_argument("--end", required=True, help="YYYY-MM-DD")
    aemet_backfill.add_argument("--chunk-days", type=int, default=31)

    source = subparsers.add_parser("knowledge-source")
    source.add_argument("--source", choices=tuple(SOURCES_BY_ID), required=True)
    subparsers.add_parser("knowledge-corpus")

    evaluation = subparsers.add_parser("knowledge-evaluation")
    evaluation.add_argument(
        "--questions",
        type=Path,
        default=Path("evaluation/retrieval_questions.json"),
    )
    evaluation.add_argument("--limit", type=int, default=5)

    search = subparsers.add_parser("knowledge-search")
    search.add_argument("--question", required=True)
    search.add_argument("--limit", type=int, default=5)

    embeddings = subparsers.add_parser("knowledge-embeddings")
    embeddings.add_argument("--batch-size", type=int, default=100)

    vector_search = subparsers.add_parser("vector-search")
    vector_search.add_argument("--question", required=True)
    vector_search.add_argument("--limit", type=int, default=5)

    comparison = subparsers.add_parser("retrieval-comparison")
    comparison.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def main():
    args = parse_args()
    lock = FileLock(f"{args.database}.lock", timeout=60)
    # Serialize schema initialization only; SQLite WAL handles short write transactions.
    with lock:
        connection = get_database(args.database)
    try:
        if args.command == "euskalmet-stations":
            print(save_euskalmet_stations(connection, load_euskalmet_stations()))
        elif args.command == "euskalmet-forecast":
            print(save_euskalmet_forecast(connection, load_euskalmet_forecast()))
        elif args.command == "aemet-stations":
            print(save_aemet_stations(connection, AemetClient().station_inventory()))
        elif args.command == "aemet-daily":
            observations = AemetClient().daily_observations(
                args.station, args.start, args.end
            )
            print(save_aemet_daily_observations(connection, observations))
        elif args.command == "euskalmet-location-forecast":
            issued_date = date.fromisoformat(args.issued)
            target_date = date.fromisoformat(args.target)
            payload = EuskalmetClient().location_forecast(
                args.region, args.zone, args.location, issued_date, target_date
            )
            path = (
                f"/euskalmet/weather/regions/{args.region}/zones/{args.zone}/"
                f"locations/{args.location}/forecast/at/{issued_date:%Y/%m/%d}/"
                f"for/{target_date:%Y%m%d}"
            )
            snapshot_id = save_weather_snapshot(
                connection,
                "euskalmet-location-forecast",
                payload,
                f"{EUSKALMET_BASE_URL}{path}",
                {
                    "region": args.region,
                    "zone": args.zone,
                    "location": args.location,
                    "issued_date": issued_date.isoformat(),
                    "target_date": target_date.isoformat(),
                },
            )
            print_snapshot_result(
                args.database,
                "weather_api_snapshots",
                snapshot_id,
                f"{EUSKALMET_BASE_URL}{path}",
            )
        elif args.command == "euskalmet-station-current":
            payload = EuskalmetClient().current_station(args.station)
            path = f"/euskalmet/stations/{args.station}/current"
            snapshot_id = save_weather_snapshot(
                connection,
                "euskalmet-station-current",
                payload,
                f"{EUSKALMET_BASE_URL}{path}",
                {"station": args.station},
            )
            print_snapshot_result(
                args.database,
                "weather_api_snapshots",
                snapshot_id,
                f"{EUSKALMET_BASE_URL}{path}",
            )
        elif args.command == "euskalmet-readings":
            observed_at = date.fromisoformat(args.date)
            payload = EuskalmetClient().readings(
                args.station,
                args.sensor,
                args.measure_type,
                args.measure,
                observed_at,
                args.hour,
            )
            path = (
                f"/euskalmet/readings/forStation/{args.station}/{args.sensor}/"
                f"measures/{args.measure_type}/{args.measure}/"
                f"at/{observed_at:%Y/%m/%d}/{args.hour:02d}"
            )
            snapshot_id = save_weather_snapshot(
                connection,
                "euskalmet-readings",
                payload,
                f"{EUSKALMET_BASE_URL}{path}",
                {
                    "station": args.station,
                    "sensor": args.sensor,
                    "measure_type": args.measure_type,
                    "measure": args.measure,
                    "date": observed_at.isoformat(),
                    "hour": args.hour,
                },
            )
            print_snapshot_result(
                args.database,
                "weather_api_snapshots",
                snapshot_id,
                f"{EUSKALMET_BASE_URL}{path}",
            )
        elif args.command == "euskalmet-alerts":
            issued_date = date.fromisoformat(args.issued)
            target_date = date.fromisoformat(args.target) if args.target else None
            payload = EuskalmetClient().alert_forecast(
                args.zone, issued_date, target_date
            )
            path = (
                f"/euskalmet/alerts/zones/{args.zone}/forecast/"
                f"at/{issued_date:%Y/%m/%d}"
            )
            if target_date is not None:
                path += f"/for/{target_date:%Y%m%d}"
            alert_id = save_alert_snapshot(
                connection,
                "euskalmet-alerts",
                payload,
                f"{EUSKALMET_BASE_URL}{path}",
                {
                    "zone": args.zone,
                    "issued_date": issued_date.isoformat(),
                    "target_date": target_date.isoformat() if target_date else None,
                },
            )
            print_snapshot_result(
                args.database,
                "hazard_alerts",
                alert_id,
                f"{EUSKALMET_BASE_URL}{path}",
            )
        elif args.command == "euskalmet-homepage-alerts":
            print(json.dumps(save_euskalmet_homepage(connection, fetch_euskalmet_homepage())))
        elif args.command == "refresh-euskalmet-alerts":
            zones = (
                tuple(area.zone for area in GIPUZKOA_ALERT_AREAS)
                if args.scope == "gipuzkoa"
                else (args.zone,)
            )
            print(
                json.dumps(
                    refresh_euskalmet_alert_scope(
                        connection, zones, as_of=args.as_of
                    ),
                    indent=2,
                )
            )
        elif args.command == "refresh-euskalmet-forecasts":
            locations = (
                tuple(
                    (location.region, location.zone, location.location)
                    for location in REPRESENTATIVE_LOCATIONS
                )
                if args.scope == "representative"
                else ((args.region, args.zone, args.location),)
            )
            print(
                json.dumps(
                    refresh_euskalmet_forecast_scope(
                        connection,
                        locations,
                        horizon_days=args.horizon_days,
                        as_of=args.as_of,
                    ),
                    indent=2,
                )
            )
        elif args.command == "refresh-aemet-daily":
            print(
                json.dumps(
                    refresh_aemet_daily(
                        connection,
                        args.station,
                        as_of=args.as_of,
                        lag_days=args.lag_days,
                        lookback_days=args.lookback_days,
                        chunk_days=args.chunk_days,
                        initial_start=args.initial_start,
                    ),
                    indent=2,
                )
            )
        elif args.command == "backfill-aemet-daily":
            print(
                json.dumps(
                    refresh_aemet_daily(
                        connection,
                        args.station,
                        chunk_days=args.chunk_days,
                        start_date=args.start,
                        end_date=args.end,
                    ),
                    indent=2,
                )
            )
        elif args.command == "knowledge-source":
            result = ingest_source(connection, SOURCES_BY_ID[args.source])
            print(json.dumps({"source_id": args.source, **result}, indent=2))
        elif args.command == "knowledge-corpus":
            print(json.dumps(ingest_corpus(connection), indent=2))
        elif args.command == "knowledge-evaluation":
            load_evaluation_questions(connection, args.questions)
            print(json.dumps(evaluate_fts(args.database, args.limit), indent=2))
        elif args.command == "knowledge-search":
            print(
                json.dumps(
                    SQLiteRepository(args.database).search(args.question, args.limit),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "knowledge-embeddings":
            print(
                json.dumps(
                    sync_embeddings(args.database, batch_size=args.batch_size), indent=2
                )
            )
        elif args.command == "vector-search":
            print(
                json.dumps(
                    PgvectorRepository(args.database).search(args.question, args.limit),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "retrieval-comparison":
            print(json.dumps(compare_retrieval(args.database, args.limit), indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
