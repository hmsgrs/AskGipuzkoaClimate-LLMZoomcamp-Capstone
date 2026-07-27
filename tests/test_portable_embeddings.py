import sqlite3
from pathlib import Path

import pytest

from app.ingest import get_database
from app.knowledge_base import upsert_document
from app.portable_embeddings import (
    PortableEmbeddingError,
    export_embeddings,
    inspect_export,
    validate_export,
)
from app.source_registry import Source


class FakeEmbeddingClient:
    model = "portable-test-model"
    dimensions = 3

    def embed(self, texts):
        return [[float(index + 1), 0.5, -0.5] for index, _ in enumerate(texts)]


def corpus(path: Path):
    connection = get_database(path)
    source = Source(
        "test-source",
        "Official Test",
        "Test source",
        "https://example.test/source",
        "en",
        "text/html",
        "climate_history",
    )
    upsert_document(
        connection,
        source,
        {
            "text": "First grounded sentence. Second grounded sentence.",
            "content_hash": "portable-test-content",
            "retrieved_at": "2026-07-22T00:00:00+00:00",
        },
    )
    connection.close()


def test_portable_embedding_export_is_bound_to_corpus(tmp_path):
    database = tmp_path / "corpus.sqlite"
    artifact = tmp_path / "embeddings.sqlite"
    corpus(database)

    result = export_embeddings(
        database, artifact, embedding_client=FakeEmbeddingClient(), batch_size=1
    )
    validation = validate_export(artifact, database)

    assert result["embedding_model"] == "portable-test-model"
    assert result["dimensions"] == 3
    assert result["vector_count"] > 0
    assert validation["corpus_digest"] == result["corpus_digest"]
    assert inspect_export(artifact)["vector_count"] == result["vector_count"]


def test_portable_embedding_export_rejects_tampering(tmp_path):
    database = tmp_path / "corpus.sqlite"
    artifact = tmp_path / "embeddings.sqlite"
    corpus(database)
    export_embeddings(database, artifact, embedding_client=FakeEmbeddingClient())
    connection = sqlite3.connect(artifact)
    connection.execute(
        "UPDATE embeddings SET vector_f32 = ? WHERE chunk_id = "
        "(SELECT chunk_id FROM embeddings LIMIT 1)",
        (b"truncated",),
    )
    connection.commit()
    connection.close()

    with pytest.raises(PortableEmbeddingError, match="bytes"):
        validate_export(artifact, database)


def test_portable_embedding_export_rejects_changed_corpus(tmp_path):
    database = tmp_path / "corpus.sqlite"
    artifact = tmp_path / "embeddings.sqlite"
    corpus(database)
    export_embeddings(database, artifact, embedding_client=FakeEmbeddingClient())
    connection = sqlite3.connect(database)
    connection.execute("UPDATE chunks SET text = text || ' changed'")
    connection.commit()
    connection.close()

    with pytest.raises(PortableEmbeddingError, match="active corpus"):
        validate_export(artifact, database)
