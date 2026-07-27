# Session Fixtures

The reviewer runtime separates three monitoring-record origins:

- `synthetic_fixture`: four designed bilingual examples covering knowledge, historical weather, emergency routing, feedback, and judge outcomes.
- `published_test`: two real test sessions explicitly selected for publication with their full stored prompts, instructions, answers, citations, metrics, comments, and verdicts.
- `live`: consented records created by the current local user. These remain only in the PostgreSQL Docker volume.

The committed files are in `evaluation/session_fixtures/`. `manifest.json` records each fixture's SHA-256 digest, origin, conversation count, and feedback count. `runtime-init` rejects a checksum, schema, count, privacy, or origin mismatch before Streamlit starts.

## Fresh Clone Behavior

A fresh PostgreSQL volume imports six conversations and nine feedback records. Repeated initialization updates the same stable fixture IDs and never creates duplicates. If a published test session still exists as a matching local live row, initialization promotes that row in place instead of inserting a copy.

New live sessions survive `docker compose up --build`, container recreation, and normal `docker compose down` because PostgreSQL uses a named volume. They are not transferred to another clone. `docker compose down --volumes` removes local live records; the next startup restores only committed fixtures.

## Publishing Selected Sessions

Publication is always explicit. Identify the conversation IDs, then export from the running application container:

```bash
OPENAI_API_KEY=dummy docker compose exec streamlit /bin/sh -c \
  'python -m app.session_fixtures export \
    --database-url "$DATABASE_URL" \
    --conversation-id ID_ONE \
    --conversation-id ID_TWO \
    --output /tmp/published_test_sessions.json \
    --source-commit SOURCE_COMMIT \
    --snapshot-id gipuzkoa-demo-2026-07-22'

docker cp \
  "$(OPENAI_API_KEY=dummy docker compose ps -q streamlit):/tmp/published_test_sessions.json" \
  evaluation/session_fixtures/published_test_sessions.json
```

The exporter rejects OpenAI keys, private keys, bearer tokens, assigned credentials, signed URLs, email addresses, and IP addresses. Review the full JSON content before committing it, especially questions, prompts, model instructions, answers, and free-text comments.

Regenerate and validate the fixture manifest:

```bash
uv run python -m app.session_fixtures validate \
  --fixture evaluation/session_fixtures/published_test_sessions.json

uv run python -m app.session_fixtures manifest \
  --directory evaluation/session_fixtures \
  --output evaluation/session_fixtures/manifest.json
```

Rebuild from a fresh volume to prove clone behavior:

```bash
OPENAI_API_KEY=dummy docker compose down --volumes
OPENAI_API_KEY=your-key docker compose up --build --wait
```

Never commit a PostgreSQL data volume, `.env`, provider credential, raw signed response, or unreviewed session export.
