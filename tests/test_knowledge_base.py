import json
from pathlib import Path

from app.ingest import get_database
from app.knowledge_base import chunk_text, load_evaluation_questions, upsert_document
from app.retrieval_evaluation import evaluate_fts
from app.source_registry import MAX_PDF_BYTES, SOURCES, Source
from app.sqlite_repository import SQLiteRepository, fts_query


def test_registry_is_limited_to_approved_organizations_and_bounded_pdfs():
    assert {source.organization for source in SOURCES} == {"Euskalmet", "Gobierno Vasco"}
    assert len([source for source in SOURCES if source.content_type == "text/html"]) == 3
    assert all(
        source.max_bytes == MAX_PDF_BYTES
        for source in SOURCES
        if source.content_type == "application/pdf"
    )


def test_chunking_is_deterministic_and_bounded():
    text = " ".join(f"word{index}" for index in range(500))

    first = chunk_text(text, max_chars=200, overlap_chars=30)
    second = chunk_text(text, max_chars=200, overlap_chars=30)

    assert first == second
    assert len(first) > 1
    assert all(len(chunk) <= 200 for chunk in first)


def test_document_versions_and_fts_search(tmp_path: Path):
    database = tmp_path / "knowledge.sqlite"
    connection = get_database(database)
    source = Source(
        source_id="test-climate",
        organization="Euskalmet",
        title="Informe de clima",
        url="https://example.test/climate",
        language="es",
        content_type="text/html",
        source_type="climate_history",
    )
    first = {
        "text": "La precipitacion invernal fue abundante en Gipuzkoa.",
        "content_hash": "first-hash",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    second = {
        "text": "La temperatura estival fue calida en Gipuzkoa.",
        "content_hash": "second-hash",
        "retrieved_at": "2026-02-01T00:00:00+00:00",
    }

    first_result = upsert_document(connection, source, first)
    assert upsert_document(connection, source, first) == first_result
    second_result = upsert_document(connection, source, second)
    connection.close()

    assert first_result["document_id"] != second_result["document_id"]
    connection = get_database(database)
    assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2
    connection.close()

    results = SQLiteRepository(database).search("temperatura Gipuzkoa")
    assert results[0]["source_id"] == "test-climate"
    assert "estival" in results[0]["text"]
    assert SQLiteRepository(database).search("precipitacion") == []


def test_fts_query_ignores_punctuation_and_empty_terms():
    assert fts_query("¿Cambio climático?") == '"cambio" OR "climático"'
    assert fts_query("?") == ""


def test_loads_bilingual_evaluation_questions(tmp_path: Path):
    database = tmp_path / "knowledge.sqlite"
    connection = get_database(database)
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            [
                {
                    "question_id": "question-es",
                    "question": "Que es el clima?",
                    "language": "es",
                    "expected_source_id": "basque-government-climate",
                    "category": "climate_guidance",
                },
                {
                    "question_id": "question-en",
                    "question": "What is climate?",
                    "language": "en",
                    "expected_source_id": "basque-government-climate",
                    "category": "climate_guidance",
                },
            ]
        ),
        encoding="utf-8",
    )

    assert load_evaluation_questions(connection, path) == 2
    assert connection.execute("SELECT COUNT(*) FROM evaluation_questions").fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(DISTINCT language) FROM evaluation_questions"
    ).fetchone()[0] == 2
    connection.close()

    result = evaluate_fts(database)
    assert result["method"] == "sqlite_fts5"
    assert result["questions"] == 2
    assert result["hits"] == 0
