# Data Snapshots

The application is snapshot-first. A published snapshot is the default input for
auditing, retrieval evaluation, and the Streamlit application. Provider ingestion is
an optional maintainer operation.

The complete design and migration rationale are in
[Snapshot-First Redesign](SNAPSHOT_REDESIGN.md).

## Audit Without Provider Keys

Verify a snapshot downloaded from a project release:

```bash
uv sync --group dev
uv run python -m app.snapshot verify \
  --snapshot data/snapshots/SNAPSHOT_ID
```

Inspect its acquisition window, table counts, source revision, and artifacts:

```bash
uv run python -m app.snapshot inspect \
  --snapshot data/snapshots/SNAPSHOT_ID
```

Run lexical retrieval directly against the immutable database:

```bash
DATA_MODE=snapshot \
SQLITE_DATABASE=data/snapshots/SNAPSHOT_ID/snapshot.sqlite \
RETRIEVAL_BACKEND=sqlite_fts5 \
uv run streamlit run app/streamlit_app.py
```

The snapshot database is opened read-only. Weather and warning results are historical
and use the snapshot acquisition date when interpreting relative terms such as
"today" and "tomorrow".

OpenAI is still required for new generated answers. Provider credentials, Kestra,
CDS, and PostgreSQL are not needed for FTS retrieval or snapshot verification.

## Install A Working Copy

Some development commands expect the conventional processed-data path. Install a
verified disposable copy without contacting providers:

```bash
uv run python -m app.snapshot install \
  --snapshot data/snapshots/SNAPSHOT_ID \
  --database data/processed/ingestion.sqlite
```

Use `--replace` only after stopping all SQLite writers. Installation refuses a
destination with WAL sidecar files because replacing a database under an active WAL
can corrupt or mix states.

## Create A Snapshot

Snapshot creation uses SQLite's online backup API, so committed WAL data is included.
The target is written to a temporary sibling directory, verified, and atomically
renamed. Existing snapshot IDs cannot be overwritten.

```bash
uv run python -m app.snapshot create \
  --database data/processed/ingestion.sqlite \
  --output-root data/snapshots \
  --snapshot-id 2026-07-24-all-sources \
  --artifact data/raw/era5-land/2026-06.nc \
  --artifact data/raw/era5-land/2026-06.json \
  --required-table sources \
  --required-table weather_stations \
  --required-table weather_forecasts \
  --required-table weather_api_snapshots \
  --required-table aemet_daily_observations \
  --required-table hazard_alerts \
  --require-nonempty sources \
  --require-nonempty weather_stations \
  --require-nonempty weather_forecasts \
  --require-nonempty weather_api_snapshots \
  --require-nonempty aemet_daily_observations \
  --notes "Bounded all-source acquisition for the course evaluation"
```

Do not require `hazard_alerts` to be non-empty: a successful acquisition window can
legitimately contain no warning. The release checklist must instead retain evidence
that the alert source was queried successfully.

Additional `--artifact` arguments can package NetCDF data, ERA5 request manifests,
portable embedding exports, raw source bundles, and machine-readable evaluation
results. Every artifact is stored under a content-addressed name.

## One-Command All-Source Refresh

The canonical maintainer command creates a fresh staging database, loads all current
source groups, downloads one explicit ERA5 month, validates required coverage, and
publishes only after every stage succeeds:

```bash
uv run python -m app.snapshot_refresh \
  --snapshot-id 2026-07-24-all-sources \
  --as-of 2026-07-24 \
  --aemet-station 1012P \
  --aemet-start 2024-01-01 \
  --aemet-end 2024-01-31 \
  --era5-year 2024 \
  --era5-month 1
```

The command loads public Euskalmet stations and forecasts, homepage warning cards,
the complete allowlisted document corpus, retrieval questions, AEMET stations and
bounded daily observations, authenticated Euskalmet forecasts and alerts, and the
bounded ERA5-Land artifact. Its temporary database and downloads are removed after
publication. On any provider or validation failure, no snapshot is published.

This command requires all provider credentials and accepted CDS licences. It is not
part of the reviewer path.

## Manual Source Refresh

The working database can be populated with explicit Python commands before snapshot
creation. The exact dates and station IDs are part of the acquisition definition:

```bash
# Public sources
uv run python -m app.ingest euskalmet-stations
uv run python -m app.ingest euskalmet-forecast
uv run python -m app.ingest euskalmet-homepage-alerts
uv run python -m app.ingest knowledge-corpus
uv run python -m app.ingest knowledge-evaluation --limit 5

# Authenticated Euskalmet examples
uv run python -m app.ingest euskalmet-location-forecast \
  --zone donostialdea --location donostia \
  --issued 2026-07-24 --target 2026-07-25
uv run python -m app.ingest euskalmet-alerts \
  --zone GIPUZKOA_COAST --issued 2026-07-24

# Authenticated AEMET bounded history
uv run python -m app.ingest aemet-stations
uv run python -m app.ingest backfill-aemet-daily \
  --station 1012P --start 2024-01-01 --end 2024-01-31

# Bounded Copernicus artifact
uv run python -m app.climate_ingest \
  --year 2024 --month 1 --days 1 \
  --output data/raw/era5-land-2024-01-smoke.nc
```

Dates in this example are illustrative. A release uses one checked-in acquisition
plan with explicit dates, source coverage, and artifact paths.

Embedding synchronization is deliberately separate because it requires OpenAI and
PostgreSQL and is not deterministic source ingestion:

```bash
docker compose up -d --wait postgres
uv run python -m app.db_init
uv run python -m app.ingest knowledge-embeddings
```

## Optional Kestra

`kestra/produce_all_source_snapshot.yaml` invokes the one-command producer with
explicit user inputs and all provider secret mounts. `kestra/create_data_snapshot.yaml`
can separately package and verify an already populated `/data/ingestion.sqlite`.
Neither flow has a schedule, and neither is required by reviewers. Existing source
flows remain available as smaller manual demonstrations.

The flow enforces non-empty knowledge, station, forecast, authenticated-weather, and
AEMET history tables. ERA5 files are added through the equivalent manual command
until the raw-source capture phase gives the flow an explicit acquisition plan.

## Publication Checklist

- Use a clean, newly initialized working database.
- Rotate and validate the previously exposed AEMET token.
- Complete every source command in the acquisition plan.
- Record successful zero-result alert acquisitions separately.
- Attach ERA5 data and its checksum manifest.
- Verify the snapshot after copying it to its final distribution location.
- Run retrieval evaluation against `snapshot.sqlite`.
- Record the snapshot ID and manifest digest in evaluation results.
- Review raw-document and ERA5 redistribution licences.
- Never publish `.env`, tokens, keys, signed URLs, or credential paths.

The current ignored development database does not meet this checklist and must not be
presented as the canonical all-source release.
