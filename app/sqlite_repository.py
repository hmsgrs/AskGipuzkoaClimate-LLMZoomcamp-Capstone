"""SQLite FTS5 retrieval for canonical knowledge-base chunks."""

import re
import sqlite3
from pathlib import Path

from app.knowledge_base import DEFAULT_DATABASE, initialize_knowledge_base


def fts_query(question: str):
    tokens = re.findall(r"\w+", question.casefold(), flags=re.UNICODE)
    return " OR ".join(f'"{token}"' for token in tokens if len(token) > 1)


class SQLiteRepository:
    def __init__(self, database: Path = DEFAULT_DATABASE):
        self.database = database

    def search(self, question: str, limit: int = 5):
        query = fts_query(question)
        if not query:
            return []
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            initialize_knowledge_base(connection)
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.text, d.document_id, d.title,
                       s.source_id, s.organization, s.url, s.language,
                       s.publication_date, bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                JOIN documents d ON d.document_id = c.document_id
                JOIN sources s ON s.source_id = d.source_id
                WHERE chunks_fts MATCH ? AND d.active = 1
                ORDER BY score
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()
