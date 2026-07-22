# Project Status

Last updated: 2026-07-22

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

- Kestra flows exist for hourly public weather ingestion and monthly canonical corpus indexing, embedding synchronization, and retrieval comparison.
- The ingestion Docker image builds successfully as `gipuzkoa-askbot-ingestion:latest`.
- Authenticated ingestion commands return structured JSON receipts with the destination table and record ID.
- The automated test suite passes: 27 tests, including the real pgvector integration test against the Compose service.

## Currently Working On

Automating authenticated weather, alert, and historical-data refreshes in Kestra.

## Next Steps

### High Priority

1. Add a Kestra flow for authenticated Euskalmet forecasts and alerts with read-only secret mounts.
2. Add a Kestra flow for AEMET station metadata and incremental daily observations.
3. Select representative AEMET stations across coastal and inland Gipuzkoa for the historical corpus.

### Medium Priority

1. Implement the ERA5-Land historical backfill and derive daily/monthly climate indicators.
2. Validate Euskalmet current-station and sensor-reading ingestion using selected Gipuzkoa stations.
3. Validate the AEMET warning endpoint and store normalized hazard alerts.

### Low Priority

1. Implement the course-style RAG application around `rag_helper.py` and `assistant.py`.
2. Build the Streamlit interface with citations, source timestamps, and emergency notices.
3. Add retrieval and LLM evaluation datasets.
4. Add conversation monitoring, feedback collection, and Grafana dashboards.
5. Complete the full Docker Compose environment and final reviewer documentation.
