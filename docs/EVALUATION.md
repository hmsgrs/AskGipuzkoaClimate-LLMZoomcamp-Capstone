# Retrieval Evaluation

## SQLite FTS5 Baseline

Evaluation date: 2026-07-22

The initial baseline uses the six bilingual questions in `evaluation/retrieval_questions.json`. A hit means the expected official source appears in the first five FTS5 results.

| Language | Hits | Questions | Hit rate at 5 |
|---|---:|---:|---:|
| Spanish | 3 | 3 | 100% |
| English | 1 | 3 | 33% |
| Overall | 4 | 6 | 67% |

The live corpus contained 9 active documents and 161 chunks. FTS5 retrieved all expected sources for Spanish questions. It missed the English adverse-weather recommendation and summer-climate questions because the initial corpus is predominantly Spanish and lexical retrieval does not translate query terms.

This is an intentionally small smoke-test dataset, not a final quality claim. The dataset must be expanded before final retrieval selection. The pgvector comparison uses the same expected source IDs.

## pgvector Comparison

The live OpenAI synchronization embedded all 161 active chunks with `text-embedding-3-small`. A second synchronization reported `embedded: 0`, confirming that unchanged chunks do not incur another embedding call. The corpus contains approximately 47,000 embedding tokens, with an estimated initial cost of about $0.001 at $0.02 per million tokens.

| Language | Hits | Questions | FTS5 hit rate at 5 | pgvector hit rate at 5 |
|---|---:|---:|---:|---:|
| Spanish | 3 | 3 | 100% | 100% |
| English | 3 | 3 | 33% | 100% |
| Overall | 6 | 6 | 67% | 100% |

pgvector retrieved every expected source and removed the lexical baseline's English-language gap on this fixture. The result supports semantic retrieval for the initial bilingual application, subject to validation on a larger dataset. The real PostgreSQL integration test separately verifies first-time insertion, idempotency, semantic ranking, SQLite hydration, and stale-vector removal.

Run the baseline with:

```bash
uv run python -m app.ingest knowledge-corpus
uv run python -m app.ingest knowledge-evaluation --limit 5
uv run python -m app.ingest knowledge-embeddings
uv run python -m app.ingest retrieval-comparison --limit 5
```
