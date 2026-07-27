"""Export, validate, and idempotently import auditable session fixtures."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db_init import connect


FIXTURE_SCHEMA_VERSION = 1
ALLOWED_ORIGINS = {"synthetic_fixture", "published_test"}
CONVERSATION_FIELDS = (
    "question",
    "answer",
    "route",
    "language",
    "retrieval_backend",
    "model",
    "instructions",
    "prompt",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "response_time",
    "cost",
    "citations",
    "status",
    "timestamp",
)
FEEDBACK_FIELDS = (
    "source",
    "relevance",
    "explanation",
    "score",
    "comment",
    "timestamp",
)
SENSITIVE_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{16,}", re.IGNORECASE),
    "assigned_secret": re.compile(
        r"\b(?:api[_-]?key|password|secret|authorization)\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
    "signed_url": re.compile(
        r"[?&](?:token|signature|sig|api[_-]?key)=[^&\s]+", re.IGNORECASE
    ),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


class SessionFixtureError(ValueError):
    """Raised when session evidence is unsafe or incompatible."""


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _walk_strings(value, path="root"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def privacy_findings(value):
    findings = []
    for path, text in _walk_strings(value):
        for name, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                findings.append({"path": path, "type": name})
    return findings


def validate_fixture(fixture):
    if fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise SessionFixtureError("Unsupported session fixture schema version")
    origin = fixture.get("record_origin")
    if origin not in ALLOWED_ORIGINS:
        raise SessionFixtureError("Invalid fixture record origin")
    conversations = fixture.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        raise SessionFixtureError("Session fixture must contain conversations")
    fixture_ids = set()
    for conversation in conversations:
        fixture_id = conversation.get("fixture_id")
        if not fixture_id or fixture_id in fixture_ids:
            raise SessionFixtureError("Fixture IDs must be present and unique")
        fixture_ids.add(fixture_id)
        missing = [field for field in CONVERSATION_FIELDS if field not in conversation]
        if missing:
            raise SessionFixtureError(f"Conversation is missing fields: {missing}")
        for feedback in conversation.get("feedback", []):
            missing = [field for field in FEEDBACK_FIELDS if field not in feedback]
            if missing:
                raise SessionFixtureError(f"Feedback is missing fields: {missing}")
    findings = privacy_findings(fixture)
    if findings:
        raise SessionFixtureError(f"Session fixture contains sensitive data: {findings}")
    return {
        "record_origin": origin,
        "conversations": len(conversations),
        "feedback": sum(len(item.get("feedback", [])) for item in conversations),
    }


def load_fixture(path: Path):
    try:
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SessionFixtureError(f"Invalid session fixture: {error}") from error
    validate_fixture(fixture)
    return fixture


def export_sessions(
    connection,
    conversation_ids,
    *,
    output: Path,
    source_commit: str,
    snapshot_id: str,
):
    conversation_ids = sorted(set(int(value) for value in conversation_ids))
    if not conversation_ids:
        raise ValueError("At least one conversation ID is required")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, question, answer, route, language, retrieval_backend,
                   model, instructions, prompt, prompt_tokens, completion_tokens,
                   total_tokens, response_time, cost, citations, status, timestamp
            FROM conversations
            WHERE id = ANY(%s)
            ORDER BY id
            """,
            (conversation_ids,),
        )
        rows = cursor.fetchall()
        if [row["id"] for row in rows] != conversation_ids:
            raise SessionFixtureError("One or more selected conversations do not exist")
        cursor.execute(
            """
            SELECT id, conversation_id, source, relevance, explanation, score,
                   comment, timestamp
            FROM feedback
            WHERE conversation_id = ANY(%s)
            ORDER BY conversation_id, source
            """,
            (conversation_ids,),
        )
        feedback_rows = cursor.fetchall()
    feedback_by_conversation = {identifier: [] for identifier in conversation_ids}
    for feedback in feedback_rows:
        feedback_by_conversation[feedback["conversation_id"]].append(
            {
                "source_feedback_id": feedback["id"],
                **{
                    field: _json_value(feedback[field])
                    for field in FEEDBACK_FIELDS
                },
            }
        )
    conversations = []
    for row in rows:
        identity = hashlib.sha256(
            f"{row['id']}\0{row['question']}\0{row['timestamp'].isoformat()}".encode()
        ).hexdigest()[:16]
        conversations.append(
            {
                "fixture_id": f"published-test-{identity}",
                "source_conversation_id": row["id"],
                **{field: _json_value(row[field]) for field in CONVERSATION_FIELDS},
                "feedback": feedback_by_conversation[row["id"]],
            }
        )
    fixture = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_set": "selected_real_test_sessions",
        "record_origin": "published_test",
        "snapshot_id": snapshot_id,
        "source_commit": source_commit,
        "exported_at": datetime.now(UTC).isoformat(),
        "conversations": conversations,
    }
    validate_fixture(fixture)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**validate_fixture(fixture), "path": str(output)}


