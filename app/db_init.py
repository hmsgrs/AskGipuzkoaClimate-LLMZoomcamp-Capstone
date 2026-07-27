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
    vector_type = connection.execute(
        """
        SELECT format_type(attribute.atttypid, attribute.atttypmod)
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        WHERE relation.relname = 'chunk_embeddings'
          AND attribute.attname = 'embedding'
          AND attribute.attnum > 0
        """
    ).fetchone()[0]
    if vector_type != f"vector({dimensions})":
        connection.rollback()
        raise RuntimeError(
            f"Existing embedding column is {vector_type}; expected vector({dimensions})"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS chunk_embeddings_hnsw
        ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
        """
    )
    connection.commit()


def initialize_application_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id BIGSERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            route TEXT NOT NULL,
            language TEXT NOT NULL,
            retrieval_backend TEXT NOT NULL,
            model TEXT,
            instructions TEXT,
            prompt TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            response_time DOUBLE PRECISION NOT NULL DEFAULT 0,
            cost DOUBLE PRECISION NOT NULL DEFAULT 0,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'success',
            timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            source TEXT NOT NULL CHECK (source IN ('user', 'judge')),
            relevance TEXT CHECK (
                relevance IS NULL OR relevance IN (
                    'NON_RELEVANT', 'PARTLY_RELEVANT', 'RELEVANT'
                )
            ),
            explanation TEXT,
            score INTEGER CHECK (score IS NULL OR score IN (-1, 1)),
            comment TEXT,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (conversation_id, source)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS conversations_timestamp_idx ON conversations (timestamp)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS feedback_timestamp_idx ON feedback (timestamp)"
    )
    connection.commit()


def main():
    with connect() as connection:
        initialize_pgvector(connection)
        initialize_application_schema(connection)
    print("PostgreSQL schemas initialized")


if __name__ == "__main__":
    main()
