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

The default reviewer path requires Docker and one OpenAI key. Compose verifies the committed snapshot, imports document embeddings without an API call, provisions PostgreSQL, and starts Streamlit and Grafana:

```bash
OPENAI_API_KEY=your-key docker compose up --build --wait
```

For a CLI answer:

```bash
OPENAI_API_KEY=dummy docker compose exec streamlit python -m app.assistant \
  "Why is climate change a global problem?"
```

The reviewer backend is pgvector. The committed `text-embedding-3-small` document vectors are seeded locally; OpenAI is called only for the query embedding and generated answer. SQLite FTS5 remains the lexical baseline for evaluation.

## Routing

`app/assistant.py` uses deterministic rules so routing is reproducible and testable:

- Immediate-danger language or `112` selects the emergency route and bypasses the LLM.
- Current weather, forecast, temperature, warning, and alert terms select cached official weather.
- Preparedness and recommendation questions select the knowledge base even if they contain the word "weather".
- All remaining questions select the knowledge base.

The weather repository never makes provider calls per question. In the default `DATA_MODE=snapshot`, it treats forecasts and warnings as historical, uses the snapshot effective date when interpreting relative dates through "day after tomorrow", filters municipality and warning-area aliases, includes archived warnings with a stale marker, and directs users to current official channels. In an explicitly configured refresh deployment, warning queries exclude rows older than `LIVE_DATA_MAX_AGE_HOURS`. Both modes retain distinct request scopes and official source URLs and retrieval times.

## Citation Contract

Retrieved chunks are grouped by official source and labelled `[S1]`, `[S2]`, and so on in the prompt. The model is allowed to cite only those labels. After generation, the application extracts citation labels and displays only the source cards actually cited; it never trusts a model-generated URL. An answer with no citation, an unknown label, or a model-generated URL fails closed and is replaced with a safe fallback rather than shown as grounded output.

The selected prompt instructs the model to answer in the question's language, use only official context, minimize the source set, separate live weather from climate history, identify stale data, and direct immediate danger to `112`. Responses API calls set `store=False`; structured judge calls use the same setting.

## Persistence And Feedback

`app.runtime_init` creates the vector, conversation, and feedback schemas before the application starts. It verifies and imports four synthetic sessions plus two approved real test sessions so a fresh clone has auditable monitoring data. `record_origin` distinguishes `synthetic_fixture`, `published_test`, and future `live` records. Streamlit uses a least-privilege writer that cannot alter document vectors or create schema objects. Storage is disabled by default. If the user explicitly enables it before asking, the application stores the question, answer, grounded prompt, route, language, backend, model, tokens, latency, estimated cost, citations, citation-contract status, and UTC time. Feedback and the optional LLM relevance verdict are available only for a stored conversation.

The application does not persist IP addresses or browser identifiers. Consented live questions, answers, prompts, and comments remain in local PostgreSQL until manually deleted. They are not published automatically. The two records labelled `published_test` were explicitly selected, privacy-scanned, reviewed, and committed with their full stored content. A hosted deployment still requires an explicit privacy and retention policy.

## Safety Boundary

The application is informational. It does not issue alerts or replace Euskalmet, AEMET, emergency authorities, or `112`. Immediate-danger questions receive deterministic `112` guidance without waiting for retrieval or model generation.
