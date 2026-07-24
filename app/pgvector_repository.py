"""Semantic retrieval through pgvector with SQLite citation hydration."""

import sqlite3
from pathlib import Path

from pgvector import Vector

from app.db_init import connect, embedding_model
from app.knowledge_base import DEFAULT_DATABASE
from app.openai_client import OpenAIEmbeddingClient
from app.snapshot import open_readonly_database


class PgvectorRepository:
    def __init__(
        self,
        sqlite_database: Path = DEFAULT_DATABASE,
        postgres_connection=None,
        embedding_client=None,
    ):
        self.sqlite_database = sqlite_database
        self.postgres_connection = postgres_connection
        self.embedding_client = embedding_client or OpenAIEmbeddingClient()

    def search(self, question: str, limit: int = 5):
        if limit <= 0:
            return []
        query_vector = self.embedding_client.embed([question])[0]
        model = getattr(self.embedding_client, "model", embedding_model())
        owns_connection = self.postgres_connection is None
        connection = self.postgres_connection or connect()
        try:
            vector = Vector(query_vector).to_text()
            vector_rows = connection.execute(
                """
                SELECT chunk_id, 1 - (embedding <=> %s::vector) AS score
                FROM chunk_embeddings
                WHERE embedding_model = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vector, model, vector, limit),
            ).fetchall()
        finally:
            if owns_connection:
                connection.close()
        return self._hydrate(vector_rows)

    def _hydrate(self, vector_rows):
        if not vector_rows:
            return []
        chunk_ids = [row[0] for row in vector_rows]
        placeholders = ",".join("?" for _ in chunk_ids)
        connection = open_readonly_database(Path(self.sqlite_database))
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                f"""
                SELECT c.chunk_id, c.text, d.document_id, d.title,
                       s.source_id, s.organization, s.url, s.language,
                       s.publication_date, s.retrieved_at, s.source_type,
                       s.content_type
                FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                JOIN sources s ON s.source_id = d.source_id
                WHERE c.chunk_id IN ({placeholders}) AND d.active = 1
                """,
                chunk_ids,
            ).fetchall()
        finally:
            connection.close()
        hydrated = {row["chunk_id"]: dict(row) for row in rows}
        results = []
        for chunk_id, score in vector_rows:
            if chunk_id in hydrated:
                results.append({**hydrated[chunk_id], "score": float(score)})
        return results
