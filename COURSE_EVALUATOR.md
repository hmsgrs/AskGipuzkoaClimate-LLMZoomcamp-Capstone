# Course Evaluator Guide

This document is the shortest path for evaluating the LLM Zoomcamp capstone project.
It explains how to run the complete showcase, where each course deliverable is
implemented, what is committed in the reviewer snapshot, and why Kestra is available
but not required for grading.

## Project At A Glance

Ask Gipuzkoa is a Spanish/English RAG assistant for weather, climate risk, and
emergency preparedness in Gipuzkoa, Spain. It combines:

- A verified SQLite snapshot containing official documents and historical weather data.
- PostgreSQL with pgvector for semantic retrieval.
- OpenAI for query embeddings, answer generation, and optional LLM-as-Judge feedback.
- Streamlit for the application interface.
- PostgreSQL persistence for consented interactions and feedback.
- Grafana for monitoring.
- Python ingestion commands and optional Kestra orchestration.

The application is informational. It is not an official warning service. Immediate
danger is routed deterministically to `112` guidance without waiting for an LLM call.

## Recommended Evaluation Path

### Requirements

- Docker with Docker Compose.
- An OpenAI API key for new semantic queries and generated answers.
- No Euskalmet, AEMET, CDS, Kestra, PostgreSQL, or Grafana configuration.

Clone the submitted branch if necessary:

```bash
git clone --branch final \
  https://github.com/hmsgrs/AskGipuzkoaClimate-LLMZoomcamp-Capstone.git
cd AskGipuzkoaClimate-LLMZoomcamp-Capstone
```

Start the complete reviewer runtime:

```bash
OPENAI_API_KEY=your-key docker compose up --build --wait
```

Open:

| Service | URL | Login |
|---|---|---|
| Streamlit application | <http://127.0.0.1:8501> | None |
| Grafana monitoring | <http://127.0.0.1:3000> | `admin` / `askgipuzkoa` |
| Streamlit health | <http://127.0.0.1:8501/_stcore/health> | None |
| Grafana health | <http://127.0.0.1:3000/api/health> | None |

Compose performs the following automatically before Streamlit starts:

1. Starts PostgreSQL with pgvector.
2. Verifies the committed snapshot, manifest, database digest, and embedding artifact.
3. Creates the database schemas and least-privilege application/Grafana roles.
4. Imports 161 committed vectors without calling the embedding API.
5. Loads six auditable monitoring sessions and nine feedback records.
6. Starts Streamlit and the provisioned Grafana dashboard.

Suggested questions:

```text
¿Qué previsión muestra la instantánea para hoy en Hernani?
What forecast was captured for tomorrow in Lasarte-Oria?
¿Qué tiempo se capturó para pasado mañana en Irun?
¿Qué avisos se capturaron para la costa de Gipuzkoa?
What climate risks affect Gipuzkoa?
Hay peligro inmediato, necesito ayuda
```

The weather examples are interpreted relative to the snapshot effective date,
`2026-07-27`. The UI and citations mark this evidence as historical/stale.

Stop the runtime without deleting its PostgreSQL data:

```bash
docker compose down
```

Use `docker compose down --volumes` only when intentionally deleting local live
sessions and resetting PostgreSQL to the six committed fixtures.

## Audit Without An OpenAI Key

The data bundle, lexical retrieval, and tests can be audited without any provider or
OpenAI credentials:

```bash
uv sync --group dev

uv run python -m app.snapshot verify \
  --snapshot data/snapshots/gipuzkoa-demo-2026-07-27

uv run python -m app.snapshot inspect \
  --snapshot data/snapshots/gipuzkoa-demo-2026-07-27

uv run pytest -q
```

Expected test result on a machine without the optional PostgreSQL integration-test
URL is:

```text
82 passed, 3 skipped
```

The three skipped tests require `TEST_DATABASE_URL`. PostgreSQL behavior is also
exercised by the default Compose startup and one-shot runtime initializer.

## Course Deliverables And Evidence

