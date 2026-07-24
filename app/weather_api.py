"""Query cached official weather and warning data refreshed by Kestra."""

import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.knowledge_base import DEFAULT_DATABASE


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

    def __init__(self, database: Path = DEFAULT_DATABASE, max_age_hours: int = 3):
        self.database = Path(database)
        self.max_age = timedelta(hours=max_age_hours)

    def _stale(self, retrieved_at):
        timestamp = _parse_timestamp(retrieved_at)
        return timestamp is None or datetime.now(UTC) - timestamp > self.max_age

    def search(self, question: str, limit: int = 5):
        if not self.database.exists() or limit <= 0:
            return []
        connection = sqlite3.connect(self.database)
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
                results.extend(self._alerts(connection, limit))
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

    def _alerts(self, connection, limit):
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
        for row in rows:
            if self._stale(row["retrieved_at"]):
                continue
            payload_key = (row["provider"], row["payload_json"])
            if payload_key in seen_payloads:
                continue
            seen_payloads.add(payload_key)
            request = json.loads(row["request_json"] or "{}")
            results.append(
                {
                    "chunk_id": row["alert_id"],
                    "document_id": row["alert_id"],
                    "text": (
                        "Recent cached warning snapshot. Verify current validity on the "
                        f"official page. Request: {json.dumps(request)}. Payload: "
                        f"{json.dumps(json.loads(row['payload_json']), ensure_ascii=False)[:3000]}"
                    ),
                    "title": "Recent official warning snapshot",
                    "source_id": f"{row['provider']}-alerts-{row['alert_id']}",
                    "organization": row["provider"].replace("-", " ").title(),
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": None,
                    "retrieved_at": row["retrieved_at"],
                    "source_type": "live_weather_alert",
                    "stale": False,
                    "score": 1.0,
                }
            )
            if len(results) == limit:
                break
        return results

    def _target_date(self, question):
        terms = set(re.findall(r"[\wáéíóúñü]+", question.casefold()))
        today = datetime.now(MADRID).date()
        if terms & {"tomorrow", "mañana"}:
            return today + timedelta(days=1)
        if terms & {"today", "hoy"}:
            return today
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
            LIMIT 30
            """
        ).fetchall()
        target = self._target_date(question)
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
        if target is not None:
            candidates = [candidate for candidate in candidates if candidate[2] == target]
        else:
            today = datetime.now(MADRID).date()
            candidates = [candidate for candidate in candidates if candidate[2] >= today]
        candidates.sort(key=lambda candidate: candidate[2])

        results = []
        for row, request, target_date in candidates[:limit]:
            payload = json.loads(row["payload_json"])
            forecast_text = payload.get("forecastText") or {}
            if isinstance(forecast_text, dict):
                description = forecast_text.get("SPANISH") or forecast_text.get("BASQUE")
            else:
                description = str(forecast_text)
            temperature = payload.get("temperatureRange") or payload.get("temperature") or {}
            results.append(
                {
                    "chunk_id": row["snapshot_id"],
                    "document_id": row["snapshot_id"],
                    "text": (
                        f"Forecast for {request.get('location', 'Gipuzkoa')} on "
                        f"{target_date.isoformat()}: {description}. Temperature: "
                        f"{json.dumps(temperature, ensure_ascii=False)}"
                    ),
                    "title": (
                        f"Forecast for {request.get('location', 'Gipuzkoa')} "
                        f"on {target_date.isoformat()}"
                    ),
                    "source_id": (
                        f"{row['provider']}:{request.get('location')}:{target_date.isoformat()}"
                    ),
                    "organization": "Euskalmet",
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": request.get("issued_date"),
                    "retrieved_at": row["retrieved_at"],
                    "source_type": "live_weather_forecast",
                    "stale": self._stale(row["retrieved_at"]),
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
        normalized_question = question.casefold()
        target = self._target_date(question)
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
        if target is not None:
            dated_rows = [item for item in dated_rows if item[1] == target]
        else:
            today = datetime.now(MADRID).date()
            upcoming = [item for item in dated_rows if item[1] >= today]
            dated_rows = upcoming or dated_rows
        ordered = sorted(
            dated_rows,
            key=lambda item: (
                item[0]["location_name"].casefold() not in normalized_question,
                item[0]["location_name"].casefold()
                not in {"donostia", "donostia-san sebastián", "san sebastián"},
                item[1],
            ),
        )
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
                    "source_id": f"{row['provider']}-forecast",
                    "organization": row["provider"].title(),
                    "url": row["source_url"],
                    "language": "es",
                    "publication_date": parsed_date.isoformat(),
                    "retrieved_at": row["retrieved_at"],
                    "source_type": "live_weather_forecast",
                    "stale": self._stale(row["retrieved_at"]),
                    "score": 1.0,
                }
            )
        return results
