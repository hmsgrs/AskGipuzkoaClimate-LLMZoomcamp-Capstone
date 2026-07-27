"""Export and load portable, corpus-bound float32 document embeddings."""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import struct
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pgvector import Vector

from app.db_init import embedding_dimensions, embedding_model
from app.openai_client import OpenAIEmbeddingClient


EMBEDDING_EXPORT_SCHEMA_VERSION = 1


class PortableEmbeddingError(ValueError):
    """Raised when an embedding export is incompatible or unsafe."""


def active_chunks(database: Path):
    database = Path(database)
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
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
    finally:
        connection.close()
    return [
        {
            "chunk_id": row["chunk_id"],
            "text": row["text"],
            "text_hash": hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
        }
        for row in rows
    ]


def corpus_digest(chunks):
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk["chunk_id"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(chunk["text_hash"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _pack_vector(vector, dimensions):
    if len(vector) != dimensions:
        raise PortableEmbeddingError(
            f"Embedding has {len(vector)} dimensions; expected {dimensions}"
        )
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise PortableEmbeddingError("Embedding contains a non-finite value")
    return struct.pack(f"<{dimensions}f", *values)


def _unpack_vector(blob, dimensions):
    expected_size = dimensions * 4
    if len(blob) != expected_size:
        raise PortableEmbeddingError(
            f"Embedding blob has {len(blob)} bytes; expected {expected_size}"
        )
    vector = list(struct.unpack(f"<{dimensions}f", blob))
    if not all(math.isfinite(value) for value in vector):
        raise PortableEmbeddingError("Embedding contains a non-finite value")
    return vector


def export_embeddings(
    database: Path,
    output: Path,
    *,
    embedding_client=None,
    batch_size: int = 100,
):
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    chunks = active_chunks(database)
    if not chunks:
        raise PortableEmbeddingError("The corpus has no active chunks")
    client = embedding_client or OpenAIEmbeddingClient()
    model = getattr(client, "model", embedding_model())
    dimensions = getattr(client, "dimensions", embedding_dimensions())
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                CREATE TABLE metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    embedding_model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_count INTEGER NOT NULL,
                    corpus_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE embeddings (
                    chunk_id TEXT PRIMARY KEY,
                    text_hash TEXT NOT NULL,
                    vector_f32 BLOB NOT NULL
                );
                """
            )
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start : start + batch_size]
                vectors = client.embed([chunk["text"] for chunk in batch])
                if len(vectors) != len(batch):
                    raise PortableEmbeddingError(
                        "Embedding provider returned an unexpected vector count"
                    )
                connection.executemany(
                    "INSERT INTO embeddings VALUES (?, ?, ?)",
                    [
                        (
                            chunk["chunk_id"],
                            chunk["text_hash"],
                            _pack_vector(vector, dimensions),
                        )
                        for chunk, vector in zip(batch, vectors, strict=True)
                    ],
                )
            connection.execute(
                "INSERT INTO metadata VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    EMBEDDING_EXPORT_SCHEMA_VERSION,
                    model,
                    dimensions,
                    len(chunks),
                    corpus_digest(chunks),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()
            connection.execute("VACUUM")
        finally:
            connection.close()
        validate_export(temporary, database)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return inspect_export(output)


def _read_export(path: Path):
    path = Path(path)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise PortableEmbeddingError(f"Embedding export integrity failed: {integrity}")
        metadata_rows = connection.execute("SELECT * FROM metadata").fetchall()
        if len(metadata_rows) != 1:
            raise PortableEmbeddingError("Embedding export must have one metadata row")
        metadata = dict(metadata_rows[0])
        rows = connection.execute(
            "SELECT chunk_id, text_hash, vector_f32 FROM embeddings ORDER BY chunk_id"
        ).fetchall()
    except sqlite3.Error as error:
        raise PortableEmbeddingError(f"Invalid embedding export: {error}") from error
    finally:
        connection.close()
    return metadata, rows


def validate_export(path: Path, database: Path):
    metadata, rows = _read_export(path)
    if metadata["schema_version"] != EMBEDDING_EXPORT_SCHEMA_VERSION:
        raise PortableEmbeddingError("Unsupported embedding export schema version")
    if metadata["dimensions"] <= 0:
        raise PortableEmbeddingError("Embedding dimensions must be positive")
    chunks = active_chunks(database)
    expected = {chunk["chunk_id"]: chunk["text_hash"] for chunk in chunks}
    actual = {row["chunk_id"]: row["text_hash"] for row in rows}
    if len(actual) != len(rows):
        raise PortableEmbeddingError("Embedding export contains duplicate chunk IDs")
    if actual != expected:
        raise PortableEmbeddingError("Embedding export does not match the active corpus")
    if metadata["vector_count"] != len(rows) or len(rows) != len(chunks):
        raise PortableEmbeddingError("Embedding export vector count is inconsistent")
    if metadata["corpus_digest"] != corpus_digest(chunks):
        raise PortableEmbeddingError("Embedding export corpus digest does not match")
    for row in rows:
        _unpack_vector(row["vector_f32"], metadata["dimensions"])
    return {
        "schema_version": metadata["schema_version"],
        "embedding_model": metadata["embedding_model"],
        "dimensions": metadata["dimensions"],
        "vector_count": metadata["vector_count"],
        "corpus_digest": metadata["corpus_digest"],
        "created_at": metadata["created_at"],
    }


def inspect_export(path: Path):
    metadata, rows = _read_export(path)
    return {
        "path": str(path),
        "schema_version": metadata["schema_version"],
        "embedding_model": metadata["embedding_model"],
        "dimensions": metadata["dimensions"],
        "vector_count": len(rows),
        "corpus_digest": metadata["corpus_digest"],
        "created_at": metadata["created_at"],
    }


def import_embeddings(path: Path, database: Path, postgres_connection):
    metadata = validate_export(path, database)
    _, rows = _read_export(path)
    model = metadata["embedding_model"]
    dimensions = metadata["dimensions"]
    values = [
        (
            row["chunk_id"],
            model,
            row["text_hash"],
            Vector(_unpack_vector(row["vector_f32"], dimensions)).to_text(),
        )
        for row in rows
    ]
    with postgres_connection.transaction():
        with postgres_connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO chunk_embeddings
                    (chunk_id, embedding_model, text_hash, embedding)
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (chunk_id, embedding_model) DO UPDATE SET
                    text_hash=excluded.text_hash,
                    embedding=excluded.embedding,
                    embedded_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        active_ids = [row["chunk_id"] for row in rows]
        postgres_connection.execute(
            """
            DELETE FROM chunk_embeddings
            WHERE embedding_model = %s AND NOT (chunk_id = ANY(%s))
            """,
            (model, active_ids),
        )
    count = postgres_connection.execute(
        "SELECT COUNT(*) FROM chunk_embeddings WHERE embedding_model = %s", (model,)
    ).fetchone()[0]
    if count != len(rows):
        raise PortableEmbeddingError("PostgreSQL embedding count does not match export")
    return {**metadata, "imported": count}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--batch-size", type=int, default=100)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--artifact", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "export":
        result = export_embeddings(
            args.database, args.output, batch_size=args.batch_size
        )
    else:
        result = inspect_export(args.artifact)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
