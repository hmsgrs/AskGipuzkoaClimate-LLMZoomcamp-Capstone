# Project Status

Last updated: 2026-07-24

Update this file whenever the project reaches a milestone or its active priority changes.

## Current State

### Repository and planning

- The independent capstone repository is initialized on the `main` branch.
- The problem, architecture, course criteria, implementation plan, and source-discovery results are documented.
- Python 3.12 and `uv` are configured with locked dependencies.
- Local API keys and generated data are excluded from Git and Docker build contexts.

### Data ingestion

- Public Euskalmet station ingestion works and stores 45 Gipuzkoa stations in SQLite.
- Public Euskalmet forecast ingestion works and stores 18 bilingual city forecast rows.
- Euskalmet RS256 JWT authentication works with the downloaded private key.
- The documented Euskalmet region, zone, location, station, reading, and alert routes are implemented.
- Authenticated Donostia forecast ingestion works.
- Authenticated Gipuzkoa coast alert ingestion works.
- AEMET OpenData authentication works using the official raw-JWT API-key format.
- AEMET station ingestion stores 16 Gipuzkoa stations.
- AEMET historical ingestion was validated with three daily records from station `1012P`.
- Copernicus ERA5-Land ingestion works through `~/.cdsapirc`.
- A bounded ERA5-Land smoke test succeeded for temperature, precipitation, and surface soil moisture.
- The temporary Euskalmet `token.txt` was removed; production ingestion signs fresh JWTs from the private key.
- Allowlisted official HTML ingestion works for Euskalmet and Basque Government pages.
- Social-media ingestion was removed. The project relies on official Euskalmet alert APIs and public Meteoadversa warning cards instead.
- A no-key Euskalmet homepage fallback was live-validated with two explicit Meteoadversa warning cards.
- The initial RAG corpus is limited to Euskalmet and Basque Government HTML plus four seasonal and two monthly Euskalmet PDFs.
- Canonical `sources`, versioned `documents`, deterministic `chunks`, `chunks_fts`, and `evaluation_questions` tables are implemented.
- HTML main-content and bounded PDF extraction, hash-based upserts, FTS5 retrieval, and a bilingual evaluation fixture are implemented.
- Live corpus ingestion produced 9 active documents and 161 FTS5 chunks.
- The six-question FTS5 baseline at 5 is 67% overall: 100% Spanish and 33% English.
- PostgreSQL/pgvector schema initialization, HNSW cosine indexing, incremental OpenAI embedding synchronization, semantic retrieval, SQLite hydration, and shared comparison are implemented.
- The real pgvector integration test validates insertion, idempotency, semantic ranking, and stale-vector removal.
- Live OpenAI synchronization stores 161 `text-embedding-3-small` vectors; an unchanged second run embedded zero chunks.
- The six-question pgvector result at 5 is 100% overall and for both languages, compared with FTS5's 67% overall and 33% English result.
- Operational weather, alerts, and historical observations remain separate from RAG documents.

### Automation and verification

