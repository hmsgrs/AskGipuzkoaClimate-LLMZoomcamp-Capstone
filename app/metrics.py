"""OpenAI usage, latency, and estimated-cost capture adapted from the course."""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.rag_helper import RAGBase, response_text


MODEL_PRICES_PER_MILLION = {
    "gpt-5.4-mini": (0.75, 4.50),
}


@dataclass(frozen=True)
class LLMCallRecord:
    model: str
    prompt: str
    instructions: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    response_time: float
    cost: float
    route: str
    language: str
    retrieval_backend: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def calculate_cost(model, usage):
    input_price, output_price = MODEL_PRICES_PER_MILLION.get(model, (0.0, 0.0))
    return (
        usage.input_tokens * input_price + usage.output_tokens * output_price
    ) / 1_000_000


class RAGWithMetrics(RAGBase):
    def _generate(self, prompt: str, route: str, language: str):
        started = time.monotonic()
        response = self.llm_client.responses.create(
            model=self.model,
            store=False,
            input=[
                {"role": "developer", "content": self.instructions},
                {"role": "user", "content": prompt},
            ],
        )
        elapsed = time.monotonic() - started
        answer = response_text(response)
        usage = response.usage
        record = LLMCallRecord(
            model=self.model,
            prompt=prompt,
            instructions=self.instructions,
            answer=answer,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            response_time=elapsed,
            cost=calculate_cost(self.model, usage),
            route=route,
            language=language,
            retrieval_backend=self.retrieval_backend,
        )
        return answer, record