| Course area | Primary implementation | Supporting evidence |
|---|---|---|
| Problem and users | [`README.md`](README.md) | [`PROJECT_PLAN.md`](PROJECT_PLAN.md) |
| Official data sources | [`app/source_registry.py`](app/source_registry.py) | [`docs/API_DISCOVERY.md`](docs/API_DISCOVERY.md) |
| Data ingestion | [`app/ingest.py`](app/ingest.py) | [`docs/INGESTION.md`](docs/INGESTION.md) |
| Orchestration | [`kestra/`](kestra/) | [`compose.yaml`](compose.yaml) optional `ingestion` profile |
| Immutable dataset | [`data/snapshots/gipuzkoa-demo-2026-07-27/`](data/snapshots/gipuzkoa-demo-2026-07-27/) | [`app/snapshot.py`](app/snapshot.py), [`docs/SNAPSHOTS.md`](docs/SNAPSHOTS.md) |
| RAG orchestration | [`app/rag_helper.py`](app/rag_helper.py) | [`app/assistant.py`](app/assistant.py) |
| Lexical retrieval | [`app/sqlite_repository.py`](app/sqlite_repository.py) | SQLite FTS5 in the committed snapshot |
| Semantic retrieval | [`app/pgvector_repository.py`](app/pgvector_repository.py) | [`app/portable_embeddings.py`](app/portable_embeddings.py) |
| Weather retrieval | [`app/weather_api.py`](app/weather_api.py) | [`app/euskalmet_scope.py`](app/euskalmet_scope.py) |
| LLM and metrics | [`app/metrics.py`](app/metrics.py) | OpenAI Responses API with `store=False` |
| Retrieval evaluation | [`evaluation/retrieval_questions.json`](evaluation/retrieval_questions.json) | [`app/retrieval_evaluation.py`](app/retrieval_evaluation.py) |
| LLM evaluation | [`evaluation/llm_questions.json`](evaluation/llm_questions.json) | [`app/llm_evaluation.py`](app/llm_evaluation.py), [`docs/EVALUATION.md`](docs/EVALUATION.md) |
| Interface | [`app/streamlit_app.py`](app/streamlit_app.py) | [`docs/APPLICATION.md`](docs/APPLICATION.md) |
| Persistence and feedback | [`app/db_save.py`](app/db_save.py), [`app/db_feedback.py`](app/db_feedback.py) | [`app/db_init.py`](app/db_init.py) |
| Monitoring | [`grafana/dashboards/askgipuzkoa.json`](grafana/dashboards/askgipuzkoa.json) | [`docs/MONITORING.md`](docs/MONITORING.md) |
| Reproducibility | [`Dockerfile`](Dockerfile), [`compose.yaml`](compose.yaml), [`uv.lock`](uv.lock) | [`app/runtime_init.py`](app/runtime_init.py) |
| Automated tests | [`tests/`](tests/) | [`pyproject.toml`](pyproject.toml) |

## Application Flow

```text
Streamlit question
  -> deterministic route and language detection
     -> emergency: immediate 112 response, no LLM
     -> weather: read-only historical weather/warning snapshot
     -> knowledge: pgvector semantic search with SQLite source hydration
  -> bounded official context with application-assigned [S#] labels
  -> OpenAI answer with response storage disabled
  -> citation validation and official source cards
  -> optional consented PostgreSQL persistence and feedback
  -> Grafana dashboard
```

The citation contract is implemented in [`app/rag_helper.py`](app/rag_helper.py).
The application, rather than the model, assigns source labels. Missing citations,
unknown labels, and model-written URLs fail closed instead of being displayed as a
grounded answer.

## Reviewer Snapshot Contents

The committed bundle is:

```text
data/snapshots/gipuzkoa-demo-2026-07-27/
```

Important identifiers:

| Field | Value |
|---|---|
| Snapshot ID | `gipuzkoa-demo-2026-07-27` |
| Effective date | `2026-07-27` |
| Database SHA-256 | `24aa4860286d420aee3a6daab139f8a25de1997d13f3d9a84ea1f505557341a3` |
| Manifest SHA-256 | `e0b33a259bd60c53965be83958a830047fc9d5c5ae7394d77516edabbe9a6073` |
| Producer revision | `46f8ef8fdaed64425f378fbd9d134e658ea7b0af` |
| Producer worktree | Clean (`source_dirty: false`) |
| Embedding model | `text-embedding-3-small` |
| Embedding dimensions | 1,536 |
| Portable vectors | 161 |

Table coverage:

| Data | Rows | Notes |
|---|---:|---|
| Official sources | 9 | Euskalmet and Basque Government pages/reports |
| Active documents | 9 | Normalized official HTML/PDF material |
| Document chunks | 161 | Deterministic RAG chunks with stable IDs |
| Retrieval questions | 6 | Spanish/English evaluation fixture |
| Weather stations | 45 | Gipuzkoa station metadata |
| Public city forecasts | 18 | Six public cities across three dates |
| Authenticated location forecasts | 30 | Ten Gipuzkoa municipalities across three dates |
| Authenticated warning responses | 2 | Gipuzkoa coast and interior request scopes |
| AEMET daily observations | 0 | Explicitly outside this scoped reviewer snapshot |
| Current conditions | 0 | Explicitly excluded |

