import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from app.db_init import connect, initialize_application_schema
from app.session_fixtures import (
    SessionFixtureError,
    export_sessions,
    import_manifest,
    load_fixture,
    sha256_file,
    validate_fixture,
)


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "evaluation" / "session_fixtures"
MANIFEST = FIXTURE_DIRECTORY / "manifest.json"


def test_committed_session_fixture_manifest_matches_safe_files():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert {entry["record_origin"] for entry in manifest["fixtures"]} == {
        "synthetic_fixture",
        "published_test",
    }
    assert sum(entry["conversations"] for entry in manifest["fixtures"]) == 6
    assert sum(entry["feedback"] for entry in manifest["fixtures"]) == 9
    for entry in manifest["fixtures"]:
        path = FIXTURE_DIRECTORY / entry["path"]
        assert sha256_file(path) == entry["sha256"]
        validate_fixture(load_fixture(path))


def test_session_fixture_rejects_credentials_and_personal_identifiers():
    fixture = load_fixture(FIXTURE_DIRECTORY / "synthetic_sessions.json")
    unsafe = deepcopy(fixture)
    unsafe["conversations"][0]["comment"] = "Contact person@example.test"

    with pytest.raises(SessionFixtureError, match="sensitive data"):
        validate_fixture(unsafe)

    unsafe = deepcopy(fixture)
    unsafe["conversations"][0]["prompt"] = "authorization=private-value"
    with pytest.raises(SessionFixtureError, match="sensitive data"):
        validate_fixture(unsafe)


def test_session_fixture_manifest_detects_tampering(tmp_path):
    for path in FIXTURE_DIRECTORY.glob("*.json"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    fixture = tmp_path / "published_test_sessions.json"
    fixture.write_text(fixture.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(SessionFixtureError, match="checksum"):
        import_manifest(object(), tmp_path / "manifest.json")


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_session_fixtures_import_idempotently_and_export_full_records(tmp_path):
    published = load_fixture(FIXTURE_DIRECTORY / "published_test_sessions.json")
    all_fixture_ids = [
        conversation["fixture_id"]
        for name in ("published_test_sessions.json", "synthetic_sessions.json")
        for conversation in load_fixture(FIXTURE_DIRECTORY / name)["conversations"]
    ]
    first_published = published["conversations"][0]

    with connect(os.environ["TEST_DATABASE_URL"]) as connection:
        initialize_application_schema(connection)
        connection.execute(
            "DELETE FROM conversations WHERE fixture_id = ANY(%s)", (all_fixture_ids,)
        )
        connection.execute(
            "DELETE FROM conversations WHERE question = %s AND timestamp = %s",
            (first_published["question"], first_published["timestamp"]),
        )
        promoted_id = connection.execute(
            """
            INSERT INTO conversations (
                question, answer, route, language, retrieval_backend, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                first_published["question"],
                first_published["answer"],
                first_published["route"],
                first_published["language"],
                first_published["retrieval_backend"],
                first_published["timestamp"],
            ),
        ).fetchone()[0]
        connection.commit()

        first = import_manifest(connection, MANIFEST)
        second = import_manifest(connection, MANIFEST)
        rows = connection.execute(
            """
            SELECT fixture_id, record_origin, id
            FROM conversations WHERE fixture_id = ANY(%s)
            """,
            (all_fixture_ids,),
        ).fetchall()
        fixture_ids = {row[0] for row in rows}
        origins = {row[1] for row in rows}
        promoted = next(
            row for row in rows if row[0] == first_published["fixture_id"]
        )
        feedback_count = connection.execute(
            """
            SELECT COUNT(*) FROM feedback
            WHERE conversation_id = ANY(
                SELECT id FROM conversations WHERE fixture_id = ANY(%s)
            )
            """,
            (all_fixture_ids,),
        ).fetchone()[0]
        export_path = tmp_path / "round-trip.json"
        export_sessions(
            connection,
            [promoted_id],
            output=export_path,
            source_commit="test-commit",
            snapshot_id="test-snapshot",
        )
        connection.execute(
            "DELETE FROM conversations WHERE fixture_id = ANY(%s)", (all_fixture_ids,)
        )
        connection.commit()

    exported = load_fixture(export_path)
    assert first == second == {
        "fixture_sets": 2,
        "conversations": 6,
        "feedback": 9,
    }
    assert fixture_ids == set(all_fixture_ids)
    assert origins == {"synthetic_fixture", "published_test"}
    assert promoted[2] == promoted_id
    assert feedback_count == 9
    assert exported["conversations"][0]["prompt"] == first_published["prompt"]
    assert exported["conversations"][0]["instructions"] == first_published["instructions"]
