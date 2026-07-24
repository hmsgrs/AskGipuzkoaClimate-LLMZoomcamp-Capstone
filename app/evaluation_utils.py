"""Structured OpenAI helpers adapted from the course evaluation module."""

import time

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from app.metrics import calculate_cost


TRANSIENT_OPENAI_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


def llm_structured(client, instructions, user_prompt, output_type, model):
    response = client.responses.parse(
        model=model,
        store=False,
        input=[
            {"role": "developer", "content": instructions},
            {"role": "user", "content": user_prompt},
        ],
        text_format=output_type,
    )
    if getattr(response, "status", None) not in {None, "completed"}:
        raise RuntimeError("Structured OpenAI response did not complete")
    if response.output_parsed is None:
        raise RuntimeError("OpenAI returned no structured evaluation")
    return response.output_parsed, response.usage


def llm_structured_retry(
    client,
    instructions,
    user_prompt,
    output_type,
    model="gpt-5.4-mini",
    max_retries=3,
):
    for attempt in range(max_retries):
        try:
            return llm_structured(
                client,
                instructions,
                user_prompt,
                output_type,
                model,
            )
        except TRANSIENT_OPENAI_ERRORS:
            if attempt == max_retries - 1:
                raise
            time.sleep(2**attempt)


def usage_summary(model, usage):
    return {
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "estimated_cost": calculate_cost(model, usage),
    }
