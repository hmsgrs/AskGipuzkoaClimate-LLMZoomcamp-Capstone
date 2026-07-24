import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.assistant import QueryRouter, WeatherClimateAssistant, detect_language
from app.ingest import get_database
from app.metrics import RAGWithMetrics
from app.rag_helper import RAGBase, RAGResult
from app.weather_api import CachedWeatherRepository


class FakeRepository:
    def __init__(self, results):
        self.results = results

    def search(self, question, limit=5):
        return self.results[:limit]


class Usage:
    input_tokens = 100
    output_tokens = 50
    total_tokens = 150


class Response:
    output_text = "Grounded answer [S1]."
    usage = Usage()


class Responses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return Response()


class FakeOpenAI:
    def __init__(self):
        self.responses = Responses()


def result(source_id="source-one", chunk_id="chunk-one", text="Official evidence"):
    return {
        "chunk_id": chunk_id,
        "document_id": "document-one",
        "text": text,
        "title": "Official climate report",
        "source_id": source_id,
        "organization": "Euskalmet",
        "url": "https://example.test/report",
        "language": "en",
        "publication_date": "2026-01-01",
        "retrieved_at": "2026-07-22T12:00:00+00:00",
        "source_type": "climate_history",
        "score": 0.9,
    }


def test_rag_builds_bounded_context_and_deduplicated_source_cards():
    client = FakeOpenAI()
    repository = FakeRepository(
        [result(), result(chunk_id="chunk-two", text="More evidence")]
    )
    rag = RAGWithMetrics(repository, client, retrieval_backend="pgvector")

    output = rag.rag("What changed?", route="knowledge_base", language="en")

    assert output.answer == "Grounded answer [S1]."
    assert len(output.citations) == 1
    assert output.citations[0].citation_id == "S1"
    assert output.call.total_tokens == 150
    assert output.call.route == "knowledge_base"
    assert output.citation_valid is True
    prompt = client.responses.calls[0]["input"][1]["content"]
    assert client.responses.calls[0]["store"] is False
    assert "[S1] Official climate report" in prompt
    assert "Official evidence" in prompt
    assert "More evidence" in prompt


def test_rag_does_not_call_openai_without_official_context():
    client = FakeOpenAI()
    rag = RAGBase(FakeRepository([]), client, retrieval_backend="sqlite_fts5")

    output = rag.rag("Pregunta sin resultados", language="es")

    assert "No encuentro" in output.answer
    assert output.citations == ()
    assert client.responses.calls == []


def test_rag_fails_closed_when_generation_breaks_the_citation_contract():
    class UncitedResponse(Response):
        output_text = "Unsupported answer with https://untrusted.test"

    class UncitedResponses(Responses):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return UncitedResponse()

    client = FakeOpenAI()
    client.responses = UncitedResponses()
    rag = RAGWithMetrics(FakeRepository([result()]), client)

    output = rag.rag("What changed?", language="en")

    assert output.citation_valid is False
    assert output.citations == ()
    assert "fully cited" in output.answer


def test_router_and_language_detection_are_deterministic():
    router = QueryRouter()

    assert detect_language("¿Qué riesgos hay para Gipuzkoa?") == "es"
    assert detect_language("What risks affect Gipuzkoa?") == "en"
    assert detect_language("Temperatura en Donostia") == "es"
    assert detect_language("Necesito recomendaciones oficiales") == "es"
    assert router.route("¿Qué tiempo hará mañana?") == "live_weather"
    assert (
        router.route("Where can I find official adverse-weather recommendations?")
        == "knowledge_base"
    )
    assert router.route("How is the climate changing?") == "knowledge_base"
    assert router.route("Is Donostia flooding now?") == "live_weather"
    assert router.route("What happened in 1120?") == "knowledge_base"
    assert router.route("I am trapped and in immediate danger") == "emergency"


class RecordingRAG:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []

    def rag(self, question, num_results, route, language):
        self.calls.append((question, num_results, route, language))
        return RAGResult("answer", (), route, language, self.backend)


def test_assistant_routes_weather_and_short_circuits_emergencies():
    knowledge = RecordingRAG("pgvector")
    weather = RecordingRAG("cached_official_weather")
    assistant = WeatherClimateAssistant(knowledge, weather)

    live = assistant.ask("¿Qué tiempo hará mañana?")
    emergency = assistant.ask("Hay peligro inmediato, necesito ayuda")

    assert live.retrieval_backend == "cached_official_weather"
    assert weather.calls[0][2:] == ("live_weather", "es")
    assert emergency.route == "emergency"
    assert "112" in emergency.answer
    assert len(knowledge.calls) == 0
    with pytest.raises(ValueError, match="must not be empty"):
        assistant.ask("   ")


def test_cached_weather_repository_returns_freshness_and_sources(tmp_path):
    database = tmp_path / "weather.sqlite"
    connection = get_database(database)
    retrieved_at = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO weather_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet",
            "donostia",
            "2026-07-24",
            "Donostia",
            18.0,
            25.0,
            "Nubes y claros",
            "Hodeiak eta ostarteak",
            "https://example.test/forecast",
            retrieved_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO hazard_alerts
            (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-homepage",
            "old-alert",
            '{"warning": "historical snow"}',
            "https://example.test/warnings",
            (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "{}",
        ),
    )
    connection.execute(
        """
        INSERT INTO hazard_alerts
            (provider, alert_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-homepage",
            "alert-one",
            '{"warning": "heavy rain"}',
            "https://example.test/warnings",
            retrieved_at,
            "{}",
        ),
    )
    connection.commit()
    connection.close()

    results = CachedWeatherRepository(database).search(
        "¿Hay avisos? ¿Qué tiempo hará en Donostia?", limit=5
    )

    assert results[0]["source_type"] == "live_weather_alert"
    assert results[0]["stale"] is False
    assert all(row["chunk_id"] != "old-alert" for row in results)
    assert any(row["source_type"] == "live_weather_forecast" for row in results)


def test_cached_weather_uses_authenticated_snapshot_for_tomorrow(tmp_path):
    database = tmp_path / "weather.sqlite"
    connection = get_database(database)
    tomorrow = datetime.now(ZoneInfo("Europe/Madrid")).date() + timedelta(days=1)
    request = {
        "issued_date": datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat(),
        "target_date": tomorrow.isoformat(),
        "location": "donostia",
        "zone": "donostialdea",
        "region": "basque_country",
    }
    payload = {
        "forecastText": {"SPANISH": "Lluvia por la mañana."},
        "temperatureRange": {"min": 12.0, "max": 18.0, "unit": "CELSIUS_DEGREE"},
    }
    connection.execute(
        """
        INSERT INTO weather_api_snapshots
            (provider, snapshot_id, payload_json, source_url, retrieved_at, request_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "euskalmet-location-forecast",
            "snapshot-one",
            json.dumps(payload),
            "https://example.test/authenticated-forecast",
            datetime.now(UTC).isoformat(),
            json.dumps(request),
        ),
    )
    connection.commit()
    connection.close()

    results = CachedWeatherRepository(database).search(
        "What is tomorrow's forecast in Donostia?"
    )

    assert len(results) == 1
    assert results[0]["publication_date"] == request["issued_date"]
    assert tomorrow.isoformat() in results[0]["text"]
    assert results[0]["url"] == "https://example.test/authenticated-forecast"
