# Gipuzkoa Weather and Climate Emergency Askbot

An end-to-end RAG application that answers Spanish and English questions about weather, climate risks, and emergency preparedness in Gipuzkoa, Spain.

Course reviewers should start with the dedicated [Course Evaluator Guide](COURSE_EVALUATOR.md).

The application uses official public sources, cites its evidence, collects user feedback, and exposes operational metrics in Grafana. It is an informational tool, not an official emergency-alert service. In an immediate emergency, contact `112`.

## Problem

Residents and visitors need trustworthy, locally relevant answers to questions such as:

- What warnings were captured in the published Gipuzkoa snapshot?
- How should I prepare for heavy rain or flooding?
- What local climate risks are expected to increase?
- Where can I find official guidance for a heatwave or storm?

Official information is distributed between weather services, government pages, public documents, and climate datasets. This project brings those sources into one question-and-answer interface while retaining links back to the original material.

## Features

- Spanish and English question answering
- Retrieval-augmented answers grounded in official documents
- Source links and publication/update metadata with answers
- Committed, checksummed historical reviewer snapshot
- Emergency disclaimer and `112` guidance
- User feedback collection
- Automatically provisioned Grafana dashboard with nine panels
- One-command reviewer runtime with optional Kestra orchestration

## Technology Stack

| Area | Technology | Purpose |
|---|---|---|
| LLM | OpenAI | Answer generation and embeddings |
| Knowledge base | SQLite with FTS5 | Document text, source metadata, and evaluation data |
| Vector database | PostgreSQL with pgvector | Vector embeddings and similarity search |
| Ingestion pipeline | Python and optional Kestra | Explicit capture and immutable snapshot publication |
| Interface | Streamlit | Interactive web application |
| Monitoring | Grafana | Automatically provisioned application and feedback dashboard |
| Local runtime | Docker Compose | Verify data, seed pgvector, and run the complete showcase |

SQLite holds the complete normalized documents and citation metadata. Its FTS5 index provides the text-retrieval baseline. pgvector stores embeddings keyed to SQLite chunk IDs. After vector retrieval, the application loads the full text and source details from SQLite. With explicit user consent, conversation and feedback metrics are stored in local PostgreSQL for Grafana.

## Data Sources

The scoped reviewer corpus uses public, reproducible, authoritative sources:

- AEMET OpenData for forecasts, observations, and meteorological warnings
- Euskalmet and Basque Government public weather and emergency information
- Selected Euskalmet monthly and seasonal climate reports
- Basque Government climate publications and guidance
- Public Copernicus climate data where historical context is necessary

Warnings come from authenticated official alert APIs, with visible Euskalmet Meteoadversa warning cards as a no-key fallback. The project does not ingest social-media posts.

Every stored document will include its source URL, publishing organization, language, title, date, content type, and ingestion timestamp.

The initial RAG corpus is intentionally limited to Euskalmet and Basque Government material. Its canonical SQLite schema versions normalized documents, creates deterministic chunks, and indexes active chunks with FTS5. The bilingual retrieval fixture records expected official source IDs for reproducible evaluation.

Semantic retrieval stores `text-embedding-3-small` vectors in PostgreSQL with pgvector, keyed by stable SQLite chunk IDs. A committed portable export binds all 161 vectors to exact chunk text hashes and seeds a fresh PostgreSQL volume without an embedding API call. Search results are hydrated from the verified read-only SQLite snapshot so citations retain one canonical source.

Confirmed access paths, source classifications, and credential requirements are documented in [API Discovery](docs/API_DISCOVERY.md).

See [Data Snapshots](docs/SNAPSHOTS.md) for the no-credential audit path and snapshot commands. [Snapshot-First Redesign](docs/SNAPSHOT_REDESIGN.md) records the architecture decision and migration. [Data Ingestion](docs/INGESTION.md) retains provider commands and optional Kestra flows. [RAG Application](docs/APPLICATION.md) documents routing, citations, safety, and startup. Retrieval and prompt results are recorded in [Evaluation](docs/EVALUATION.md), the fixture publication workflow is in [Session Fixtures](docs/SESSION_FIXTURES.md), and the PostgreSQL/Grafana contract is in [Monitoring](docs/MONITORING.md).

## Architecture

```text
Streamlit user interface
  -> deterministic bilingual query routing
     -> immutable historical API and homepage snapshots for forecasts and warnings
     -> SQLite FTS5 retrieval or pgvector similarity retrieval
  -> OpenAI answer generation with citations
  -> optional feedback and consented interaction events in PostgreSQL
  -> Grafana dashboard

Optional manual Python or Kestra producer
  -> fetch and normalize bounded official sources
  -> load a disposable working SQLite database
  -> publish a verified immutable snapshot
```

## Course Criteria

This project is designed to cover the LLM Zoomcamp project requirements:

