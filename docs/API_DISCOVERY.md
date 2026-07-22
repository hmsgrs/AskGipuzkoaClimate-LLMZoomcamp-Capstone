# API Discovery Spike

Discovery date: 2026-07-21

This spike tested the availability and access requirements of the initial data sources for Gipuzkoa. It separates normal weather data from hazard alerts and long-term climate data.

## Source Classification

| Data type | Meaning | Retrieval path |
|---|---|---|
| `weather` | Current observations and short-term forecasts | Structured API or public forecast feed |
| `hazard_alert` | Official current or forecast warning for severe weather, flood, fire, or coastal conditions | Official alert API or official homepage warning card |
| `climate_history` | Historical observations, reanalysis, and climate bulletins | Historical datasets and official reports |
| `climate_risk_guidance` | Projections, adaptation, and preparedness guidance | Official documents |

`hazard_alert` is deliberately distinct from `climate_history`: a current severe-weather warning is not a climate projection.

## Confirmed Sources

| Source | Availability confirmed | Project use | Access decision |
|---|---|---|---|
| [Euskalmet station network GeoJSON](https://opendata.euskadi.eus/contenidos/ds_meteorologicos/estaciones_meteorologicas/opendata/estaciones.geojson) | HTTP 200, public GeoJSON | Gipuzkoa station catalogue, location, provider identifiers, and station metadata | Ingest directly without credentials |
| [Euskalmet forecast XML](https://opendata.euskadi.eus/contenidos/prevision_tiempo/met_forecast/opendata/met_forecast.xml) | HTTP 200, public XML | Short-term normal-weather fallback; bilingual forecast narrative and city values, including Donostia-San Sebastian and Mondragon | Poll directly without credentials |
| [Euskalmet Meteo API](https://opendata.euskadi.eus/api-euskalmet/) | Documentation lists location forecasts, station readings, basin data, radar reports, and alert forecasts | Primary live weather and hazard-alert data | Registration and API keys required |
| [Euskalmet climatological bulletins](https://www.euskalmet.euskadi.eus/clima/boletines-climatologicos/) | Public index exposes annual, seasonal, and monthly PDF reports | Historical climate RAG corpus | Targeted official-document ingestion |
| [Copernicus ERA5-Land](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=overview) | Historical hourly land reanalysis from 1950 to present at 0.1-degree resolution | Derived Gipuzkoa temperature, precipitation, soil-moisture, and drought indicators | CDS registration and licence acceptance required |
| [AEMET OpenData](https://opendata.aemet.es/centrodedescargas/inicio) | Official OpenData service available | Historical daily station data and national warning fallback | API key required before payload validation |
| [AEMET warnings](https://www.aemet.es/es/eltiempo/prediccion/avisos) | Official warning page is public and exposes a dynamically named GeoJSON download | Human-facing fallback only | Do not scrape dynamic download URLs when the API is available |
| [Euskalmet homepage](https://www.euskalmet.euskadi.eus/webmet00-home/es/) | Public page renders current Meteoadversa warning cards | No-key Euskalmet-only alert fallback | Ingest explicit warning cards only |

The public station GeoJSON contains Gipuzkoa stations, including coastal, inland, river, and reservoir locations such as Lasarte, Erenozu, Urkulu, Oiartzun, and Pasaia. It is the source of truth for the project station allowlist. Its per-station `xmldatos` URLs currently expose station metadata, not live readings.

## Access Constraints

### Euskalmet API

The unauthenticated request to the documented municipality forecast route returned HTTP 403. Euskalmet's Open Data page explicitly requires registration for Meteo API use. The authenticated API is therefore required for live station readings and alert forecasts.

The registered RSA key pair signs an RS256 JWT whose non-sensitive header and claims match the provider guide: `alg=RS256`, `typ=JWT`, `aud=met01.apikey`, descriptive application `iss`, `version=1.0.0`, `iat`, one-hour `exp`, owner email, and the `fingerPrint.txt` value as `loginId`. Authentication is validated against the production API. The earlier HTTP 403 responses were caused by an invalid inferred route, `/meteorology/v1.0/forecast/at/municipality/20069`. The current official contract uses `/euskalmet` and hierarchical region, zone, and location IDs.

Required follow-up:

1. Confirm the key pair is active in the Euskalmet API portal.
2. Confirm the required alert and station-reading response schemas.
3. Keep the private key and fingerprint outside Git through the configured file paths.

### AEMET OpenData

The AEMET client now follows the official OpenData reference's raw-JWT API-key flow and strips the optional wrapper used by the local credential file. The authenticated inventory smoke test succeeded and stored 16 Gipuzkoa stations. A three-day daily-observation request for station `1012P` also succeeded and stored three records. The client sanitizes HTTP errors so its token is never included in logs or exceptions.

Required follow-up:

1. Select the representative Gipuzkoa AEMET station series for the historical backfill.
2. Validate the warning response schema before enabling AEMET alert ingestion.

## Ingestion Decisions

### Normal Weather Flow

Use the public Euskalmet forecast XML immediately for a low-complexity initial forecast feature. Once Euskalmet credentials are available, replace or supplement it with the API's municipality forecasts and station readings.

Persist structured measurements separately from the RAG corpus. Numeric weather records are queried directly at answer time and must include a provider and retrieval timestamp.

### Hazard Alert Flow

Use the authenticated Euskalmet alert-forecast endpoint as the primary structured source. The public Euskalmet homepage is a no-key fallback for its visible warning cards. The project does not ingest social-media posts.

Store each alert with:

- Provider
- Source URL and publication time
- Phenomenon, severity, area, and validity window when explicitly stated
- Raw text and linked official page
- Retrieval time and expiry state

The answer flow must never infer alert severity, location, or validity from raw measurements or incomplete warning-card wording.

### Historical Climate Flow

Start with ERA5-Land derived indicators and a small set of AEMET station series after credentials are available. Add Euskalmet monthly and seasonal bulletins as RAG documents.

The available Euskalmet annual report index includes very large files. The first ingestion scope must exclude files larger than 10 MB and begin with monthly or seasonal PDFs. Do not bulk-download the historical annual-report archive.

### Web Scraping Flow

Scraping is limited to allowlisted official Euskalmet and Basque Government HTML/PDF pages. The Euskalmet homepage is an explicit exception for its own visible warning-card summaries. The project does not retrieve social-media content.

## Initial Source Registry

| ID | Type | Refresh | Status |
|---|---|---:|---|
| `euskalmet-stations` | `weather` metadata | Monthly | Ingested successfully into SQLite |
| `euskalmet-forecast-xml` | `weather` | Hourly | Ingested successfully into SQLite |
| `euskalmet-api` | `weather`, `hazard_alert` | 15 minutes for alerts; hourly for forecasts/readings | Authentication, geo catalogue, Donostia forecast, and Gipuzkoa coast alerts validated |
| `aemet-opendata` | `climate_history`, `hazard_alert` | Daily historical update; 15 minutes for alerts | Station inventory and daily-observation access validated |
| `era5-land` | `climate_history` | Monthly | Bounded retrieval smoke test validated through CDS credentials |
| `euskalmet-climate-reports` | `climate_history` | Monthly | Four seasonal and two monthly PDFs registered with a 10 MB limit |
| `official-knowledge-corpus` | `climate_history`, `climate_risk_guidance` | Monthly | Canonical documents, deterministic chunks, FTS5 index, and bilingual fixture implemented |
| `euskalmet-homepage` | `hazard_alert` | 15 minutes | Implemented no-key fallback for explicit warning cards |

## Next Implementation Step

The canonical Euskalmet/Basque Government corpus is the current implementation focus. Its registry, HTML/PDF normalization, versioned SQLite documents, deterministic chunks, FTS5 repository, and bilingual retrieval fixture are implemented. The next retrieval step is to establish the live FTS5 baseline and compare it with pgvector embeddings.
