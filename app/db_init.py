"""Initialize PostgreSQL and pgvector for semantic retrieval."""

import os

import psycopg


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSIONS = 1536


def database_url():
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("Set DATABASE_URL before connecting to PostgreSQL")
    return value


def embedding_model():
    return os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def embedding_dimensions():
    value = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS", DEFAULT_EMBEDDING_DIMENSIONS))
    if value <= 0:
        raise ValueError("OPENAI_EMBEDDING_DIMENSIONS must be positive")
    return value


def connect(url: str | None = None):
    return psycopg.connect(url or database_url())


def initialize_pgvector(connection, dimensions: int | None = None):
    dimensions = dimensions or embedding_dimensions()
    if dimensions <= 0:
        raise ValueError("Embedding dimensions must be positive")
    connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_id TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            embedding VECTOR({dimensions}) NOT NULL,
            embedded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chunk_id, embedding_model)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS chunk_embeddings_hnsw
        ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
        """
    )
    connection.commit()


def main():
    with connect() as connection:
        initialize_pgvector(connection)
    print("pgvector schema initialized")


if __name__ == "__main__":
    main()
