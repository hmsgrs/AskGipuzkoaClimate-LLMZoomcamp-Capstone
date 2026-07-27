"""Persist one human or judge feedback record per conversation."""

from app.db_init import connect


RELEVANCE_LABELS = {"NON_RELEVANT", "PARTLY_RELEVANT", "RELEVANT"}


def save_feedback(
    conversation_id: int,
    source: str,
    relevance: str | None = None,
    explanation: str | None = None,
    score: int | None = None,
    comment: str | None = None,
    connection=None,
):
    if source not in {"user", "judge"}:
        raise ValueError("Feedback source must be user or judge")
    if score not in {None, -1, 1}:
        raise ValueError("Feedback score must be -1, 1, or None")
    if relevance is not None and relevance not in RELEVANCE_LABELS:
        raise ValueError("Invalid relevance label")

    owns_connection = connection is None
    connection = connection or connect()
    try:
        row = connection.execute(
            """
            INSERT INTO feedback (
                conversation_id, source, relevance, explanation, score, comment
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, source) DO UPDATE SET
                relevance=excluded.relevance,
                explanation=excluded.explanation,
                score=excluded.score,
                comment=excluded.comment,
                timestamp=CURRENT_TIMESTAMP
            RETURNING id
            """,
            (conversation_id, source, relevance, explanation, score, comment),
        ).fetchone()
        connection.commit()
        return row[0]
    finally:
        if owns_connection:
            connection.close()
