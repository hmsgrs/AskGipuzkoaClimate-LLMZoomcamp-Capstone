# Retrieval And LLM Evaluation

## SQLite FTS5 Baseline

Evaluation date: 2026-07-22

The initial baseline uses the six bilingual questions in `evaluation/retrieval_questions.json`. A hit means the expected official source appears in the first five FTS5 results.

| Language | Hits | Questions | Hit rate at 5 |
|---|---:|---:|---:|
| Spanish | 3 | 3 | 100% |
| English | 1 | 3 | 33% |
| Overall | 4 | 6 | 67% |

The evaluated corpus is committed in snapshot `gipuzkoa-demo-2026-07-22` with database SHA-256 `f2b02b8ae386b69b0e35375b9dae49b2f33941c8327a78ffb5ceccc750a56094`. It contains 9 active documents and 161 chunks. FTS5 retrieved all expected sources for Spanish questions. It missed the English adverse-weather recommendation and summer-climate questions because the corpus is predominantly Spanish and lexical retrieval does not translate query terms.

This is an intentionally small smoke-test dataset, not a final quality claim. The dataset must be expanded before final retrieval selection. The pgvector comparison uses the same expected source IDs.

## pgvector Comparison

The live OpenAI synchronization embedded all 161 active chunks with `text-embedding-3-small`. A second synchronization reported `embedded: 0`, confirming that unchanged chunks do not incur another embedding call. The corpus contains approximately 47,000 embedding tokens, with an estimated initial cost of about $0.001 at $0.02 per million tokens.

| Language | Hits | Questions | FTS5 hit rate at 5 | pgvector hit rate at 5 |
|---|---:|---:|---:|---:|
| Spanish | 3 | 3 | 100% | 100% |
| English | 3 | 3 | 33% | 100% |
| Overall | 6 | 6 | 67% | 100% |

FTS5 MRR at 5 is 0.67. pgvector MRR at 5 is 0.92; five expected sources rank first and the winter report ranks second.

pgvector retrieved every expected source and removed the lexical baseline's English-language gap on this fixture. The result supports semantic retrieval for the initial bilingual application, subject to validation on a larger dataset. The real PostgreSQL integration test separately verifies first-time insertion, idempotency, semantic ranking, SQLite hydration, and stale-vector removal.

Run the baseline with:

```bash
uv run python -m app.ingest knowledge-corpus
uv run python -m app.ingest knowledge-evaluation --limit 5
uv run python -m app.ingest knowledge-embeddings
uv run python -m app.ingest retrieval-comparison --limit 5
```

## Prompt And LLM-as-Judge Evaluation

Evaluation date: 2026-07-24

The six bilingual cases in `evaluation/llm_questions.json` cover climate definition, official preparedness guidance, historical climate, and immediate-danger safety. Each case defines answer criteria and required source IDs. `app/llm_evaluation.py` generates answers with two prompt variants and uses a structured independent call to score relevance, grounding, citation correctness, language quality, safety, and overall quality from 1 to 5.

| Metric | Course baseline | Citation and safety prompt |
|---|---:|---:|
| Relevance | 5.00 | 4.83 |
| Grounding | 4.83 | 4.67 |
| Citation correctness | 1.83 | 4.50 |
| Language quality | 5.00 | 5.00 |
| Safety | 5.00 | 4.67 |
| Overall | 3.67 | **4.83** |
| Citation contract rate | 16.7% | **100%** |
| Required source recall | 16.7% | **100%** |

The citation and safety prompt is selected. Its explicit `[S#]` contract materially improves citation correctness while preserving relevance and bilingual quality. All six cases satisfy the deterministic citation contract and return every required source. The emergency case correctly bypasses generation and directs the user to `112`.

The first evaluation run exposed a routing bug: the English phrase "adverse-weather recommendations" was incorrectly treated as a live forecast. Deterministic preparedness terms now take precedence, and the corrected evaluation retrieves `euskalmet-recommendations` through pgvector. This demonstrates the evaluation workflow finding and correcting behavior rather than only reporting scores.

Residual weakness: the summer-history answer sometimes adds a directly related July report even when the seasonal report is sufficient. The selected prompt reduces unrelated source cards, but larger fixtures and source-aware reranking remain future work.

The hardened run used 53,853 tokens with an estimated generation cost of $0.0225 and judge cost of $0.0277. Compact results are committed in `evaluation/results/llm_evaluation_summary.json`; full answer-level reports can be regenerated with:

```bash
uv run python -m app.llm_evaluation \
  --database data/processed/ingestion.sqlite \
  --output evaluation/results/llm_evaluation.json
```

LLM-as-Judge scores are model-based estimates, not ground truth. The generation and judge currently use the same configured model, so future evaluation should add human review or an independent judge model.
