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

Start PostgreSQL and ensure the local SQLite corpus and pgvector embeddings have been ingested. Set `OPENAI_API_KEY`, `DATABASE_URL`, and the application variables documented in `.env.example`.

```bash
uv sync --group dev
uv run python -m app.db_init
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

The weather repository serves snapshots refreshed by Kestra rather than making a provider API call for every user request. It prefers authenticated location forecasts for the requested local date and falls back to the public forecast table when no matching authenticated snapshot exists. Source cards expose retrieval timestamps and mark stale forecast snapshots. Warning queries exclude snapshots older than `LIVE_DATA_MAX_AGE_HOURS` and deduplicate repeated provider payloads rather than presenting old warnings as current evidence.

## Citation Contract

Retrieved chunks are grouped by official source and labelled `[S1]`, `[S2]`, and so on in the prompt. The model is allowed to cite only those labels. After generation, the application extracts citation labels and displays only the source cards actually cited; it never trusts a model-generated URL. An answer with no citation, an unknown label, or a model-generated URL fails closed and is replaced with a safe fallback rather than shown as grounded output.

The selected prompt instructs the model to answer in the question's language, use only official context, minimize the source set, separate live weather from climate history, identify stale data, and direct immediate danger to `112`. Responses API calls set `store=False`; structured judge calls use the same setting.

## Persistence And Feedback

`app/db_init.py` creates idempotent `conversations` and `feedback` tables in PostgreSQL. Streamlit storage is disabled by default. If the user explicitly enables it before asking, the application stores the question, answer, grounded prompt, route, language, backend, model, tokens, latency, estimated cost, citations, citation-contract status, and UTC time. Feedback and the optional LLM relevance verdict are available only for a stored conversation.

The application does not persist IP addresses or browser identifiers. Consented questions, answers, prompts, and comments remain in local PostgreSQL until manually deleted, so a hosted deployment still requires an explicit privacy and retention policy.

## Safety Boundary

The application is informational. It does not issue alerts or replace Euskalmet, AEMET, emergency authorities, or `112`. Immediate-danger questions receive deterministic `112` guidance without waiting for retrieval or model generation.