def _upsert_conversation(connection, conversation, origin):
    existing = connection.execute(
        "SELECT id FROM conversations WHERE fixture_id = %s",
        (conversation["fixture_id"],),
    ).fetchone()
    if existing is None and origin == "published_test":
        existing = connection.execute(
            """
            SELECT id FROM conversations
            WHERE fixture_id IS NULL AND question = %s AND timestamp = %s
            """,
            (conversation["question"], conversation["timestamp"]),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE conversations
                SET fixture_id = %s, record_origin = %s
                WHERE id = %s
                """,
                (conversation["fixture_id"], origin, existing[0]),
            )
    values = [
        Jsonb(conversation[field]) if field == "citations" else conversation[field]
        for field in CONVERSATION_FIELDS
    ]
    row = connection.execute(
        f"""
        INSERT INTO conversations (
            fixture_id, record_origin, {', '.join(CONVERSATION_FIELDS)}
        ) VALUES (%s, %s, {', '.join(['%s'] * len(CONVERSATION_FIELDS))})
        ON CONFLICT (fixture_id) WHERE fixture_id IS NOT NULL DO UPDATE SET
            record_origin=excluded.record_origin,
            {', '.join(f'{field}=excluded.{field}' for field in CONVERSATION_FIELDS)}
        RETURNING id
        """,
        (conversation["fixture_id"], origin, *values),
    ).fetchone()
    return row[0]


def import_fixture(connection, fixture):
    summary = validate_fixture(fixture)
    origin = fixture["record_origin"]
    with connection.transaction():
        for conversation in fixture["conversations"]:
            conversation_id = _upsert_conversation(connection, conversation, origin)
            for feedback in conversation.get("feedback", []):
                connection.execute(
                    """
                    INSERT INTO feedback (
                        conversation_id, source, relevance, explanation,
                        score, comment, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (conversation_id, source) DO UPDATE SET
                        relevance=excluded.relevance,
                        explanation=excluded.explanation,
                        score=excluded.score,
                        comment=excluded.comment,
                        timestamp=excluded.timestamp
                    """,
                    (
                        conversation_id,
                        *(feedback[field] for field in FEEDBACK_FIELDS),
                    ),
                )
    return summary


def sha256_file(path: Path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_manifest(directory: Path, output: Path):
    directory = Path(directory)
    fixtures = []
    for path in sorted(directory.glob("*_sessions.json")):
        fixture = load_fixture(path)
        summary = validate_fixture(fixture)
        fixtures.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                **summary,
            }
        )
    if not fixtures:
        raise SessionFixtureError("No session fixtures were found")
    manifest = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixtures": fixtures,
    }
    Path(output).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def import_manifest(connection, manifest_path: Path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise SessionFixtureError("Unsupported fixture manifest version")
    imported = {"conversations": 0, "feedback": 0, "fixture_sets": 0}
    for entry in manifest.get("fixtures", []):
        path = manifest_path.parent / entry["path"]
        if sha256_file(path) != entry["sha256"]:
            raise SessionFixtureError(f"Fixture checksum does not match: {path.name}")
        fixture = load_fixture(path)
        summary = import_fixture(connection, fixture)
        if summary["record_origin"] != entry["record_origin"]:
            raise SessionFixtureError("Fixture origin does not match manifest")
        if summary["conversations"] != entry["conversations"]:
            raise SessionFixtureError("Fixture conversation count does not match manifest")
        if summary["feedback"] != entry["feedback"]:
            raise SessionFixtureError("Fixture feedback count does not match manifest")
        imported["conversations"] += summary["conversations"]
        imported["feedback"] += summary["feedback"]
        imported["fixture_sets"] += 1
    return imported


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--database-url", required=True)
    export.add_argument("--conversation-id", action="append", required=True, type=int)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--source-commit", required=True)
    export.add_argument("--snapshot-id", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--fixture", type=Path, required=True)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--directory", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "export":
        with connect(args.database_url) as connection:
            result = export_sessions(
                connection,
                args.conversation_id,
                output=args.output,
                source_commit=args.source_commit,
                snapshot_id=args.snapshot_id,
            )
    elif args.command == "validate":
        result = validate_fixture(load_fixture(args.fixture))
    else:
        result = build_manifest(args.directory, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
