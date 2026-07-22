from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ingest import get_database
from app.knowledge_base import upsert_document
from app.openai_client import OpenAIEmbeddingClient
from app.pgvector_repository import PgvectorRepository
from app.source_registry import Source


class FakeOpenAI:
    def __init__(self, vectors):
        self.vectors = vectors
        self.embeddings = self
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        data = [
            SimpleNamespace(index=index, embedding=vector)
            for index, vector in reversed(list(enumerate(self.vectors)))
        ]
        return SimpleNamespace(data=data)


def test_embedding_client_preserves_input_order_and_configuration():
    api = FakeOpenAI([[1.0, 0.0], [0.0, 1.0]])
    client = OpenAIEmbeddingClient(api, model="test-model", dimensions=2)

    assert client.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert api.request == {
        "input": ["first", "second"],
        "model": "test-model",
        "dimensions": 2,
    }


def test_embedding_client_rejects_wrong_dimensions():
    client = OpenAIEmbeddingClient(
        FakeOpenAI([[1.0]]), model="test-model", dimensions=2
    )

    with pytest.raises(RuntimeError, match="dimension does not match 2"):
        client.embed(["text"])


def test_pgvector_results_are_hydrated_in_vector_order(tmp_path: Path):
    database = tmp_path / "knowledge.sqlite"
    connection = get_database(database)
    source = Source(
        source_id="semantic-source",
        organization="Euskalmet",
        title="Semantic report",
        url="https://example.test/report",
        language="es",
        content_type="text/html",
        source_type="climate_history",
    )
    result = upsert_document(
        connection,
        source,
        {
            "text": "Condiciones climaticas del verano.",
            "content_hash": "semantic-hash",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
        },
    )
    chunk_id = connection.execute(
        "SELECT chunk_id FROM chunks WHERE document_id = ?", (result["document_id"],)
    ).fetchone()[0]
    connection.close()

    repository = PgvectorRepository(
        database,
        embedding_client=SimpleNamespace(model="test-model"),
    )
    hydrated = repository._hydrate([(chunk_id, 0.93), ("missing", 0.5)])

    assert len(hydrated) == 1
    assert hydrated[0]["chunk_id"] == chunk_id
    assert hydrated[0]["source_id"] == "semantic-source"
    assert hydrated[0]["score"] == 0.93
