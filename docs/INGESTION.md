# Data Ingestion

The ingestion code follows the fetch, normalize, and load structure used in the course's `ingest.py`. It writes operational data to SQLite and keeps numerical data out of the RAG index.

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
```

All commands default to `data/processed/ingestion.sqlite`. This file is intentionally ignored by Git.

Authenticated snapshot commands print a JSON receipt rather than the complete provider payload. `upserted: 1` means one API response was stored successfully; `table`, `database`, `record_id`, and `source_url` identify where it was written.

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

## Kestra

Build the ingestion image:

```bash
docker build -f Dockerfile.ingestion -t gipuzkoa-askbot-ingestion:latest .
```

Import `kestra/ingest_public_weather.yaml`, `kestra/ingest_euskalmet_homepage_alerts.yaml`, and `kestra/ingest_official_documents.yaml` into Kestra. The flows refresh normal weather hourly, official Euskalmet homepage warning cards every 15 minutes, and the canonical knowledge corpus monthly. The corpus flow then synchronizes embeddings and compares FTS5 with pgvector. Configure persistent storage at `/data` and Kestra secrets named `OPENAI_API_KEY` and `DATABASE_URL`.

Authenticated AEMET and Euskalmet flows are intentionally not scheduled until their credentials return successful smoke tests.
