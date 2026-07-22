"""Course-style fetch, normalize, and load functions for project data sources."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

from app.aemet import AemetClient
from app.euskalmet import ALERT_ZONES, BASE_URL as EUSKALMET_BASE_URL, EuskalmetClient
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


def utc_now():
    return datetime.now(UTC).isoformat()


def get_database(path: Path = DEFAULT_DATABASE):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
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
    initialize_knowledge_base(connection)
    return connection


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


def save_alert_snapshot(connection, provider, payload, source_url):
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    alert_id = hashlib.sha256(serialized.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO hazard_alerts VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(provider, alert_id) DO UPDATE SET
            payload_json=excluded.payload_json, retrieved_at=excluded.retrieved_at
        """,
        (provider, alert_id, serialized, source_url, utc_now()),
    )
    connection.commit()
    return alert_id


def save_weather_snapshot(connection, provider, payload, source_url):
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    snapshot_id = hashlib.sha256(serialized.encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO weather_api_snapshots VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(provider, snapshot_id) DO UPDATE SET
            payload_json=excluded.payload_json, retrieved_at=excluded.retrieved_at
        """,
        (provider, snapshot_id, serialized, source_url, utc_now()),
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
        save_alert_snapshot(connection, homepage["source_id"], alert, homepage["url"])
    return len(homepage["alerts"])


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
                connection, "euskalmet-station-current", payload, f"{EUSKALMET_BASE_URL}{path}"
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
                connection, "euskalmet-readings", payload, f"{EUSKALMET_BASE_URL}{path}"
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
            )
            print_snapshot_result(
                args.database,
                "hazard_alerts",
                alert_id,
                f"{EUSKALMET_BASE_URL}{path}",
            )
        elif args.command == "euskalmet-homepage-alerts":
            print(json.dumps(save_euskalmet_homepage(connection, fetch_euskalmet_homepage())))
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
