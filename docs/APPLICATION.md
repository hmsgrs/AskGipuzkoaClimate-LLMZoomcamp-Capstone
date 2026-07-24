# RAG Application

The application follows the LLM Zoomcamp course flow while replacing `minsearch` with the project's SQLite and pgvector repositories:

```text
question
  -> deterministic route and language detection
  -> pgvector, SQLite FTS5, or cached official weather retrieval
  -> bounded context with application-assigned [S#] labels
  -> OpenAI Responses API
  -> answer, cited source cards, usage and latency
  -> optional, consented PostgreSQL conversation and feedback
```

## Run Locally

For the default audit path, verify a published snapshot and use SQLite FTS5. Set `OPENAI_API_KEY` for new answers and point `SQLITE_DATABASE` at the snapshot database. PostgreSQL is only required for pgvector or consented monitoring.

```bash
uv sync --group dev
uv run python -m app.snapshot verify --snapshot data/snapshots/SNAPSHOT_ID
DATA_MODE=snapshot \
SQLITE_DATABASE=data/snapshots/SNAPSHOT_ID/snapshot.sqlite \
RETRIEVAL_BACKEND=sqlite_fts5 \
uv run streamlit run app/streamlit_app.py
```

For a CLI answer:

```bash
uv run python -m app.assistant \
  "Why is climate change a global problem?"
```

The default retrieval backend is pgvector. Set `RETRIEVAL_BACKEND=sqlite_fts5` to demonstrate the lexical baseline.

## Routing

`app/assistant.py` uses deterministic rules so routing is reproducible and testable:

- Immediate-danger language or `112` selects the emergency route and bypasses the LLM.
- Current weather, forecast, temperature, warning, and alert terms select cached official weather.
- Preparedness and recommendation questions select the knowledge base even if they contain the word "weather".
- All remaining questions select the knowledge base.

The weather repository never makes provider calls per question. In the default `DATA_MODE=snapshot`, it treats forecasts and warnings as historical, uses the snapshot acquisition date when interpreting "today" or "tomorrow", includes archived warnings with a stale marker, and directs users to current official channels. In an explicitly configured refresh deployment, warning queries exclude rows older than `LIVE_DATA_MAX_AGE_HOURS`. Both modes deduplicate repeated provider payloads and retain official source URLs and retrieval times.

## Citation Contract

Retrieved chunks are grouped by official source and labelled `[S1]`, `[S2]`, and so on in the prompt. The model is allowed to cite only those labels. After generation, the application extracts citation labels and displays only the source cards actually cited; it never trusts a model-generated URL. An answer with no citation, an unknown label, or a model-generated URL fails closed and is replaced with a safe fallback rather than shown as grounded output.

The selected prompt instructs the model to answer in the question's language, use only official context, minimize the source set, separate live weather from climate history, identify stale data, and direct immediate danger to `112`. Responses API calls set `store=False`; structured judge calls use the same setting.

## Persistence And Feedback

`app/db_init.py` creates idempotent `conversations` and `feedback` tables in PostgreSQL. Streamlit storage is disabled by default. If the user explicitly enables it before asking, the application stores the question, answer, grounded prompt, route, language, backend, model, tokens, latency, estimated cost, citations, citation-contract status, and UTC time. Feedback and the optional LLM relevance verdict are available only for a stored conversation.

The application does not persist IP addresses or browser identifiers. Consented questions, answers, prompts, and comments remain in local PostgreSQL until manually deleted, so a hosted deployment still requires an explicit privacy and retention policy.

## Safety Boundary

The application is informational. It does not issue alerts or replace Euskalmet, AEMET, emergency authorities, or `112`. Immediate-danger questions receive deterministic `112` guidance without waiting for retrieval or model generation.