- Nine Kestra flows cover public weather, homepage alerts, authenticated Euskalmet forecasts and alerts, AEMET catalogue and daily history, bounded AEMET backfill, monthly ERA5-Land, and canonical corpus/vector refreshes.
- The Compose runtime pins Kestra `v1.0.0`, PostgreSQL/pgvector, explicit Docker networking, persistent Kestra storage, and the shared ingestion-data volume.
- Custom Kestra Basic Auth is active with a valid-email username; the built-in password is rejected.
- The ingestion Docker image builds successfully as `gipuzkoa-askbot-ingestion:0.2.0`.
- SQLite uses WAL mode, a 60-second busy timeout, locked schema initialization, request-scoped snapshot IDs, and durable `ingestion_runs` receipts without locking across network calls.
- Authenticated refresh commands implement transient HTTP retries; incremental AEMET refreshes use lag, repair-window, and chunk controls; monthly ERA5-Land publication is atomic and idempotent.
- All nine flow definitions validate with the pinned Kestra image and are imported into the local runtime.
- Public weather, homepage alerts, authenticated Euskalmet alerts and forecasts, monthly ERA5-Land, and monthly corpus/vector refresh schedules are enabled. The two scheduled AEMET flows remain disabled.
- Real Docker task-runner executions pass for public weather and homepage warning-card ingestion. The public run completed both tasks successfully in 5.8 seconds.
- Runtime-only Kestra secrets are configured. Live Docker task-runner smoke tests pass for Euskalmet alerts and forecasts, AEMET station metadata, OpenAI access, CDS/AEMET/Euskalmet mounts, and PostgreSQL `SELECT 1` connectivity.
- June 2026 ERA5-Land validation produced a 267,123-byte NetCDF with a matching SHA-256 manifest; an unchanged second execution reported `skipped`.
- Corpus/vector validation passes with 9 active documents, 161 chunks, 161 vectors, zero unchanged embeddings, a 67% FTS5 hit rate, and a 100% pgvector hit rate.
- Supervised enabled-flow executions succeeded for Euskalmet alerts (`4Z5ULbLUMBYhL6txvmndWq`), Euskalmet forecasts (`2T6MvUlX6E7dNzVDC8gKbr`), ERA5-Land (`4AUvyN7V9Mu85BjwYDOyuy`), and corpus/vector refresh (`6SNZZsJy9ls5NL0v7AxzBH`).
- The initial AEMET historical backfill exposed its token because Kestra still had a pre-redaction ingestion image. The execution/logs were deleted, SQLite receipts were sanitized, the corrected image was rebuilt, and current logs are clean. The token must be rotated before AEMET validation resumes.
- The complete suite contains 56 tests: 54 unit tests pass, and both real pgvector and application-persistence integration tests pass against the Compose service.

### RAG application and evaluation

- Course-style `RAGBase`, `RAGWithMetrics`, and `create_assistant()` modules provide the end-to-end OpenAI answer path over pgvector or FTS5.
- Deterministic Spanish/English routing separates knowledge, cached live-weather, and immediate-danger requests. Emergency requests bypass the LLM and direct users to `112`.
- Application-assigned `[S#]` labels constrain prompts and source cards; missing, unknown, or URL-bearing citations fail closed instead of being displayed as grounded output.
- The cached weather repository prefers authenticated location forecasts, selects local today/tomorrow dates, excludes stale warnings, deduplicates warning payloads, and retains official URLs and retrieval timestamps.
- The Streamlit showcase includes bilingual examples, escaped source metadata, route/model metrics, optional consented PostgreSQL storage, feedback comments, optional LLM-as-Judge, and a persistent emergency notice. Its real health endpoint returns HTTP 200.
- With explicit user consent, PostgreSQL stores conversations, citation-contract status, token usage, latency, estimated cost, user feedback, and judge relevance through idempotent schema initialization.
- OpenAI answer and structured-judge calls disable provider response storage with `store=False` and reject incomplete, refused, or empty responses.
- A live answer and online judge round trip succeeded with pgvector, persisted conversation/feedback records, and returned a `RELEVANT` verdict.
- Retrieval evaluation now reports hit rate and MRR: FTS5 is 67%/0.67 and pgvector is 100%/0.92 at 5.
- The hardened six-question bilingual prompt evaluation selected the citation/safety prompt at 4.83/5 overall versus 3.67/5 for the course baseline. Citation correctness improved from 1.83 to 4.50; citation-contract compliance and required-source recall both reached 100% versus 16.7%.
- An importable Grafana dashboard defines eight PostgreSQL-backed panels. Runtime Grafana deployment is intentionally pending infrastructure decisions.

## Currently Working On

Implementing the selected local Streamlit/Grafana Compose topology while AEMET validation remains blocked on token rotation.

## Next Steps

### High Priority

1. Rotate the AEMET token and update the mounted `api.pem` file.
2. Backfill station `1012P` from 2024-01-01, validate the seven-day incremental repair window twice, and enable the two AEMET schedules.
3. Add Streamlit and Grafana to local Compose on ports 8501 and 3000 with separate application-writer/Grafana-reader roles and manual retention.

### Medium Priority

1. Expand the retrieval and LLM evaluation fixtures and add independent human review.
2. Select representative AEMET stations across coastal and inland Gipuzkoa for the historical corpus.
3. Implement the ERA5-Land historical backfill and derive daily/monthly climate indicators from the automated monthly files.

### Low Priority

1. Validate Euskalmet current-station and sensor-reading ingestion using selected Gipuzkoa stations.
2. Validate the AEMET warning endpoint and store normalized hazard alerts.
3. Add application and Grafana screenshots after deployment.
4. Complete the full Docker Compose environment and final reviewer walkthrough.
