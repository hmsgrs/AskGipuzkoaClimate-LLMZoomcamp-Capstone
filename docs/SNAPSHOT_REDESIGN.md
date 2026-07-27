# Snapshot-First Redesign

## Decision

The project will use immutable, versioned data snapshots as the default audit and
application input. Provider ingestion remains available through explicit Python
commands and optional manually triggered Kestra flows, but reviewers do not need
provider credentials or Kestra to run retrieval, evaluation, or the application.

This is a bounded rearchitecture rather than a rewrite. Existing provider clients,
document extraction, deterministic chunking, SQLite FTS5, pgvector retrieval, RAG,
evaluation, Streamlit, feedback, and monitoring code remain useful.

## Implementation Status

The repository now commits scoped snapshot `gipuzkoa-demo-2026-07-22` and a portable
`text-embedding-3-small` export bound to all 161 chunk hashes. The default Compose
runtime verifies both artifacts, imports vectors into a fresh pgvector volume,
provisions Streamlit and Grafana, and requires only an OpenAI key for new queries and
answers. This satisfies the reviewer path; the broader canonical all-source release
remains blocked on AEMET token rotation and complete hazard/history coverage.

## Why Change

The original architecture gave production ingestion more weight than the course
application:

- Twenty ingestion commands and nine independently scheduled Kestra flows mutate a
  shared database.
- AEMET, Euskalmet, CDS, OpenAI, PostgreSQL, and Kestra credentials are needed to
  recreate all generated state.
- Generated SQLite, NetCDF, and vector data are ignored by Git, so a clean clone
  cannot reproduce the documented retrieval results.
- Operational weather and climate acquisition were implemented before the complete
  RAG, evaluation, interface, and monitoring experience.
- Re-fetching mutable webpages does not recreate the corpus used for an earlier
  evaluation.

The redesign makes one acquisition window a permanent, verifiable input for all
retrieval and LLM strategies.

## Goals

1. A reviewer can verify and use a published snapshot without data-provider keys.
2. Retrieval and evaluation run against an identified, unchanged corpus.
3. Weather and warning answers clearly state that snapshot data is historical.
4. Manual Python commands are the canonical ingestion implementation.
5. Kestra remains an optional orchestration demonstration, not a runtime prerequisite.
6. Snapshot data and mutable application monitoring data remain separate.
7. Every released artifact has provenance, a byte count, and a SHA-256 digest.

## Non-Goals

- The application is not an official live-warning channel.
- A snapshot does not claim all providers were queried at the exact same instant.
- LLM responses are not treated as deterministic data artifacts.
- Conversation and feedback rows are not included in immutable source snapshots.
- The first migration does not remove working legacy ingestion commands before the
  replacement capture path is verified.

## Architecture

```text
Optional producers
  manual Python ingestion commands
  optional manually triggered Kestra flows
                |
                v
  mutable working SQLite + ERA5/other files
                |
         snapshot create
                |
                v
Immutable snapshot release
  manifest.json + manifest.sha256
  snapshot.sqlite
  artifacts/<sha256>-<name>
                |
      verify / inspect / install
                |
                v
Application and evaluation
  SQLite FTS5
  semantic retrieval / pgvector
  hybrid search, reranking, query rewriting
  RAG and agent/tool routing
  retrieval and LLM evaluation
  Streamlit

Separate mutable runtime state
  PostgreSQL conversations and feedback
  Grafana dashboards
```

## Three User Paths

### Audit Path

The default reviewer path requires no provider credentials:

1. Obtain a published snapshot from the repository or a release asset.
2. Verify its manifest and all artifact hashes.
3. Point the application directly at `snapshot.sqlite` in snapshot mode, or install a
   disposable working copy.
4. Run retrieval evaluation and inspect recorded evaluation results.
5. Supply only an LLM key when generating new answers or query embeddings.

### Manual Refresh Path

Maintainers explicitly run bounded source commands, then publish the resulting state:

1. Start with a new working database.
2. Run the documented public and authenticated ingestion commands.
3. Generate climate artifacts and embeddings as required.
4. Create and verify a snapshot.
5. Publish only after source coverage and validation pass.

Provider credentials are needed only for this path.

### Kestra Path

Kestra demonstrates course orchestration by invoking the same Python commands. It is
an optional Compose profile and its flows are manually triggered for snapshot
production. Scheduled continuous refresh is not required for the audit application.

## Snapshot Semantics

A snapshot represents an immutable **acquisition window**, not an instantaneous
measurement. The manifest records:

- snapshot schema version and stable snapshot ID;
- creation time and the minimum/maximum source retrieval timestamps found in SQLite;
- project revision and whether the producer worktree was dirty;
- Python version;
- SQLite schema version, integrity result, table counts, byte count, and digest;
- each additional artifact's relative path, media type, byte count, and digest;
- source-coverage status and optional release notes.

Creation uses SQLite's online backup API. Copying a WAL-mode `.sqlite` file directly
is forbidden because committed pages may still be in the WAL file.

Snapshot creation writes to a temporary sibling directory, verifies the result, and
publishes it with an atomic rename. Existing snapshot IDs are never overwritten.

## Bundle Layout

```text
data/snapshots/<snapshot-id>/
  manifest.json
  manifest.sha256
  snapshot.sqlite
  artifacts/
    <sha256>-<original-name>
```

The SQLite artifact contains all database-backed sources currently loaded:

- official knowledge documents and deterministic chunks;
- FTS5 index and retrieval fixtures;
- Euskalmet and AEMET station catalogues;
- public and authenticated forecasts;
- official alert snapshots and homepage warning cards;
- AEMET bounded historical observations;
- snapshot build metadata.

