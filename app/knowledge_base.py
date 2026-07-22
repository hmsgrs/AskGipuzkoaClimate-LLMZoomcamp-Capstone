"""Canonical SQLite schema and ingestion for the RAG knowledge base."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.documents import fetch_source_document
from app.source_registry import SOURCES, Source


DEFAULT_DATABASE = Path("data/processed/ingestion.sqlite")


def utc_now():
    return datetime.now(UTC).isoformat()


def initialize_knowledge_base(connection: sqlite3.Connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY,
            organization TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            content_type TEXT NOT NULL,
            source_type TEXT NOT NULL,
            publication_date TEXT,
            retrieved_at TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES sources(source_id),
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            publication_date TEXT,
            retrieved_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(source_id, content_hash)
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            UNIQUE(document_id, chunk_index)
        );

        CREATE TABLE IF NOT EXISTS evaluation_questions (
            question_id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            language TEXT NOT NULL,
            expected_source_id TEXT NOT NULL,
            category TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            text,
            title,
            organization,
            language UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
    connection.commit()


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 150):
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start
        size = 0
        while end < len(words):
            addition = len(words[end]) + (1 if end > start else 0)
            if size + addition > max_chars and end > start:
                break
            size += addition
            end += 1
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        overlap = 0
        next_start = end
        while next_start > start and overlap < overlap_chars:
            next_start -= 1
            overlap += len(words[next_start]) + 1
        start = next_start if next_start > start else end
    return chunks


def document_id(source_id: str, content_hash: str):
    return hashlib.sha256(f"{source_id}:{content_hash}".encode()).hexdigest()


def chunk_id(document_id_value: str, index: int):
    return hashlib.sha256(f"{document_id_value}:{index}".encode()).hexdigest()


def upsert_document(connection: sqlite3.Connection, source: Source, document: dict):
    initialize_knowledge_base(connection)
    retrieved_at = document.get("retrieved_at", utc_now())
    content_hash = document["content_hash"]
    current_document_id = document_id(source.source_id, content_hash)
    connection.execute(
        """
        INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            organization=excluded.organization, url=excluded.url,
            title=excluded.title, language=excluded.language,
            content_type=excluded.content_type, source_type=excluded.source_type,
            publication_date=excluded.publication_date,
            retrieved_at=excluded.retrieved_at, content_hash=excluded.content_hash
        """,
        (
            source.source_id,
            source.organization,
            source.url,
            source.title,
            source.language,
            source.content_type,
            source.source_type,
            source.publication_date,
            retrieved_at,
            content_hash,
        ),
    )
    existing = connection.execute(
        "SELECT 1 FROM documents WHERE document_id = ?", (current_document_id,)
    ).fetchone()
    connection.execute(
        "UPDATE documents SET active = 0 WHERE source_id = ? AND document_id != ?",
        (source.source_id, current_document_id),
    )
    connection.execute(
        """
        INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(document_id) DO UPDATE SET
            title=excluded.title, retrieved_at=excluded.retrieved_at, active=1
        """,
        (
            current_document_id,
            source.source_id,
            source.title,
            document["text"],
            content_hash,
            source.publication_date,
            retrieved_at,
        ),
    )
    if existing is None:
        chunks = chunk_text(document["text"])
        rows = [
            (
                chunk_id(current_document_id, index),
                current_document_id,
                index,
                text,
                len(text),
            )
            for index, text in enumerate(chunks)
        ]
        connection.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", rows)
        connection.executemany(
            "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?)",
            [
                (row[0], row[3], source.title, source.organization, source.language)
                for row in rows
            ],
        )
    else:
        chunks = connection.execute(
            "SELECT chunk_id FROM chunks WHERE document_id = ?", (current_document_id,)
        ).fetchall()
    connection.commit()
    return {"document_id": current_document_id, "chunks": len(chunks)}


def ingest_source(connection: sqlite3.Connection, source: Source, session=None):
    return upsert_document(connection, source, fetch_source_document(source, session=session))


def ingest_corpus(connection: sqlite3.Connection, session=None):
    results = []
    for source in SOURCES:
        result = ingest_source(connection, source, session=session)
        results.append({"source_id": source.source_id, **result})
    return results


def load_evaluation_questions(connection: sqlite3.Connection, path: Path):
    initialize_knowledge_base(connection)
    questions = json.loads(path.read_text(encoding="utf-8"))
    connection.executemany(
        """
        INSERT INTO evaluation_questions VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(question_id) DO UPDATE SET
            question=excluded.question, language=excluded.language,
            expected_source_id=excluded.expected_source_id,
            category=excluded.category
        """,
        [
            (
                item["question_id"],
                item["question"],
                item["language"],
                item["expected_source_id"],
                item["category"],
            )
            for item in questions
        ],
    )
    connection.commit()
    return len(questions)
