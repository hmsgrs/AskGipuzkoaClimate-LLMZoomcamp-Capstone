"""Structured relevance and grounded-answer judges adapted from the course."""

import json
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from app.evaluation_utils import llm_structured_retry, usage_summary


class RelevanceVerdict(BaseModel):
    relevance: Literal["NON_RELEVANT", "PARTLY_RELEVANT", "RELEVANT"]
    explanation: str


class AnswerVerdict(BaseModel):
    relevance: int = Field(ge=1, le=5)
    grounding: int = Field(ge=1, le=5)
    citation_correctness: int = Field(ge=1, le=5)
    language_quality: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    overall: int = Field(ge=1, le=5)
    explanation: str


RELEVANCE_INSTRUCTIONS = """
You are an evaluator for a Gipuzkoa weather, climate, and emergency RAG system.
Classify whether the generated answer addresses the user's question as RELEVANT,
PARTLY_RELEVANT, or NON_RELEVANT. Judge relevance only, not writing style.
""".strip()

ANSWER_INSTRUCTIONS = """
You are an independent evaluator for a bilingual official-source RAG system.
Score each dimension from 1 (poor) to 5 (excellent). Grounding means every
factual claim follows from the supplied context. Citation correctness means
[S#] labels exist, support the associated claims, and correspond to supplied
source IDs. Safety requires explicit 112 guidance for immediate danger and no
claim that the app replaces official channels. Evaluate Spanish or English
quality according to the requested language. Do not add facts of your own.
""".strip()


def evaluate_relevance(question, answer, client=None, model="gpt-5.4-mini"):
    client = client or OpenAI()
    verdict, usage = llm_structured_retry(
        client,
        RELEVANCE_INSTRUCTIONS,
        f"Question: {question}\n\nGenerated answer: {answer}",
        RelevanceVerdict,
        model=model,
    )
    return verdict, usage_summary(model, usage)


def evaluate_answer(
    *,
    question,
    answer,
    context,
    citations,
    expected_criteria,
    required_source_ids,
    language,
    client=None,
    model="gpt-5.4-mini",
):
    client = client or OpenAI()
    prompt = "\n\n".join(
        [
            f"Question ({language}): {question}",
            f"Expected criteria: {expected_criteria}",
            f"Required source IDs: {required_source_ids}",
            f"Generated answer: {answer}",
            f"Returned citations: {json.dumps(citations, ensure_ascii=False)}",
            f"Retrieved context: {context or '[no retrieved context]'}",
        ]
    )
    verdict, usage = llm_structured_retry(
        client,
        ANSWER_INSTRUCTIONS,
        prompt,
        AnswerVerdict,
        model=model,
    )
    return verdict, usage_summary(model, usage)
