# Data Ingestion

The ingestion code follows the fetch, normalize, and load structure used in the course's `ingest.py`. It writes operational data to a disposable working SQLite database and keeps numerical data out of the RAG index. Published application data is an immutable bundle created with `app.snapshot`; see [Data Snapshots](SNAPSHOTS.md).

## Source Commands

```bash
# Public normal-weather data
uv run python -m app.ingest euskalmet-stations
uv run python -m app.ingest euskalmet-forecast

# Authenticated AEMET historical data
uv run python -m app.ingest aemet-stations
uv run python -m app.ingest aemet-daily \
  --station STATION_ID --start 2024-01-01 --end 2024-01-31

# Authenticated Euskalmet location forecast
uv run python -m app.ingest euskalmet-location-forecast \
  --zone donostialdea --location donostia \
  --issued 2026-07-21 --target 2026-07-21

# Authenticated Euskalmet alerts
uv run python -m app.ingest euskalmet-alerts \
  --zone GIPUZKOA_COAST --issued 2026-07-21

# One allowlisted knowledge source
uv run python -m app.ingest knowledge-source --source euskalmet-climate-bulletins

# Complete bounded Euskalmet/Basque Government corpus
uv run python -m app.ingest knowledge-corpus

# Load the bilingual fixture and evaluate FTS5 hit rate at 5
uv run python -m app.ingest knowledge-evaluation --limit 5

# Inspect FTS5 results
uv run python -m app.ingest knowledge-search \
  --question "Que es el cambio climatico?" --limit 5

# Initialize pgvector and embed only missing or changed active chunks
docker compose up -d --wait postgres
uv run python -m app.db_init
uv run python -m app.ingest knowledge-embeddings --batch-size 100

# Inspect semantic results and compare both retrieval methods
uv run python -m app.ingest vector-search \
  --question "What were conditions like during summer 2021?" --limit 5
uv run python -m app.ingest retrieval-comparison --limit 5

# Public Euskalmet homepage Meteoadversa warning cards
uv run python -m app.ingest euskalmet-homepage-alerts

# Scheduled-operation equivalents with durable ingestion-run receipts
uv run python -m app.ingest refresh-euskalmet-alerts \
  --zone GIPUZKOA_COAST --as-of 2026-07-22
uv run python -m app.ingest refresh-euskalmet-forecasts \
  --region basque_country --zone donostialdea --location donostia \
  --horizon-days 3 --as-of 2026-07-22
uv run python -m app.ingest refresh-aemet-daily \
  --station 1012P --as-of 2026-07-22 --lag-days 2 \
  --lookback-days 7 --chunk-days 31 --initial-start 2024-01-01
uv run python -m app.ingest backfill-aemet-daily \
  --station 1012P --start 2024-01-01 --end 2024-01-31 --chunk-days 31
```

All commands default to `data/processed/ingestion.sqlite`. This mutable working file is intentionally ignored by Git. Reviewers use a verified published snapshot and do not run these provider commands.

Authenticated snapshot commands print a JSON receipt rather than the complete provider payload. `upserted: 1` means one API response was stored successfully; `table`, `database`, `record_id`, and `source_url` identify where it was written. Bulk refresh commands also write a durable row to `ingestion_runs` and report their requested, succeeded, and failed work units. A file lock serializes schema initialization; WAL mode and the 60-second SQLite busy timeout coordinate the short write transactions without holding a lock during network requests.

## Operational Data Types

| Table | Data type | Contents |
|---|---|---|
| `weather_stations` | Weather metadata | Gipuzkoa station catalogue from Euskalmet and AEMET |
| `weather_forecasts` | Normal weather | Public bilingual Euskalmet city forecasts |
| `weather_api_snapshots` | Normal weather | Authenticated Euskalmet forecast responses |
| `aemet_daily_observations` | Climate history | Raw official daily AEMET station records |
| `hazard_alerts` | Hazard alert | Authenticated Euskalmet alert snapshots and explicit Euskalmet homepage warning cards |

Normal forecasts and measurements must not create alerts. The public Euskalmet homepage fallback stores only visible Meteoadversa warning-card text with the official homepage as the source URL. The project does not ingest social-media posts.

## Knowledge Base

The reproducible registry in `app/source_registry.py` contains three HTML sources and six selected Euskalmet reports: four seasonal reports and two monthly reports. PDFs are rejected above 10 MB.

| Table | Contents |
|---|---|
| `sources` | Current authority, URL, language, content type, publication date, retrieval time, and content hash |
| `documents` | Versioned normalized text; only the latest version of each source is active |
| `chunks` | Deterministic 1,200-character chunks with stable IDs derived from document version and order |
| `chunks_fts` | FTS5 index over chunk text, title, organization, and language |
| `evaluation_questions` | Spanish/English questions with expected source IDs |

Document IDs derive from `source_id` and the normalized content hash. Changed content creates a new version and deactivates the previous version; unchanged content keeps the same document and chunk IDs. `evaluation/retrieval_questions.json` is the initial FTS5 baseline fixture.

## Semantic Retrieval

SQLite remains the canonical text and citation store. PostgreSQL stores one vector per `(chunk_id, embedding_model)` in `chunk_embeddings`; it does not duplicate full document text. The default `text-embedding-3-small` vectors have 1,536 dimensions and use an HNSW cosine index.

