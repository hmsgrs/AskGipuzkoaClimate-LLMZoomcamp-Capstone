# Monitoring

With explicit user consent, the application records answer-generation events and feedback in local PostgreSQL using the course's conversation/feedback pattern. `app/db_init.py` creates the schema idempotently, `app/db_save.py` persists answers and citation-contract status, and `app/db_feedback.py` upserts one human and one judge verdict per conversation. Storage is disabled by default in Streamlit.

## Stored Dimensions

The `conversations` table stores the question and answer, route, language, retrieval backend, model, prompt, token counts, latency, estimated cost, source cards, status, and UTC timestamp. The `feedback` table stores user scores/comments and LLM relevance verdicts.

The application does not store IP addresses or browser identifiers. If storage is enabled, questions, answers, grounded prompts, and optional comments remain until manually deleted. A public deployment therefore needs an explicit privacy and retention policy even though OpenAI response storage is disabled with `store=False`.

## Dashboard

Import `grafana/dashboards/askgipuzkoa.json` and select a PostgreSQL datasource with access to the application tables. The dashboard includes:

1. Questions over time
2. Spanish versus English usage
3. Live-weather, knowledge-base, and emergency route distribution
4. Average and p95 answer latency
5. Positive versus negative user feedback
6. LLM-as-Judge relevance distribution
7. Token usage
8. Estimated generation cost

All time-series queries use Grafana's selected-range macros and the dashboard refreshes every 30 seconds. The default view is the last six hours.

## Selected Local Topology

The selected milestone topology adds Streamlit and Grafana to the existing Compose file. Streamlit will bind to `127.0.0.1:8501`; Grafana will bind to `127.0.0.1:3000` with its own login. PostgreSQL will use separate least-privilege application-writer and Grafana read-only roles. Consented records remain until manually deleted. These choices are recorded here but the services and role provisioning still need implementation.
