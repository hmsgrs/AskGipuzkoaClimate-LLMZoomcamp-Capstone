"""Application composition and deterministic bilingual query routing."""

import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI

from app.metrics import RAGWithMetrics
from app.pgvector_repository import PgvectorRepository
from app.rag_helper import RAGResult
from app.sqlite_repository import SQLiteRepository
from app.weather_api import CachedWeatherRepository


LIVE_WEATHER_TERMS = {
    "weather",
    "forecast",
    "temperature",
    "rain",
    "today",
    "tomorrow",
    "warning",
    "alert",
    "tiempo",
    "previsión",
    "pronóstico",
    "temperatura",
    "lluvia",
    "hoy",
    "mañana",
    "aviso",
    "alerta",
    "flood",
    "flooding",
    "inundación",
    "inundaciones",
}
KNOWLEDGE_GUIDANCE_TERMS = {
    "recommendation",
    "recommendations",
    "recommended",
    "prepare",
    "preparedness",
    "recomendación",
    "recomendaciones",
    "preparar",
    "preparación",
}
EMERGENCY_TERMS = {
    "immediate danger",
    "life threatening",
    "trapped",
    "emergency now",
    "peligro inmediato",
    "emergencia ahora",
    "atrapado",
    "atrapada",
}
SPANISH_MARKERS = {
    "qué",
    "cómo",
    "cuándo",
    "dónde",
    "hay",
    "para",
    "hoy",
    "mañana",
    "riesgo",
    "aviso",
    "actual",
    "clima",
    "de",
    "el",
    "en",
    "la",
    "las",
    "lluvia",
    "los",
    "meteorología",
    "necesito",
    "previsión",
    "recomendaciones",
    "temperatura",
    "tiempo",
}


def detect_language(question: str):
    words = set(re.findall(r"[\wáéíóúñü]+", question.casefold()))
    has_spanish_character = re.search(r"[áéíóúñü¿¡]", question.casefold()) is not None
    return "es" if has_spanish_character or words & SPANISH_MARKERS else "en"


class QueryRouter:
    def route(self, question: str):
        normalized = question.casefold()
        if re.search(r"\b112\b", normalized) or any(
            term in normalized for term in EMERGENCY_TERMS
        ):
            return "emergency"
        words = set(re.findall(r"[\wáéíóúñü]+", normalized))
        if words & KNOWLEDGE_GUIDANCE_TERMS:
            return "knowledge_base"
        if words & LIVE_WEATHER_TERMS:
            return "live_weather"
        return "knowledge_base"


class WeatherClimateAssistant:
    def __init__(self, knowledge_rag, weather_rag, router=None):
        self.knowledge_rag = knowledge_rag
        self.weather_rag = weather_rag
        self.router = router or QueryRouter()

    def ask(self, question: str, num_results: int = 5):
        question = question.strip()
        if not question:
            raise ValueError("Question must not be empty")
        language = detect_language(question)
        route = self.router.route(question)
        if route == "emergency":
            answer = (
                "Si existe peligro inmediato, llama al 112 ahora y sigue las "
                "instrucciones de los servicios oficiales. Esta aplicación no "
                "sustituye a los servicios de emergencia."
                if language == "es"
                else "If there is immediate danger, call 112 now and follow "
                "official emergency-service instructions. This application does "
                "not replace emergency services."
            )
            return RAGResult(answer, (), route, language, "safety_rule")
        rag = self.weather_rag if route == "live_weather" else self.knowledge_rag
        return rag.rag(question, num_results, route, language)

    def rag(self, question: str, num_results: int = 5):
        return self.ask(question, num_results)


def create_assistant(
    sqlite_database: Path | None = None,
    retrieval_backend: str | None = None,
    llm_client=None,
    embedding_client=None,
):
    database = Path(
        sqlite_database
        or os.getenv("SQLITE_DATABASE", "data/processed/ingestion.sqlite")
    )
    backend = retrieval_backend or os.getenv("RETRIEVAL_BACKEND", "pgvector")
    client = llm_client or OpenAI()
    model = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")
    weather_max_age = int(os.getenv("LIVE_DATA_MAX_AGE_HOURS", "3"))

    if backend == "pgvector":
        knowledge_repository = PgvectorRepository(
            database,
            embedding_client=embedding_client,
        )
    elif backend == "sqlite_fts5":
        knowledge_repository = SQLiteRepository(database)
    else:
        raise ValueError("RETRIEVAL_BACKEND must be pgvector or sqlite_fts5")

    knowledge_rag = RAGWithMetrics(
        knowledge_repository,
        client,
        model=model,
        retrieval_backend=backend,
    )
    weather_rag = RAGWithMetrics(
        CachedWeatherRepository(database, max_age_hours=weather_max_age),
        client,
        model=model,
        retrieval_backend="cached_official_weather",
    )
    return WeatherClimateAssistant(knowledge_rag, weather_rag)


def main():
    question = " ".join(sys.argv[1:]) or "What climate risks affect Gipuzkoa?"
    result = create_assistant().ask(question)
    print(
        json.dumps(
            {
                "answer": result.answer,
                "route": result.route,
                "language": result.language,
                "retrieval_backend": result.retrieval_backend,
                "citations": [citation.__dict__ for citation in result.citations],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