| Criterion | Implementation |
|---|---|
| Problem description | This README defines the users, scope, problem, and safety boundary |
| Retrieval flow | Course-style RAG over SQLite/pgvector with cited OpenAI answers |
| Retrieval evaluation | Compare SQLite FTS5 retrieval with pgvector similarity retrieval |
| LLM evaluation | Structured LLM-as-Judge comparison selects the citation/safety prompt |
| Interface | Bilingual Streamlit UI with source cards, feedback, metrics, and `112` guidance |
| Ingestion pipeline | Canonical Python snapshot commands plus optional manual Kestra workflows |
| Monitoring | Consented PostgreSQL events plus an automatically provisioned nine-panel Grafana dashboard |
| Containerization | One Compose command starts pgvector, initialization, Streamlit, and Grafana; Kestra is optional |
| Reproducibility | Version-pinned dependencies and a committed snapshot with checksummed vectors |

## Course Code Reuse

The project will intentionally adapt the LLM Zoomcamp implementations so its structure remains familiar to course reviewers and learners. The course permits reuse of its code.

| Capstone module | Course implementation | Planned minimal change |
|---|---|---|
| `app/rag_helper.py` | `05-monitoring/code/rag_helper.py` | Preserve `RAGBase` and its `search`, `build_context`, `build_prompt`, `llm`, and `rag` flow. Adapt the document fields for source chunks and delegate search to SQLite FTS5 or pgvector. |
| `app/metrics.py` | `05-monitoring/code/metrics.py` | Preserve `LLMCallRecord`, `RAGWithMetrics`, token usage, latency, and cost capture. Persist each record to PostgreSQL for Grafana. |
| `app/judge.py` | `05-monitoring/code/judge.py` | Preserve the structured OpenAI relevance judge. Change only its domain wording to Gipuzkoa weather, climate, and emergency questions. |
| `app/ingest.py` | `02-vector-search/code/ingest.py` and `05-monitoring/code/ingest.py` | Preserve the `requests`-based loading pattern. Replace course FAQ endpoints and the in-memory index with a source registry, SQLite storage, and pgvector embedding loading. |
| `app/assistant.py` | `05-monitoring/code/assistant.py` | Preserve `create_assistant()` as the composition root for OpenAI, retrieval, and metrics. |
| `app/streamlit_app.py` | `05-monitoring/code/app.py` | Preserve the question, spinner, response, metrics, and feedback flow. Add source cards and the emergency notice. |
| `app/db_init.py`, `app/db_save.py`, `app/db_feedback.py` | `05-monitoring/code` equivalents | Preserve the PostgreSQL conversation and feedback persistence pattern used by Grafana. |
| `app/evaluation_utils.py` | `04-rag-evaluation/code/evaluation_utils.py` | Preserve structured OpenAI calls, retries, usage tracking, and evaluation helpers. |

`minsearch` will not be used. SQLite FTS5 and pgvector are the two retrieval implementations evaluated by the project.

## Status

See [STATUS.md](STATUS.md) for the current state, active work, and prioritized next steps. See [PROJECT_PLAN.md](PROJECT_PLAN.md) for milestones, data design, evaluation, and expected deliverables.

## Run The Showcase

Install Docker, clone the repository, and provide only an OpenAI key for new answers:

```bash
OPENAI_API_KEY=your-key docker compose up --build --wait
```

Open Streamlit at [http://127.0.0.1:8501](http://127.0.0.1:8501) and Grafana at [http://127.0.0.1:3000](http://127.0.0.1:3000). The local-only Grafana demo login is `admin` / `askgipuzkoa`; override it through `.env` when needed.

The one-shot initializer verifies snapshot `gipuzkoa-demo-2026-07-27`, checks every artifact digest, creates least-privilege database roles, imports 161 committed vectors, and seeds six auditable sessions. The snapshot contains three forecast days for ten representative Gipuzkoa municipalities plus coast and interior warning responses. Four sessions are synthetic demonstrations and two are published real test sessions with full prompts and feedback. Grafana labels each record origin explicitly. AEMET, Euskalmet, CDS, Kestra, PostgreSQL, and Grafana configuration are not reviewer prerequisites.

New consented sessions persist in the named PostgreSQL volume across builds and normal restarts, but they are not part of another clone until explicitly exported, reviewed, and committed. Stop the stack with `docker compose down`; `docker compose down --volumes` discards local live sessions and recreates only the six committed fixtures on the next startup. See [Session Fixtures](docs/SESSION_FIXTURES.md) for the publication workflow.

Creating a new all-source snapshot is an optional maintainer path that requires the relevant provider credentials and acceptance of the [Climate Data Store licence](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download#manage-licences). See [Data Snapshots](docs/SNAPSHOTS.md).

## Limitations

- Weather and warnings in the default snapshot are historical and must not be treated as current conditions.
- The reviewer snapshot intentionally excludes AEMET daily observations and current conditions. Its Euskalmet forecasts and warning responses are archived evidence, not live status.
- The application does not replace official warning channels or emergency services.
- Responses are limited to Gipuzkoa and should identify when a source is Basque Country- or Spain-wide rather than Gipuzkoa-specific.
- The initial evaluation fixtures contain six questions each and are showcase baselines, not comprehensive quality guarantees.
- The bundled passwords are local demonstration credentials, not production defaults.
