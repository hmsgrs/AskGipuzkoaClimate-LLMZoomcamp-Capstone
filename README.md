# Gipuzkoa Weather and Climate Emergency Askbot

An end-to-end RAG application that answers Spanish and English questions about weather, climate risks, and emergency preparedness in Gipuzkoa, Spain.

The application uses official public sources, cites its evidence, collects user feedback, and exposes operational metrics in Grafana. It is an informational tool, not an official emergency-alert service. In an immediate emergency, contact `112`.

## Problem

Residents and visitors need trustworthy, locally relevant answers to questions such as:

- What weather warnings are active in Gipuzkoa?
- How should I prepare for heavy rain or flooding?
- What local climate risks are expected to increase?
- Where can I find official guidance for a heatwave or storm?

Official information is distributed between weather services, government pages, public documents, and climate datasets. This project brings those sources into one question-and-answer interface while retaining links back to the original material.

## Features

- Spanish and English question answering
- Retrieval-augmented answers grounded in official documents
- Source links and publication/update metadata with answers
- Live weather and warning data from an official provider
- Emergency disclaimer and `112` guidance
- User feedback collection
- Grafana monitoring dashboard
- Automated ingestion with Kestra

## Technology Stack

| Area | Technology | Purpose |
|---|---|---|
| LLM | OpenAI | Answer generation and embeddings |
| Knowledge base | SQLite with FTS5 | Document text, source metadata, and evaluation data |
| Vector database | PostgreSQL with pgvector | Vector embeddings and similarity search |
| Ingestion pipeline | Kestra | Automated data refresh and loading |
| Interface | Streamlit | Interactive web application |
| Monitoring | Grafana | Application and feedback dashboard |
| Local runtime | Docker Compose | Run all services together |

SQLite holds the complete normalized documents and citation metadata. Its FTS5 index provides the text-retrieval baseline. pgvector stores embeddings keyed to SQLite chunk IDs. After vector retrieval, the application loads the full text and source details from SQLite. Conversation and feedback metrics are stored in PostgreSQL for Grafana.

## Data Sources

The initial corpus will use public, reproducible, authoritative sources:

- AEMET OpenData for forecasts, observations, and meteorological warnings
- Euskalmet and Basque Government public weather and emergency information
- Selected Euskalmet monthly and seasonal climate reports
- Basque Government climate publications and guidance
- Public Copernicus climate data where historical context is necessary

Warnings come from authenticated official alert APIs, with visible Euskalmet Meteoadversa warning cards as a no-key fallback. The project does not ingest social-media posts.

Every stored document will include its source URL, publishing organization, language, title, date, content type, and ingestion timestamp.

The initial RAG corpus is intentionally limited to Euskalmet and Basque Government material. Its canonical SQLite schema versions normalized documents, creates deterministic chunks, and indexes active chunks with FTS5. The bilingual retrieval fixture records expected official source IDs for reproducible evaluation.

Semantic retrieval stores `text-embedding-3-small` vectors in PostgreSQL with pgvector, keyed by the stable SQLite chunk IDs. Search results are hydrated from SQLite so citations and full text retain one canonical source. Embedding synchronization is incremental and removes vectors for inactive document versions.

Confirmed access paths, source classifications, and credential requirements are documented in [API Discovery](docs/API_DISCOVERY.md).

See [Data Ingestion](docs/INGESTION.md) for source commands, SQLite tables, and the Kestra flow. The initial bilingual FTS5 baseline is recorded in [Retrieval Evaluation](docs/EVALUATION.md).

## Architecture

```text
Streamlit user interface
  -> query routing
     -> official weather APIs and homepage warning cards for forecasts and warnings
     -> SQLite FTS5 retrieval or pgvector similarity retrieval
  -> OpenAI answer generation with citations
  -> feedback and interaction events in PostgreSQL
  -> Grafana dashboard

Kestra
  -> fetch public sources
  -> normalize and chunk documents
  -> load SQLite
  -> create OpenAI embeddings
  -> load pgvector
```

## Course Criteria

This project is designed to cover the LLM Zoomcamp project requirements:

| Criterion | Implementation |
|---|---|
| Problem description | This README defines the users, scope, problem, and safety boundary |
| Retrieval flow | SQLite/pgvector knowledge base with OpenAI answer generation |
| Retrieval evaluation | Compare SQLite FTS5 retrieval with pgvector similarity retrieval |
| LLM evaluation | Compare two OpenAI prompt designs and retain the better one |
| Interface | Streamlit web UI |
| Ingestion pipeline | Automated Kestra workflow |
| Monitoring | User feedback plus Grafana dashboard with at least five charts |
| Containerization | Docker Compose for all project services |
| Reproducibility | Version-pinned dependencies, public data, and documented setup |

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

## Planned Setup

The initial climate-data path uses the configured CDS credentials in `~/.cdsapirc`. Before the first retrieval, accept the ERA5-Land licence in the [Climate Data Store](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download#manage-licences):

```bash
uv sync --group dev
uv run python -m app.climate_ingest \
  --year 2024 --month 1 --days 1 \
  --output data/raw/era5-land-smoke.nc
```

The command downloads a bounded ERA5-Land subset for Gipuzkoa and writes a retrieval manifest next to it. Output data is intentionally ignored by Git.

The project will also require an OpenAI API key and credentials for the selected weather APIs.

## Limitations

- Information freshness depends on source availability and scheduled ingestion.
- The application does not replace official warning channels or emergency services.
- Responses are limited to Gipuzkoa and should identify when a source is Basque Country- or Spain-wide rather than Gipuzkoa-specific.
