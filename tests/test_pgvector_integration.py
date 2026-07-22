import os
from pathlib import Path

import pytest

from app.db_init import connect, initialize_pgvector
from app.embedding_sync import sync_embeddings
from app.ingest import get_database
from app.knowledge_base import upsert_document
from app.pgvector_repository import PgvectorRepository
from app.source_registry import Source


pytestmark = pytest.mark.integration


class FakeEmbeddingClient:
    model = "integration-test-model"
    dimensions = 1536

    def __init__(self):
        self.embedded_texts = []

    def embed(self, texts):
        self.embedded_texts.extend(texts)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            vector[0 if "verano" in text.casefold() else 1] = 1.0
            vectors.append(vector)
        return vectors


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_sync_is_idempotent_and_semantic_results_hydrate(tmp_path: Path):
    database = tmp_path / "knowledge.sqlite"
    sqlite_connection = get_database(database)
    summer = Source(
        "summer-source",
        "Euskalmet",
        "Verano",
        "https://example.test/summer",
        "es",
        "text/html",
        "climate_history",
    )
    winter = Source(
        "winter-source",
        "Euskalmet",
        "Invierno",
        "https://example.test/winter",
        "es",
        "text/html",
        "climate_history",
    )
    summer_result = upsert_document(
        sqlite_connection,
        summer,
        {
            "text": "El verano fue calido.",
            "content_hash": "summer-hash",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        },
    )
    upsert_document(
        sqlite_connection,
        winter,
        {
            "text": "El invierno fue frio.",
            "content_hash": "winter-hash",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        },
    )

    client = FakeEmbeddingClient()
    with connect(os.environ["TEST_DATABASE_URL"]) as postgres_connection:
        initialize_pgvector(postgres_connection, client.dimensions)
        postgres_connection.execute(
            "DELETE FROM chunk_embeddings WHERE embedding_model = %s", (client.model,)
        )
        postgres_connection.commit()

        first = sync_embeddings(database, postgres_connection, client, batch_size=1)
        assert first["embedded"] == 2
        assert len(client.embedded_texts) == 2

        second = sync_embeddings(database, postgres_connection, client, batch_size=1)
        assert second["embedded"] == 0
        assert len(client.embedded_texts) == 2

        repository = PgvectorRepository(database, postgres_connection, client)
        results = repository.search("verano", limit=2)
        assert results[0]["source_id"] == "summer-source"

        sqlite_connection.execute(
            "UPDATE documents SET active = 0 WHERE document_id = ?",
            (summer_result["document_id"],),
        )
        sqlite_connection.commit()
        third = sync_embeddings(database, postgres_connection, client, batch_size=1)
        assert third["removed"] == 1
        postgres_connection.execute(
            "DELETE FROM chunk_embeddings WHERE embedding_model = %s", (client.model,)
        )
        postgres_connection.commit()

    sqlite_connection.close()