`knowledge-embeddings` compares active SQLite chunk IDs and text hashes with PostgreSQL. It embeds only missing or mismatched rows, commits successful batches, and removes stale vectors only after all batches succeed. Running it twice against unchanged chunks must report `embedded: 0` on the second run.

Set `OPENAI_API_KEY`, a strong `POSTGRES_PASSWORD`, and `DATABASE_URL` at runtime. For local Compose, use a URL in the form `postgresql://gipuzkoa:<password>@localhost:5432/gipuzkoa_askbot`. PostgreSQL is bound to `127.0.0.1` rather than all network interfaces. Semantic results return chunk IDs and cosine scores, then hydrate full text and citation metadata from SQLite in vector rank order.

## Credential Setup

Copy `.env.example` to `.env` only when paths differ from the local defaults. `docs/api_keys/` is excluded from both Git and Docker build contexts.

- AEMET requires a non-empty JWT API token in `AEMET_API_KEY_PATH`. The client follows AEMET's official [OpenData API reference](https://gitlab.aemet.es/opendata/API) and accepts either the raw token or an optional PEM-style wrapper.
- Euskalmet signs a short-lived RS256 JWT with `alg=RS256`, `typ=JWT`, `aud=met01.apikey`, a descriptive application `iss`, `version=1.0.0`, `iat`, and a one-hour `exp`. The payload includes the owner email and the `fingerPrint.txt` value as `loginId`, both accepted identity claims in the official guide.
- For a provider-generated manual test JWT, place it at `EUSKALMET_TOKEN_PATH`. The client uses it directly while it exists; remove the file to return to local RS256 signing. Rotate test tokens after use.
- ERA5-Land uses the configured `~/.cdsapirc` through `cdsapi.Client()`.

Euskalmet uses hierarchical string IDs, not INE municipality codes. Discover locations through `/euskalmet/geo/regions/{region}/zones/{zone}/locations`. Alerts use the fixed zone IDs `SEA`, `BIZKAIA_COAST`, `GIPUZKOA_COAST`, `BIZKAIA_INTERIOR`, `GIPUZKOA_INTERIOR`, `TRANSITION`, and `CORE`.

## Optional Kestra

Build the ingestion image:

```bash
# First set non-empty POSTGRES_PASSWORD and KESTRA_PASSWORD values in .env.
docker compose --profile build build ingestion-image
docker compose up -d postgres kestra
```

The Compose stack pins Kestra `v1.0.0`, PostgreSQL/pgvector, the `gipuzkoa-askbot-ingestion:0.2.0` image, an explicit Docker network, and the shared `askgipuzkoa-ingestion-data` volume. Kestra's Docker task runner replaces the image working directory, so every flow invokes `/app/.venv/bin/python` with `PYTHONPATH=/app`; keep this invariant when adding tasks.

Validate the complete flow directory before importing it:

```bash
docker run --rm -v "$PWD/kestra:/flows:ro" \
  kestra/kestra:v1.0.0 flow validate --local /flows
```

All source flows are manually triggered. They retain their prior task definitions to demonstrate orchestration, but no flow has an automatic schedule. The application and retrieval evaluations do not require Kestra.

| Flow | Execution | Credentials | Current state |
|---|---|---|---|
| `ingest_public_weather` | Manual | None | Available |
| `ingest_euskalmet_homepage_alerts` | Manual | None | Available |
| `ingest_euskalmet_authenticated_forecasts` | Manual | Euskalmet | Available and live-validated |
| `ingest_euskalmet_authenticated_alerts` | Manual | Euskalmet | Available and live-validated |
| `refresh_aemet_station_catalogue` | Manual | AEMET | Disabled pending token rotation |
| `ingest_aemet_daily_incremental` | Manual | AEMET | Disabled pending backfill validation |
| `backfill_aemet_daily` | Manual, bounded inputs | AEMET | Manual only |
| `ingest_era5_land_monthly` | Manual | CDS | Available and live-validated |
| `ingest_official_documents` | Manual | OpenAI and PostgreSQL | Available and live-validated |
| `create_data_snapshot` | Manual | None | Publishes and verifies a completed working database |

Set `KESTRA_USERNAME` to a valid email address and set a unique, non-empty `KESTRA_PASSWORD`; invalid usernames cause Kestra `v1.0.0` to retain its built-in account. Compose deliberately has no example-password fallback because Kestra can control Docker through the mounted socket. Kestra OSS reads `SECRET_*` environment variables as Base64-encoded values. Populate the `KESTRA_SECRET_*_B64` entries in `.env` before starting Kestra. `EUSKALMET_SECRET_DIR` and `AEMET_SECRET_DIR` must decode to absolute host directories that Docker can mount read-only; `CDS_CONFIG_PATH` must decode to the absolute host path of `.cdsapirc`. The task-container `DATABASE_URL` uses the Compose hostname `postgres`, not `localhost`.

The source flows and snapshot publication flow use the pinned runtime. Prior real Docker executions verified public weather, homepage warning cards, authenticated Euskalmet alerts and forecasts, ERA5-Land, and corpus/vector refreshes. ERA5 reruns skip an already valid data/manifest pair; unchanged corpus reruns embed zero chunks. AEMET flows remain disabled until the runtime token is rotated and historical validation succeeds. No flow is scheduled automatically.