ERA5-Land NetCDF files, request manifests, portable embedding exports, raw documents,
and machine-readable evaluation outputs can be attached as additional artifacts.
Large or licence-restricted artifacts should be release assets referenced by digest.

## Application Behavior

The application opens the canonical snapshot database read-only. Schema creation and
ingestion never run from retrieval methods.

Snapshot weather, forecast, and warning results are always historical. The UI must
display the acquisition window and must never call a frozen warning "active". The
existing emergency notice and `112` rule remain mandatory.

The PostgreSQL database used for pgvector, conversations, feedback, and Grafana is
disposable runtime state. If pgvector is used, canonical document text and a portable
embedding export remain part of the snapshot so the PostgreSQL volume is not the only
copy of generated retrieval data.

## Course Strategy Layer

Every strategy is developed and compared over the same snapshot:

1. SQLite FTS5 keyword retrieval.
2. Semantic vector retrieval.
3. Hybrid keyword/vector retrieval.
4. Document reranking.
5. Query rewriting.
6. Agentic tool selection between documents, forecasts, warnings, observations, and
   climate indicators.
7. Multiple RAG prompt designs.
8. Retrieval evaluation with hit rate and MRR.
9. LLM-as-a-Judge evaluation for relevance, grounding, citations, language, and safety.
10. Online feedback and monitoring over separate mutable runtime tables.

## Kestra Decision

Python commands are the source of truth. Kestra flow definitions only orchestrate
those commands. The target is one manually triggered snapshot-production flow or a
small set of source-group subflows with no enabled schedules.

This preserves the course's automated-ingestion demonstration while ensuring that:

- cloning and auditing do not require Kestra;
- missing private API keys do not block the application;
- provider secrets never enter snapshots or logs;
- the exact published data remains available after upstream sources change.

## Migration

### Phase 1: Immutable Release Boundary

- Add `create`, `verify`, `inspect`, and `install` snapshot commands.
- Use WAL-safe SQLite backup and atomic publication.
- Package ERA5 and other files as hashed artifacts.
- Add snapshot metadata to the copied database.
- Make retrieval read-only and deterministic.
- Add an optional manual Kestra snapshot flow.

### Phase 2: No-Network Rebuild

- Capture original HTML, PDF, XML, JSON, and NetCDF representations before
  normalization.
- Store content-addressed objects and per-source request/response provenance.
- Build SQLite from captured artifacts without network access.
- Preserve captured retrieval timestamps instead of assigning build time.
- Version the normalizer and chunker and include chunk text hashes.

### Phase 3: Portable Semantic Retrieval

- Export document embeddings with model, dimensions, text hash, binary format, and
  vector digest.
- Seed pgvector from the snapshot, or support exact local cosine search for the small
  corpus.
- Commit query embeddings for fully offline retrieval evaluation where licensing and
  model terms permit it.

### Phase 4: Strategy Completion

- Add hybrid retrieval, reranking, and query rewriting.
- Expose snapshot-backed weather/climate tools to the agentic flow.
- Expand retrieval and LLM evaluation datasets.
- Record snapshot identity in every evaluation report and conversation metric.

### Phase 5: Simplification

- Remove recurring schedules, repair windows, watermarks, and stale-vector cleanup
  that are no longer needed for snapshot production.
- Retire legacy commands only after equivalent snapshot capture handlers and tests
  exist.
- Keep one documented optional Kestra orchestration path.

## Reuse and Retirement

### Retain

- AEMET, Euskalmet, homepage, and ERA5 clients.
- Official source registry and allowlisting.
- HTML/PDF extraction and bounded download safeguards.
- Deterministic document/chunk identifiers and FTS5 retrieval.
- Semantic retrieval and hydration contracts.
- RAG, citations, prompt evaluation, Streamlit, persistence, and Grafana work.

### Simplify or Retire

- Nine enabled recurring schedules.
- Twenty public ingestion entry points as the primary user experience.
- Cross-flow WAL/locking complexity after snapshot capture becomes isolated.
- Mutable active-version logic inside one released snapshot.
- Stale-vector deletion as a release concern.
- Unexported PostgreSQL volumes as the only copy of embeddings.

## Release Requirements

A snapshot is publishable only when:

- the manifest and every artifact verify;
- `PRAGMA integrity_check` returns `ok`;
- required table counts and source coverage are recorded;
- the database was built from a clean schema rather than an old local migration state;
- credential-like values are absent from the manifest;
- AEMET token rotation and authenticated-source validation are complete;
- no-alert results are recorded as successful zero-result acquisitions rather than
  missing ingestion;
- redistribution rights for raw PDFs and ERA5 data have been reviewed;
- retrieval evaluation is rerun and linked to the snapshot digest;
- weather and warning presentation is explicitly historical.

## Known Current Blockers

The existing local SQLite database is useful for development but is not a canonical
release: it contains schema drift, mixed acquisition times, no AEMET observations,
and no hazard-alert rows. The exposed AEMET token must be rotated before the first
complete all-source snapshot is captured. A canonical release therefore cannot be
claimed solely from the existing ignored files.

## Success Criteria

The redesign is complete when a new reviewer can:

1. clone the repository;
2. obtain and verify one named snapshot;
3. run FTS retrieval and recorded evaluations without provider credentials;
4. start the application with only the credentials needed for new LLM calls;
5. see the snapshot acquisition window and historical-data warning;
6. optionally inspect or execute the Kestra ingestion demonstration;
7. reproduce a new snapshot manually when all provider credentials are available.