Authenticated forecast dates:

```text
2026-07-27
2026-07-28
2026-07-29
```

Authenticated municipalities:

| Municipality | Euskalmet zone |
|---|---|
| Donostia / San Sebastian | `donostialdea` |
| Irun | `coast_zone` |
| Hondarribia | `coast_zone` |
| Hernani | `cantabrian_valleys` |
| Lasarte-Oria | `cantabrian_valleys` |
| Zarautz | `coast_zone` |
| Tolosa | `cantabrian_valleys` |
| Eibar | `cantabrian_valleys` |
| Arrasate / Mondragon | `cantabrian_valleys` |
| Beasain | `cantabrian_valleys` |

Warning request scopes:

```text
GIPUZKOA_COAST
GIPUZKOA_INTERIOR
```

The exact acquisition matrix, table counts, timestamps, checksums, and exclusions are
machine-readable in
[`data/snapshots/gipuzkoa-demo-2026-07-27/manifest.json`](data/snapshots/gipuzkoa-demo-2026-07-27/manifest.json).

## Retrieval And LLM Evaluation

The project compares two retrieval approaches over the same canonical chunks:

| Retriever | Hit rate at 5 | MRR at 5 |
|---|---:|---:|
| SQLite FTS5 | 67% | 0.67 |
| pgvector | 100% | 0.92 |

The six-question prompt evaluation compares the course-style baseline with the
selected citation/safety prompt:

| Metric | Course baseline | Selected prompt |
|---|---:|---:|
| Overall score | 3.67 / 5 | 4.83 / 5 |
| Citation correctness | 1.83 / 5 | 4.50 / 5 |
| Citation-contract compliance | 16.7% | 100% |
| Required-source recall | 16.7% | 100% |

Evaluation details and limitations are in [`docs/EVALUATION.md`](docs/EVALUATION.md).
Compact results are in
[`evaluation/results/llm_evaluation_summary.json`](evaluation/results/llm_evaluation_summary.json).
The recorded LLM evaluation retains its original July 22 snapshot provenance rather
than being falsely relabelled. The July 27 reviewer snapshot has the same nine
documents, 161 chunks, corpus digest, and embedding artifact; only weather coverage
changed.

## Persistence, Feedback, And Monitoring

PostgreSQL stores vectors and, only with explicit UI consent, interaction records.
Storage is disabled by default.

The application records:

- Question, answer, route, language, backend, model, and grounded prompt.
- Token usage, latency, estimated generation cost, and citation validity.
- Rendered citation metadata.
- Optional user score/comment.
- Optional structured LLM-as-Judge verdict.
- Record origin: `synthetic_fixture`, `published_test`, or `live`.

Grafana is provisioned automatically with nine panels covering questions, language,
routes, latency, feedback, judge relevance, token usage, estimated cost, and record
origin. See [`docs/MONITORING.md`](docs/MONITORING.md).

## Why Kestra Is Optional For Evaluation

Kestra is implemented and available, but it is intentionally outside the default
reviewer startup. The default command starts only PostgreSQL, snapshot initialization,
Streamlit, and Grafana. Kestra is behind the optional Compose profile `ingestion`.

In this repository, "Kestra is ON" means the orchestration is real and most flow
definitions use `disabled: false`; it does not mean Kestra is started for every
reviewer. The `kestra` and `kestra-init` services start only when the `ingestion`
profile is explicitly selected. Two AEMET flows are deliberately `disabled: true`
until the previously tested provider token is rotated.

The workflow definitions in [`kestra/`](kestra/) cover:

| Workflow group | Files |
|---|---|
| Public Euskalmet data | `ingest_public_weather.yaml`, `ingest_euskalmet_homepage_alerts.yaml` |
| Authenticated Euskalmet data | `ingest_euskalmet_authenticated_forecasts.yaml`, `ingest_euskalmet_authenticated_alerts.yaml` |
| AEMET data | `refresh_aemet_station_catalogue.yaml`, `ingest_aemet_daily_incremental.yaml`, `backfill_aemet_daily.yaml` |
| Climate reanalysis | `ingest_era5_land_monthly.yaml` |
| Official RAG corpus | `ingest_official_documents.yaml` |
| Snapshot publication | `create_data_snapshot.yaml`, `produce_all_source_snapshot.yaml` |

Most flows are manual rather than scheduled so a reviewer run cannot accidentally
contact providers, spend API quota, or mutate a published dataset. The AEMET catalogue
and incremental flows are disabled pending provider-side token rotation.

### Provider Name Clarification

This project does **not** integrate Eustat. The similarly named Basque weather provider
is **Euskalmet**, accessed through the Euskadi API. The authenticated providers and
services used by the complete ingestion implementation are:

