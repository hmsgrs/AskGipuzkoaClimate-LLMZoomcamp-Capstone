import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import llm_evaluation
from app.db_feedback import save_feedback
from app.db_init import connect, initialize_application_schema
from app.db_save import save_conversation
from app.judge import AnswerVerdict, RelevanceVerdict, evaluate_answer, evaluate_relevance
from app.metrics import LLMCallRecord
from app.rag_helper import Citation, RAGResult
from app.retrieval_evaluation import evaluate_repository
from app.ingest import get_database


class Usage:
    input_tokens = 50
    output_tokens = 20
    total_tokens = 70


class ParsedResponse:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed
        self.usage = Usage()


class StructuredResponses:
    def __init__(self):
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        output_type = kwargs["text_format"]
        if output_type is RelevanceVerdict:
            return ParsedResponse(
                RelevanceVerdict(relevance="RELEVANT", explanation="On topic")
            )
        return ParsedResponse(
            AnswerVerdict(
                relevance=5,
                grounding=5,
                citation_correctness=5,
                language_quality=5,
                safety=5,
                overall=5,
                explanation="Grounded and safe",
            )
        )


class StructuredClient:
    def __init__(self):
        self.responses = StructuredResponses()


def test_structured_judges_return_verdicts_and_usage():
    client = StructuredClient()

    relevance, relevance_usage = evaluate_relevance(
        "What changed?", "The official report explains it.", client=client
    )
    verdict, answer_usage = evaluate_answer(
        question="What changed?",
        answer="The report says this [S1].",
        context="[S1] Official evidence",
        citations=[{"citation_id": "S1", "source_id": "official"}],
        expected_criteria="Use the official evidence",
        required_source_ids=["official"],
        language="en",
        client=client,
    )

    assert relevance.relevance == "RELEVANT"
    assert relevance_usage["total_tokens"] == 70
    assert verdict.overall == 5
    assert answer_usage["input_tokens"] == 50
    judge_prompt = client.responses.calls[1]["input"][1]["content"]
    assert "Required source IDs: ['official']" in judge_prompt
    assert client.responses.calls[0]["store"] is False


def test_prompt_variant_runner_aggregates_structured_scores(tmp_path, monkeypatch):
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps(
            [
                {
                    "question_id": "one",
                    "question": "Question",
                    "language": "en",
                    "expected_criteria": "Criterion",
                    "required_source_ids": ["official"],
                }
            ]
        ),
        encoding="utf-8",
    )

    class Assistant:
        def __init__(self, variant):
            self.variant = variant

        def ask(self, question):
            return RAGResult(
                answer=self.variant,
                citations=(
                    Citation("S1", "official", "Title", "Authority", "https://test"),
                ),
                route="knowledge_base",
                language="en",
                retrieval_backend="sqlite_fts5",
                context="[S1] Evidence",
            )

    monkeypatch.setattr(
        llm_evaluation,
        "create_evaluation_assistant",
        lambda database, backend, client, generation_model, variant: Assistant(variant),
    )

    def fake_judge(**kwargs):
        score = 5 if kwargs["answer"] == "citation_safety" else 3
        return (
            AnswerVerdict(
                relevance=score,
                grounding=score,
                citation_correctness=score,
                language_quality=score,
                safety=score,
                overall=score,
                explanation="evaluated",
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(llm_evaluation, "evaluate_answer", fake_judge)
    report = llm_evaluation.evaluate_prompt_variants(
        database=tmp_path / "unused.sqlite",
        questions_path=questions,
        backend="sqlite_fts5",
        client=object(),
    )

    assert len(report["results"]) == 2
    assert report["summaries"]["course_baseline"]["overall"] == 3
    assert report["summaries"]["citation_safety"]["overall"] == 5


def test_retrieval_evaluation_reports_mrr(tmp_path):
    database = tmp_path / "evaluation.sqlite"
    connection = get_database(database)
    connection.execute(
        "INSERT INTO evaluation_questions VALUES (?, ?, ?, ?, ?)",
        ("q1", "Question", "en", "expected", "test"),
    )
    connection.commit()
    connection.close()

    class Repository:
        def search(self, question, limit):
            return [{"source_id": "other"}, {"source_id": "expected"}]

    report = evaluate_repository(Repository(), "fake", database)

    assert report["hit_rate"] == 1.0
    assert report["mrr"] == 0.5
    assert report["details"][0]["rank"] == 2


def test_feedback_rejects_invalid_values_before_connecting():
    with pytest.raises(ValueError, match="source"):
        save_feedback(1, "unknown")
    with pytest.raises(ValueError, match="score"):
        save_feedback(1, "user", score=0)


def test_grafana_dashboard_contains_required_showcase_panels():
    dashboard_path = Path(__file__).parents[1] / "grafana" / "dashboards" / "askgipuzkoa.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    titles = {panel["title"] for panel in dashboard["panels"]}

    assert {
        "Questions over time",
        "Spanish vs English",
        "Question routes",
        "Answer latency",
        "User feedback",
        "Token usage",
    } <= titles
    assert dashboard["templating"]["list"] == []
    assert all(
        panel["datasource"]["uid"] == "askgipuzkoa-postgres"
        for panel in dashboard["panels"]
    )
    provisioning = (
        Path(__file__).parents[1]
        / "grafana"
        / "provisioning"
        / "datasources"
        / "askgipuzkoa.yaml"
    ).read_text(encoding="utf-8")
    assert "uid: askgipuzkoa-postgres" in provisioning
    assert "$GRAFANA_DATABASE_PASSWORD" in provisioning
    assert "password" not in dashboard_path.read_text(encoding="utf-8").casefold()


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_conversation_and_feedback_persistence_round_trip():
    call = LLMCallRecord(
        model="test-model",
        prompt="prompt",
        instructions="instructions",
        answer="answer",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        response_time=0.2,
        cost=0.01,
        route="knowledge_base",
        language="en",
        retrieval_backend="pgvector",
        timestamp=datetime.now(UTC),
    )
    result = RAGResult(
        answer="answer",
        citations=(Citation("S1", "source", "Title", "Org", "https://test"),),
        route="knowledge_base",
        language="en",
        retrieval_backend="pgvector",
        call=call,
        context="context",
    )

    with connect(os.environ["TEST_DATABASE_URL"]) as connection:
        initialize_application_schema(connection)
        conversation_id = save_conversation(result, "question", connection)
        first_feedback = save_feedback(
            conversation_id, "user", score=1, comment="useful", connection=connection
        )
        second_feedback = save_feedback(
            conversation_id, "user", score=-1, comment="changed", connection=connection
        )
        row = connection.execute(
            "SELECT route, language, citations, status FROM conversations WHERE id=%s",
            (conversation_id,),
        ).fetchone()
        feedback = connection.execute(
            "SELECT score, comment FROM feedback WHERE id=%s", (first_feedback,)
        ).fetchone()
        connection.execute("DELETE FROM conversations WHERE id=%s", (conversation_id,))
        connection.commit()

    assert first_feedback == second_feedback
    assert row[0:2] == ("knowledge_base", "en")
    assert row[2][0]["source_id"] == "source"
    assert row[3] == "success"
    assert feedback == (-1, "changed")
