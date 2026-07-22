# Project Plan

## 1. Scope

Build a Spanish and English askbot for weather, climate risks, and emergency preparedness in Gipuzkoa, Spain.

The application will answer source-grounded questions from a curated official knowledge base. It will also handle current weather, forecasts, and warnings through a public official API. It will not issue emergency alerts, provide medical advice, or replace official channels. Answers about immediate danger must direct users to `112`.

## 2. Core User Flows

### Knowledge-base question

1. A user asks a question in Spanish or English through Streamlit.
2. The application detects whether the request concerns documents or live weather data.
3. The retrieval layer searches the knowledge base.
4. OpenAI receives the question and retrieved context.
5. The application displays an answer and source links.
6. The user submits optional positive/negative feedback and a comment.

### Live weather question

1. A user asks about a current forecast, observation, or warning.
2. The application calls the selected official weather provider.
3. The answer shows the provider and the time at which the data was retrieved.
4. If a warning is active, the UI displays the emergency disclaimer and official links.

## 3. Data Sources

The source registry will contain only publicly accessible and reproducible sources. It excludes social-media posts; warnings come from official weather APIs and visible official warning cards.

The initial API-discovery results, including the distinction between normal weather, hazard alerts, and historical climate data, are in [API Discovery](docs/API_DISCOVERY.md).

| Source type | Intended source | Intended use |
|---|---|---|
| Weather and warnings | AEMET OpenData and Euskalmet Meteo API | Current conditions, forecasts, meteorological warnings |
| Regional weather/emergency information | Euskalmet Meteoadversa warning cards and Basque Government public resources | No-key warning fallback, local context, and public guidance |
| Climate information | Basque Government publications and public Copernicus data | Historical and long-term climate context |

The initial RAG corpus is limited to Euskalmet and Basque Government HTML pages plus selected Euskalmet monthly and seasonal PDFs. Provincial and municipal sources are out of scope for the first release.

Each source record will store its URL, organization, title, language, content type, date, retrieval time, and a content hash.

## 4. Knowledge Base Design

### SQLite

SQLite is the canonical, easy-to-inspect document store.

| Table | Purpose |
|---|---|
| `sources` | Source authority, URL, language, and publication metadata |
| `documents` | Normalized retrieved documents |
| `chunks` | Chunk text, chunk order, and source references |
| `evaluation_questions` | Retrieval and LLM evaluation datasets |
| `chunks_fts` | SQLite FTS5 virtual table indexing chunk text and selected metadata |

SQLite FTS5 full-text search will provide one retrieval approach for evaluation. `minsearch` will not be used.

### PostgreSQL with pgvector

PostgreSQL with pgvector stores an embedding for every SQLite chunk, using the SQLite chunk ID as the cross-database reference. It also stores interaction events used by Grafana.

| Table | Purpose |
|---|---|
| `chunk_embeddings` | pgvector embedding, SQLite chunk ID, model metadata |
| `conversations` | Question, answer, prompt, token usage, latency, cost, and timestamps |
| `feedback` | User and LLM-judge feedback linked to conversations |

pgvector similarity search will provide the second retrieval approach for evaluation.

## 5. Course Code Reuse

The implementation will adapt familiar course modules with only domain and storage changes. This preserves the application flow taught in the course while replacing the in-memory retrieval example with databases suited to this project.

| Capstone module | Course source | Adaptation |
|---|---|---|
| `app/rag_helper.py` | `05-monitoring/code/rag_helper.py` | Keep `RAGBase`, including its prompt construction and OpenAI call flow. Change `search()` to call a SQLite FTS5 or pgvector repository, and build context from source-document chunks. |
| `app/metrics.py` | `05-monitoring/code/metrics.py` | Keep `LLMCallRecord` and `RAGWithMetrics`; save records through the existing-style PostgreSQL persistence flow. |
| `app/judge.py` | `05-monitoring/code/judge.py` | Keep the Pydantic verdict model and structured OpenAI relevance evaluation; update the domain-specific instructions only. |
| `app/ingest.py` | `02-vector-search/code/ingest.py` and `05-monitoring/code/ingest.py` | Keep the `requests` fetch/load shape and stable document-ID handling; load the source registry into SQLite and pgvector rather than building `minsearch.Index`. |
| `app/assistant.py` | `05-monitoring/code/assistant.py` | Keep `create_assistant()` to assemble the OpenAI client, retrieval backend, and metrics-enabled RAG object. |
| `app/streamlit_app.py` | `05-monitoring/code/app.py` | Keep the Streamlit question, spinner, answer, metrics, and feedback workflow; add citations and the `112` notice. |
| `app/db_init.py`, `app/db_save.py`, `app/db_feedback.py` | `05-monitoring/code` equivalents | Keep the PostgreSQL schema, conversation persistence, and feedback persistence pattern for Grafana. |
| `app/evaluation_utils.py` | `04-rag-evaluation/code/evaluation_utils.py` | Keep the structured OpenAI evaluation and retry utilities. |