- Euskalmet: private key, login fingerprint, and account email.
- AEMET OpenData: API token file.
- Copernicus CDS: `.cdsapirc` plus accepted ERA5-Land licence terms.
- OpenAI: document embedding when creating a changed corpus.

### Why Reproducing Kestra Is Not A Fair Audit Prerequisite

A complete local Kestra setup requires more than starting one container:

- Provider accounts and API credentials cannot be committed to the repository.
- Euskalmet and AEMET credentials must be mounted from absolute host paths.
- Kestra `SECRET_*` environment values must be Base64-encoded.
- CDS requires a separate account, configuration file, and accepted dataset licence.
- Task containers need the ingestion image, Docker socket access, the Compose network,
  persistent data volumes, and a PostgreSQL URL using hostname `postgres`.
- A prior AEMET test token must be rotated before canonical AEMET backfill work.
- Provider responses and forecast horizons can change over time, making a live rerun
  unsuitable as the only evidence for a fixed course submission.

Configuration placeholders are documented in [`.env.example`](.env.example) and the
maintainer procedure is in [`docs/INGESTION.md`](docs/INGESTION.md).

### How The Snapshot Represents The Kestra Pipeline Result

Kestra is an orchestration layer, not a second ingestion implementation. Its YAML
tasks execute the same Python entry points used to create the committed dataset:

```text
Kestra task
  -> python -m app.ingest or python -m app.snapshot_refresh
  -> fetch and normalize bounded official sources
  -> write a disposable SQLite working database
  -> validate expected coverage
  -> app.snapshot creates an immutable bundle
  -> manifest and content hashes make the result auditable
```

For course evaluation, the committed snapshot is the materialized, checksummed result
of that pipeline contract for the scoped reviewer dataset. It lets the evaluator
inspect the output and run the application without receiving private provider keys or
depending on changing live APIs.

The scope distinction is important: this reviewer snapshot demonstrates the official
document, public weather, authenticated Euskalmet forecast, and authenticated
Euskalmet warning paths. It does not claim that the broader AEMET daily-history and
ERA5 all-source flow is present in this bundle. Those implementations remain available
for maintainer execution after credential and licence setup.

## Optional Kestra Inspection

The YAML can be reviewed without provider credentials. Starting the Kestra UI is
optional and does not make credentialed flows runnable:

```bash
docker compose --profile ingestion up -d postgres kestra-init kestra
```

Open Kestra at <http://127.0.0.1:8080>. The local defaults are
`admin@kestra.io` / `askgipuzkoa-kestra-local` unless overridden.

Build the separate task image only when inspecting executable flows:

```bash
docker compose --profile ingestion-build build ingestion-image
```

Do not execute authenticated or all-source flows without the required secrets and
provider authorization. Kestra mounts the Docker socket, so it should remain a local
maintainer service rather than an exposed public component.

## Known Limitations

- Snapshot forecasts and warnings are historical, never current operational status.
- The snapshot excludes AEMET daily observations and current station conditions.
- A canonical all-source release still requires AEMET token rotation and bounded
  AEMET/ERA5 acquisition.
- The retrieval and LLM evaluation sets contain six questions each.
- The LLM judge uses the same configured model family as generation and is not human
  ground truth.
- The application covers Gipuzkoa and may use broader Basque Country or Spain-wide
  documents when clearly identified.
- This application does not replace Euskalmet, AEMET, emergency authorities, or `112`.

## Additional Documentation

| Document | Purpose |
|---|---|
| [`README.md`](README.md) | Project summary, architecture, course criteria, startup |
| [`STATUS.md`](STATUS.md) | Implemented state, verification, remaining work |
| [`docs/APPLICATION.md`](docs/APPLICATION.md) | Routing, citations, safety, persistence |
| [`docs/SNAPSHOTS.md`](docs/SNAPSHOTS.md) | Snapshot verification, creation, and installation |
| [`docs/INGESTION.md`](docs/INGESTION.md) | Provider commands and Kestra configuration |
| [`docs/API_DISCOVERY.md`](docs/API_DISCOVERY.md) | Provider validation and source classification |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | Retrieval and LLM evaluation methodology/results |
| [`docs/MONITORING.md`](docs/MONITORING.md) | PostgreSQL events, privacy, and Grafana |
| [`docs/SESSION_FIXTURES.md`](docs/SESSION_FIXTURES.md) | Auditable monitoring fixtures and provenance |
| [`docs/SNAPSHOT_REDESIGN.md`](docs/SNAPSHOT_REDESIGN.md) | Snapshot-first architecture decision |
