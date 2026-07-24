"""Compare two prompt designs with a structured LLM-as-Judge."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from openai import OpenAI

from app.assistant import WeatherClimateAssistant
from app.judge import evaluate_answer
from app.metrics import RAGWithMetrics
from app.openai_client import OpenAIEmbeddingClient
from app.pgvector_repository import PgvectorRepository
from app.rag_helper import INSTRUCTIONS, PROMPT_TEMPLATE
from app.sqlite_repository import SQLiteRepository
from app.weather_api import CachedWeatherRepository


BASELINE_INSTRUCTIONS = """
Answer the question using only the supplied context. Answer in the same language
as the question. If the context does not contain the answer, say that you do not
know.
""".strip()

BASELINE_PROMPT = """
QUESTION: {question}

CONTEXT:
{context}
""".strip()

PROMPT_VARIANTS = {
    "course_baseline": {
        "instructions": BASELINE_INSTRUCTIONS,
        "prompt_template": BASELINE_PROMPT,
    },
    "citation_safety": {
        "instructions": INSTRUCTIONS,
        "prompt_template": PROMPT_TEMPLATE,
    },
}


def load_questions(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def create_evaluation_assistant(
    database,
    backend,
    client,
    generation_model,
    variant,
):
    if backend == "pgvector":
        repository = PgvectorRepository(
            database,
            embedding_client=OpenAIEmbeddingClient(client=client),
        )
    elif backend == "sqlite_fts5":
        repository = SQLiteRepository(database)
    else:
        raise ValueError("backend must be pgvector or sqlite_fts5")
    settings = PROMPT_VARIANTS[variant]
    knowledge_rag = RAGWithMetrics(
        repository,
        client,
        instructions=settings["instructions"],
        prompt_template=settings["prompt_template"],
        model=generation_model,
        retrieval_backend=backend,
        require_citations=variant == "citation_safety",
    )
    weather_rag = RAGWithMetrics(
        CachedWeatherRepository(database),
        client,
        instructions=settings["instructions"],
        prompt_template=settings["prompt_template"],
        model=generation_model,
        retrieval_backend="cached_official_weather",
        require_citations=variant == "citation_safety",
    )
    return WeatherClimateAssistant(knowledge_rag, weather_rag)


def evaluate_prompt_variants(
    *,
    database: Path,
    questions_path: Path,
    backend="pgvector",
    generation_model="gpt-5.4-mini",
    judge_model="gpt-5.4-mini",
    client=None,
):
    client = client or OpenAI()
    questions = load_questions(questions_path)
    results = []
    for variant in PROMPT_VARIANTS:
        assistant = create_evaluation_assistant(
            database,
            backend,
            client,
            generation_model,
            variant,
        )
        for item in questions:
            answer = assistant.ask(item["question"])
            citations = [asdict(citation) for citation in answer.citations]
            returned_source_ids = [citation["source_id"] for citation in citations]
            required_sources_present = set(item["required_source_ids"]) <= set(
                returned_source_ids
            )
            verdict, judge_usage = evaluate_answer(
                question=item["question"],
                answer=answer.answer,
                context=answer.context,
                citations=citations,
                expected_criteria=item["expected_criteria"],
                required_source_ids=item["required_source_ids"],
                language=item["language"],
                client=client,
                model=judge_model,
            )
            results.append(
                {
                    "question_id": item["question_id"],
                    "variant": variant,
                    "route": answer.route,
                    "answer": answer.answer,
                    "citation_source_ids": returned_source_ids,
                    "citation_contract_valid": answer.citation_valid,
                    "required_sources_present": required_sources_present,
                    "generation_usage": asdict(answer.call) if answer.call else None,
                    "verdict": verdict.model_dump(),
                    "judge_usage": judge_usage,
                }
            )

    summaries = {}
    for variant in PROMPT_VARIANTS:
        variant_rows = [row for row in results if row["variant"] == variant]
        summaries[variant] = {
            metric: sum(row["verdict"][metric] for row in variant_rows)
            / len(variant_rows)
            for metric in (
                "relevance",
                "grounding",
                "citation_correctness",
                "language_quality",
                "safety",
                "overall",
            )
        }
        summaries[variant]["citation_contract_rate"] = sum(
            row["citation_contract_valid"] for row in variant_rows
        ) / len(variant_rows)
        summaries[variant]["required_source_recall"] = sum(
            row["required_sources_present"] for row in variant_rows
        ) / len(variant_rows)
    return {
        "backend": backend,
        "generation_model": generation_model,
        "judge_model": judge_model,
        "questions": len(questions),
        "summaries": summaries,
        "results": results,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run bilingual LLM RAG evaluation.")
    parser.add_argument(
        "--database", type=Path, default=Path("data/processed/ingestion.sqlite")
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("evaluation/llm_questions.json"),
    )
    parser.add_argument("--backend", choices=("pgvector", "sqlite_fts5"), default="pgvector")
    parser.add_argument("--generation-model", default="gpt-5.4-mini")
    parser.add_argument("--judge-model", default="gpt-5.4-mini")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    report = evaluate_prompt_variants(
        database=args.database,
        questions_path=args.questions,
        backend=args.backend,
        generation_model=args.generation_model,
        judge_model=args.judge_model,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