The only custom application components are the official-source registry, document normalization/chunking, SQLite FTS5 repository, pgvector repository, weather API adapter, and Kestra orchestration flow.

## 6. Ingestion Pipeline: Kestra

Create a Kestra flow at `kestra/ingest_gipuzkoa_sources.yaml` that:

1. Loads the source registry.
2. Downloads or requests the configured public sources.
3. Extracts and normalizes source content.
4. Splits documents into chunks.
5. Upserts sources, documents, and chunks into SQLite.
6. Sends new or changed chunks to OpenAI for embeddings.
7. Upserts embeddings into PostgreSQL with pgvector.
8. Records the run result and failed sources.

The flow must be runnable manually and on a schedule. Documentation will explain how to start Kestra, configure the source registry, and run the flow.

## 7. RAG Application

The initial application will have these modules:

| Module | Responsibility |
|---|---|
| `app/streamlit_app.py` | User interface, sample questions, answer display, and feedback controls |
| `app/rag_helper.py` | Course-style RAG orchestration, prompt construction, and response assembly |
| `app/assistant.py` | Course-style application composition |
| `app/sqlite_repository.py` | SQLite FTS5 lookup and source/chunk loading |
| `app/pgvector_repository.py` | Embedding and pgvector similarity search |
| `app/openai_client.py` | OpenAI chat and embedding API calls |
| `app/weather_api.py` | Official live weather/warning provider integration |
| `app/metrics.py` | Course-style OpenAI usage, latency, and cost capture |
| `app/db_init.py`, `app/db_save.py`, `app/db_feedback.py` | PostgreSQL conversation and feedback persistence for Grafana |
| `app/judge.py` | Course-style structured OpenAI relevance judge |

OpenAI must be instructed to use retrieved context, provide the linked sources, avoid unsupported claims, and clearly state when the available material does not answer the question.

## 8. Interface: Streamlit

The Streamlit UI will provide:

- A question input and Spanish/English example prompts
- Answer text with cited source cards
- Live-data provider and timestamp when applicable
- A visible `112` emergency notice
- Positive/negative feedback controls and optional comments
- Clear loading and source/API error states

## 9. Retrieval Evaluation

Create a reproducible retrieval dataset with questions in Spanish and English. Each record must include the question and expected supporting source or chunk IDs.

Evaluate:

1. SQLite FTS5 full-text search
2. pgvector similarity search

Measure whether the expected source/chunk appears in the retrieved results. Document the dataset, metric, results, and selected retrieval approach in `docs/evaluation.md`.

## 10. LLM Evaluation

Create a separate evaluation dataset with representative questions, expected answer criteria, and required citations.

Compare two OpenAI prompt designs. Evaluate each answer for:

- Relevance to the question
- Grounding in retrieved context
- Citation presence and correctness
- Spanish/English response quality
- Safe handling of emergency-related questions

Document the method, results, and selected prompt in `docs/evaluation.md`.

## 11. Monitoring: Grafana

Store application events in PostgreSQL and configure Grafana with PostgreSQL as its data source.

Create a dashboard with at least these charts:

1. Number of questions over time
2. Spanish versus English question count
3. Live-weather versus knowledge-base route distribution
4. Answer latency
5. Positive versus negative feedback
6. OpenAI token usage or estimated cost

Add a screenshot and access instructions to the README or `docs/monitoring.md`.

## 12. Containerization and Reproducibility

Use Docker Compose to start all services:

- Streamlit application
- PostgreSQL with pgvector
- Kestra
- Grafana

Pin application dependencies with `uv.lock`. Provide `.env.example` for OpenAI and data-provider credentials. Document a clean-start sequence that downloads the public data, runs ingestion, and opens the application and monitoring dashboard.

## 13. Delivery Milestones

1. Repository structure, Docker Compose, dependency setup, and environment template
2. SQLite schema, pgvector schema, source registry, and initial public corpus
3. Kestra ingestion flow
4. Retrieval layer and OpenAI RAG flow
5. Streamlit UI and live weather provider
6. Retrieval and LLM evaluation
7. Feedback capture and Grafana dashboard
8. Final documentation, screenshots, and project walkthrough

## 14. Deliverables

- Public GitHub repository: `llm-zoomcamp-capstone`
- Complete README for reviewers who have not taken the course
- Automated Kestra ingestion flow
- Streamlit interface
- SQLite and pgvector knowledge-base implementation
- Retrieval and LLM evaluation results
- Grafana dashboard with feedback and at least five charts
- Docker Compose configuration for all services
- Reproducible setup and usage documentation
