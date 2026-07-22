"""Synchronize active SQLite chunks into PostgreSQL with OpenAI embeddings."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pgvector import Vector

from app.db_init import connect, embedding_model, initialize_pgvector
from app.knowledge_base import DEFAULT_DATABASE
from app.openai_client import OpenAIEmbeddingClient


def active_chunks(database: Path = DEFAULT_DATABASE):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.chunk_id, c.text
            FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            WHERE d.active = 1
            ORDER BY c.chunk_id
            """
        ).fetchall()
        return [
            {
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "text_hash": hashlib.sha256(row["text"].encode()).hexdigest(),
            }
            for row in rows
        ]
    finally:
        connection.close()


def sync_embeddings(
    sqlite_database: Path = DEFAULT_DATABASE,
    postgres_connection=None,
    embedding_client=None,
    batch_size: int = 100,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    client = embedding_client or OpenAIEmbeddingClient()
    model = getattr(client, "model", embedding_model())
    chunks = active_chunks(sqlite_database)
    owns_connection = postgres_connection is None
    connection = postgres_connection or connect()
    try:
        initialize_pgvector(connection, getattr(client, "dimensions", None))
        existing_rows = connection.execute(
            "SELECT chunk_id, text_hash FROM chunk_embeddings WHERE embedding_model = %s",
            (model,),
        ).fetchall()
        existing = {row[0]: row[1] for row in existing_rows}
        pending = [
            chunk
            for chunk in chunks
            if existing.get(chunk["chunk_id"]) != chunk["text_hash"]
        ]
        embedded = 0
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = client.embed([chunk["text"] for chunk in batch])
            now = datetime.now(UTC)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO chunk_embeddings
                        (chunk_id, embedding_model, text_hash, embedding, embedded_at)
                    VALUES (%s, %s, %s, %s::vector, %s)
                    ON CONFLICT (chunk_id, embedding_model) DO UPDATE SET
                        text_hash=excluded.text_hash,
                        embedding=excluded.embedding,
                        embedded_at=excluded.embedded_at
                    """,
                    [
                        (
                            chunk["chunk_id"],
                            model,
                            chunk["text_hash"],
                            Vector(vector).to_text(),
                            now,
                        )
                        for chunk, vector in zip(batch, vectors, strict=True)
                    ],
                )
            connection.commit()
            embedded += len(batch)

        active_ids = [chunk["chunk_id"] for chunk in chunks]
        if active_ids:
            removed = connection.execute(
                """
                DELETE FROM chunk_embeddings
                WHERE embedding_model = %s AND NOT (chunk_id = ANY(%s))
                """,
                (model, active_ids),
            ).rowcount
        else:
            removed = connection.execute(
                "DELETE FROM chunk_embeddings WHERE embedding_model = %s", (model,)
            ).rowcount
        connection.commit()
        return {
            "model": model,
            "active_chunks": len(chunks),
            "existing_embeddings": len(existing),
            "embedded": embedded,
            "removed": removed,
        }
    finally:
        if owns_connection:
            connection.close()
