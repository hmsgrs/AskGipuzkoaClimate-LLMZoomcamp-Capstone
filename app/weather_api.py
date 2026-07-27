"""Query cached official weather and warning data refreshed by Kestra."""

import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.knowledge_base import DEFAULT_DATABASE
from app.euskalmet_scope import (
    alert_display_name,
    canonical_location_id,
    location_display_name,
    requested_alert_zones,
    requested_location_ids,
)
from app.snapshot import open_readonly_database


WARNING_TERMS = {
    "alert",
    "alerts",
    "warning",
    "warnings",
    "aviso",
    "avisos",
    "alerta",
    "alertas",
}
FORECAST_TERMS = {
    "forecast",
    "weather",
    "temperature",
    "rain",
    "today",
    "tomorrow",
    "previsión",
    "tiempo",
    "temperatura",
    "lluvia",
    "hoy",
    "mañana",
}
MADRID = ZoneInfo("Europe/Madrid")


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class CachedWeatherRepository:
    """Serve the latest official snapshots without making provider calls per question."""

    def __init__(
        self,
        database: Path = DEFAULT_DATABASE,
        max_age_hours: int = 3,
        snapshot_mode: bool = False,
    ):
        self.database = Path(database)
        self.max_age = timedelta(hours=max_age_hours)
        self.snapshot_mode = snapshot_mode

    def _stale(self, retrieved_at):
        timestamp = _parse_timestamp(retrieved_at)
        return timestamp is None or datetime.now(UTC) - timestamp > self.max_age

    def search(self, question: str, limit: int = 5):
        if not self.database.exists() or limit <= 0:
            return []
        connection = open_readonly_database(self.database)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            results = []
            terms = set(re.findall(r"[\wáéíóúñü]+", question.casefold()))
            wants_warnings = bool(terms & WARNING_TERMS)
            wants_forecast = bool(terms & FORECAST_TERMS) or not wants_warnings
            if "hazard_alerts" in tables and wants_warnings:
                results.extend(self._alerts(connection, question, limit))
            forecast_results = []
            if wants_forecast and "weather_api_snapshots" in tables:
                forecast_results = self._authenticated_forecasts(
                    connection, question, limit
                )
            if wants_forecast and not forecast_results and "weather_forecasts" in tables:
                forecast_results = self._forecasts(connection, question, limit)
            results.extend(forecast_results)
            return results[:limit]
        finally:
            connection.close()

    def _alerts(self, connection, question, limit):
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(hazard_alerts)")
        }
        request_expression = (
            "request_json" if "request_json" in columns else "'{}' AS request_json"
        )
        rows = connection.execute(
            f"""
            SELECT provider, alert_id, payload_json, source_url, retrieved_at,
                   {request_expression}
            FROM hazard_alerts
            ORDER BY retrieved_at DESC
            LIMIT 50
            """,
        ).fetchall()
        results = []
        seen_payloads = set()
        requested_zones = set(requested_alert_zones(question))
        for row in rows:
            stale = True if self.snapshot_mode else self._stale(row["retrieved_at"])
            if stale and not self.snapshot_mode:
                continue
            request = json.loads(row["request_json"] or "{}")
            zone = request.get("zone")
            if requested_zones and zone not in requested_zones:
                continue
            payload_key = (row["provider"], zone, row["payload_json"])
            if payload_key in seen_payloads:
                continue
            seen_payloads.add(payload_key)
            payload = json.loads(row["payload_json"])
            area = alert_display_name(zone) if zone else "Gipuzkoa"
            if payload in (None, [], {}):
                evidence = (
                    f"The archived Euskalmet response for {area} contained no warning "
                    "entries. This historical response does not establish current safety."
                )
            else:
                evidence = (
                    f"Archived warning response for {area}. Payload: "
                    f"{json.dumps(payload, ensure_ascii=False)[:3000]}"
                )
            results.append(
                {
                    "chunk_id": row["alert_id"],
                    "document_id": row["alert_id"],
                    "text": evidence,
                    "title": f"Archived warning response for {area}",
                    "source_id": (
                        f"{row['provider']}:alerts:{zone or row['alert_id']}:"
                        f"{request.get('issued_date', 'unknown')}"
                    ),
                    "organization": "Euskalmet",
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": request.get("issued_date"),
                    "retrieved_at": row["retrieved_at"],
                    "source_type": (
                        "snapshot_weather_alert"
                        if self.snapshot_mode
                        else "live_weather_alert"
                    ),
                    "stale": stale,
                    "score": 1.0,
                }
            )
            if len(results) == limit:
                break
        return results

    def _reference_date(self, connection):
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not self.snapshot_mode or "snapshot_metadata" not in tables:
            return datetime.now(MADRID).date()
        row = connection.execute(
            """
            SELECT effective_date, capture_completed_at, created_at
            FROM snapshot_metadata LIMIT 1
            """
        ).fetchone()
        if row and row[0]:
            return date.fromisoformat(row[0])
        timestamp = _parse_timestamp(row[1] or row[2]) if row else None
        return (
            timestamp.astimezone(MADRID).date()
            if timestamp
            else datetime.now(MADRID).date()
        )

    def _target_date(self, question, reference_date=None):
        normalized = question.casefold()
        terms = set(re.findall(r"[\wáéíóúñü]+", normalized))
        today = reference_date or datetime.now(MADRID).date()
        iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", normalized)
        if iso_date:
            try:
                return date.fromisoformat(iso_date.group(1))
            except ValueError:
                return None
        local_date = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", normalized)
        if local_date:
            try:
                return date(
                    int(local_date.group(3)),
                    int(local_date.group(2)),
                    int(local_date.group(1)),
                )
            except ValueError:
                return None
        if "day after tomorrow" in normalized or "pasado mañana" in normalized:
            return today + timedelta(days=2)
        if terms & {"tomorrow", "mañana"}:
            return today + timedelta(days=1)
        if terms & {"today", "hoy"}:
            return today
        weekdays = {
            "monday": 0,
            "lunes": 0,
            "tuesday": 1,
            "martes": 1,
            "wednesday": 2,
            "miércoles": 2,
            "miercoles": 2,
            "thursday": 3,
            "jueves": 3,
            "friday": 4,
            "viernes": 4,
            "saturday": 5,
            "sábado": 5,
            "sabado": 5,
            "sunday": 6,
            "domingo": 6,
        }
        for weekday, number in weekdays.items():
            if weekday in terms:
                return today + timedelta(days=(number - today.weekday()) % 7)
        return None

    def _authenticated_forecasts(self, connection, question, limit):
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(weather_api_snapshots)")
        }
        if "request_json" not in columns:
            return []
        rows = connection.execute(
            """
            SELECT provider, snapshot_id, payload_json, source_url, retrieved_at,
                   request_json
            FROM weather_api_snapshots
            WHERE provider = 'euskalmet-location-forecast'
            ORDER BY retrieved_at DESC
            """
        ).fetchall()
        reference_date = self._reference_date(connection)
        target = self._target_date(question, reference_date)
        requested_locations = set(requested_location_ids(question))
        latest_by_target = {}
        for row in rows:
            request = json.loads(row["request_json"] or "{}")
            target_value = request.get("target_date")
            if not target_value:
                continue
            try:
                target_date = date.fromisoformat(target_value)
            except ValueError:
                continue
            key = (request.get("location"), target_date)
            latest_by_target.setdefault(key, (row, request, target_date))

        candidates = list(latest_by_target.values())
        if requested_locations:
            candidates = [
                candidate
                for candidate in candidates
                if canonical_location_id(candidate[1].get("location", ""))
                in requested_locations
            ]
        if target is not None:
            candidates = [candidate for candidate in candidates if candidate[2] == target]
        else:
            today = reference_date
            candidates = [candidate for candidate in candidates if candidate[2] >= today]
        candidates.sort(key=lambda candidate: (candidate[2], candidate[1].get("location", "")))

        results = []
        for row, request, target_date in candidates[:limit]:
            payload = json.loads(row["payload_json"])
            forecast_text = payload.get("forecastText") or {}
            if isinstance(forecast_text, dict):
                description = forecast_text.get("SPANISH") or forecast_text.get("BASQUE")
            else:
                description = str(forecast_text)
            temperature = payload.get("temperatureRange") or payload.get("temperature") or {}
            location_id = canonical_location_id(request.get("location", ""))
            display_name = location_display_name(location_id)
            results.append(
                {
                    "chunk_id": row["snapshot_id"],
                    "document_id": row["snapshot_id"],
                    "text": (
                        f"Forecast for {display_name} on "
                        f"{target_date.isoformat()}: {description}. Temperature: "
                        f"{json.dumps(temperature, ensure_ascii=False)}"
                    ),
                    "title": (
                        f"Forecast for {display_name} "
                        f"on {target_date.isoformat()}"
                    ),
                    "source_id": (
                        f"{row['provider']}:{location_id}:{target_date.isoformat()}"
                    ),
                    "organization": "Euskalmet",
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": request.get("issued_date"),
                    "retrieved_at": row["retrieved_at"],
                    "source_type": (
                        "snapshot_weather_forecast"
                        if self.snapshot_mode
                        else "live_weather_forecast"
                    ),
                    "stale": True if self.snapshot_mode else self._stale(row["retrieved_at"]),
                    "score": 1.0,
                }
            )
        return results

    def _forecasts(self, connection, question, limit):
        rows = connection.execute(
            """
            SELECT provider, location_id, forecast_date, location_name,
                   temperature_min, temperature_max, description_es,
                   description_eu, source_url, retrieved_at
            FROM weather_forecasts
            ORDER BY forecast_date DESC, retrieved_at DESC
            LIMIT 50
            """
        ).fetchall()
        reference_date = self._reference_date(connection)
        target = self._target_date(question, reference_date)
        requested_locations = set(requested_location_ids(question))
        dated_rows = []
        for row in rows:
            parsed_date = None
            for date_format in ("%Y-%m-%d", "%d/%m/%Y"):
                try:
                    parsed_date = datetime.strptime(
                        row["forecast_date"], date_format
                    ).date()
                    break
                except ValueError:
                    continue
            if parsed_date is not None:
                dated_rows.append((row, parsed_date))
        if requested_locations:
            dated_rows = [
                item
                for item in dated_rows
                if canonical_location_id(item[0]["location_name"]) in requested_locations
            ]
        if target is not None:
            dated_rows = [item for item in dated_rows if item[1] == target]
        else:
            today = reference_date
            upcoming = [item for item in dated_rows if item[1] >= today]
            dated_rows = upcoming or dated_rows
        ordered = sorted(dated_rows, key=lambda item: (item[1], item[0]["location_name"]))
        results = []
        for row, parsed_date in ordered[:limit]:
            description = row["description_es"] or row["description_eu"] or ""
            text = (
                f"Forecast for {row['location_name']} on {row['forecast_date']}: "
                f"{description}. Minimum temperature {row['temperature_min']} C; "
                f"maximum temperature {row['temperature_max']} C."
            )
            results.append(
                {
                    "chunk_id": (
                        f"{row['provider']}:{row['location_id']}:{row['forecast_date']}"
                    ),
                    "document_id": f"{row['provider']}:{row['location_id']}",
                    "text": text,
                    "title": f"Forecast for {row['location_name']}",
                    "source_id": (
                        f"{row['provider']}:forecast:{row['location_id']}:"
                        f"{parsed_date.isoformat()}"
                    ),
                    "organization": row["provider"].title(),
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": parsed_date.isoformat(),
                    "retrieved_at": row["retrieved_at"],
                    "source_type": (
                        "snapshot_weather_forecast"
                        if self.snapshot_mode
                        else "live_weather_forecast"
                    ),
                    "stale": True if self.snapshot_mode else self._stale(row["retrieved_at"]),
                    "score": 1.0,
                }
            )
        return results
