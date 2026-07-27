"""Persist RAG conversations for feedback and Grafana monitoring."""

import json

from app.db_init import connect


def save_conversation(result, question: str, connection=None):
    owns_connection = connection is None
    connection = connection or connect()
    try:
        call = result.call
        citations = [citation.__dict__ for citation in result.citations]
        row = connection.execute(
            """
            INSERT INTO conversations (
                question, answer, route, language, retrieval_backend,
                model, instructions, prompt, prompt_tokens, completion_tokens,
                total_tokens, response_time, cost, citations, status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s
            )
            RETURNING id
            """,
            (
                question,
                result.answer,
                result.route,
                result.language,
                result.retrieval_backend,
                call.model if call else None,
                call.instructions if call else None,
                call.prompt if call else None,
                call.prompt_tokens if call else 0,
                call.completion_tokens if call else 0,
                call.total_tokens if call else 0,
                call.response_time if call else 0.0,
                call.cost if call else 0.0,
                json.dumps(citations),
                "success" if result.citation_valid else "citation_failed",
            ),
        ).fetchone()
        connection.commit()
        return row[0]
    finally:
        if owns_connection:
            connection.close()
