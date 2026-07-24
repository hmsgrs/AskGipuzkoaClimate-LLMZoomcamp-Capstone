"""Evaluate retrieval methods against expected official sources."""

import sqlite3
from pathlib import Path

from app.knowledge_base import DEFAULT_DATABASE
from app.pgvector_repository import PgvectorRepository
from app.sqlite_repository import SQLiteRepository


def evaluation_questions(database: Path):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM evaluation_questions ORDER BY question_id"
        ).fetchall()
    finally:
        connection.close()


def evaluate_repository(repository, method: str, database: Path, limit: int = 5):
    details = []
    language_totals = {}
    language_hits = {}
    for question in evaluation_questions(database):
        results = repository.search(question["question"], limit=limit)
        retrieved_sources = list(dict.fromkeys(row["source_id"] for row in results))
        hit = question["expected_source_id"] in retrieved_sources
        rank = (
            retrieved_sources.index(question["expected_source_id"]) + 1 if hit else None
        )
        language = question["language"]
        language_totals[language] = language_totals.get(language, 0) + 1
        language_hits[language] = language_hits.get(language, 0) + int(hit)
        details.append(
            {
                "question_id": question["question_id"],
                "language": language,
                "expected_source_id": question["expected_source_id"],
                "retrieved_source_ids": retrieved_sources,
                "hit": hit,
                "rank": rank,
                "reciprocal_rank": 1 / rank if rank else 0.0,
            }
        )

    total = len(details)
    hits = sum(item["hit"] for item in details)
    return {
        "method": method,
        "limit": limit,
        "questions": total,
        "hits": hits,
        "hit_rate": hits / total if total else 0.0,
        "mrr": (
            sum(item["reciprocal_rank"] for item in details) / total if total else 0.0
        ),
        "hit_rate_by_language": {
            language: language_hits.get(language, 0) / count
            for language, count in language_totals.items()
        },
        "details": details,
    }


def evaluate_fts(database: Path = DEFAULT_DATABASE, limit: int = 5):
    return evaluate_repository(SQLiteRepository(database), "sqlite_fts5", database, limit)


def evaluate_pgvector(
    database: Path = DEFAULT_DATABASE,
    limit: int = 5,
    repository=None,
):
    repository = repository or PgvectorRepository(database)
    return evaluate_repository(repository, "pgvector", database, limit)


def compare_retrieval(database: Path = DEFAULT_DATABASE, limit: int = 5):
    return {
        "fts5": evaluate_fts(database, limit),
        "pgvector": evaluate_pgvector(database, limit),
    }
