"""Course-style RAG orchestration with application-controlled citations."""

import re
from dataclasses import dataclass
from typing import Any


INSTRUCTIONS = """
You answer questions about weather, climate risks, and emergency preparedness in
Gipuzkoa using only the supplied official context.

Answer in the same language as the question (Spanish or English). Cite factual
claims with the provided source labels such as [S1]. Never invent a source or
claim that is absent from the context. If the context is insufficient, say so
clearly and direct the user to the linked official sources. Distinguish current
weather from long-term climate information and call out stale live data. Use the
smallest set of directly relevant sources; do not introduce adjacent dates,
locations, or reports unless they are necessary to answer the question. Do not
write URLs in the answer; the application renders trusted source links separately.
Warning context contains cached snapshots, so never claim a warning is active
unless its evidence explicitly establishes current validity.

This is an informational service, not an official warning channel. For immediate
danger, tell the user to call 112 and follow official emergency instructions.
""".strip()

PROMPT_TEMPLATE = """
QUESTION ({language}):
{question}

ROUTE: {route}

OFFICIAL CONTEXT:
{context}

Write a concise, grounded answer. Use [S#] citations that correspond exactly to
the labels in the context.
""".strip()


@dataclass(frozen=True)
class Citation:
    citation_id: str
    source_id: str
    title: str
    organization: str
    url: str
    language: str | None = None
    publication_date: str | None = None
    retrieved_at: str | None = None
    source_type: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class RAGResult:
    answer: str
    citations: tuple[Citation, ...]
    route: str
    language: str
    retrieval_backend: str
    call: Any = None
    context: str = ""
    citation_valid: bool = True


def response_text(response):
    status = getattr(response, "status", None)
    if status not in {None, "completed"}:
        raise RuntimeError(f"OpenAI response did not complete: {status}")
    for output in getattr(response, "output", ()):
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "refusal":
                raise RuntimeError("OpenAI refused to generate the answer")
    text = getattr(response, "output_text", "")
    if not text or not text.strip():
        raise RuntimeError("OpenAI returned an empty answer")
    return text.strip()


class RAGBase:
    """Preserve the course search -> prompt -> LLM flow for database retrieval."""

    def __init__(
        self,
        repository,
        llm_client,
        instructions: str = INSTRUCTIONS,
        prompt_template: str = PROMPT_TEMPLATE,
        model: str = "gpt-5.4-mini",
        retrieval_backend: str = "pgvector",
        max_context_chars: int = 12_000,
        require_citations: bool = True,
    ):
        self.repository = repository
        self.llm_client = llm_client
        self.instructions = instructions
        self.prompt_template = prompt_template
        self.model = model
        self.retrieval_backend = retrieval_backend
        self.max_context_chars = max_context_chars
        self.require_citations = require_citations

    def search(self, query: str, num_results: int = 5):
        return self.repository.search(query, limit=num_results)

    def _prepare_context(self, search_results):
        citation_by_source = {}
        citations = []
        blocks = []
        context_size = 0

        for result in search_results:
            source_key = result.get("source_id") or result.get("url")
            citation = citation_by_source.get(source_key)
            if citation is None:
                citation = Citation(
                    citation_id=f"S{len(citations) + 1}",
                    source_id=result.get("source_id", source_key),
                    title=result.get("title", "Official source"),
                    organization=result.get("organization", "Official provider"),
                    url=result.get("url", ""),
                    language=result.get("language"),
                    publication_date=result.get("publication_date"),
                    retrieved_at=result.get("retrieved_at"),
                    source_type=result.get("source_type"),
                    stale=bool(result.get("stale", False)),
                )
                citation_by_source[source_key] = citation
                citations.append(citation)

            block = "\n".join(
                [
                    f"[{citation.citation_id}] {citation.title}",
                    f"Organization: {citation.organization}",
                    f"URL: {citation.url}",
                    f"Publication date: {citation.publication_date or 'not provided'}",
                    f"Retrieved at: {citation.retrieved_at or 'not provided'}",
                    f"Freshness: {'STALE' if citation.stale else 'current/unspecified'}",
                    f"Evidence: {result.get('text', '')}",
                ]
            )
            if blocks and context_size + len(block) > self.max_context_chars:
                break
            blocks.append(block)
            context_size += len(block)

        return "\n\n".join(blocks), tuple(citations)

    def build_context(self, search_results):
        context, _ = self._prepare_context(search_results)
        return context

    def build_prompt(self, query: str, search_results, route="knowledge_base", language="en"):
        context = self.build_context(search_results)
        return self.prompt_template.format(
            question=query,
            context=context,
            route=route,
            language=language,
        )

    def _generate(self, prompt: str, route: str, language: str):
        response = self.llm_client.responses.create(
            model=self.model,
            store=False,
            input=[
                {"role": "developer", "content": self.instructions},
                {"role": "user", "content": prompt},
            ],
        )
        return response_text(response), None

    def llm(self, prompt: str):
        answer, _ = self._generate(prompt, "knowledge_base", "en")
        return answer

    def rag(
        self,
        query: str,
        num_results: int = 5,
        route: str = "knowledge_base",
        language: str = "en",
    ):
        search_results = self.search(query, num_results)
        context, citations = self._prepare_context(search_results)
        if not context:
            answer = (
                "No encuentro información oficial suficiente para responder."
                if language == "es"
                else "I could not find enough official information to answer."
            )
            return RAGResult(
                answer,
                (),
                route,
                language,
                self.retrieval_backend,
                context="",
            )

        prompt = self.prompt_template.format(
            question=query,
            context=context,
            route=route,
            language=language,
        )
        answer, call = self._generate(prompt, route, language)
        cited_ids = set(re.findall(r"\[(S\d+)\]", answer))
        known_ids = {citation.citation_id for citation in citations}
        citation_valid = (
            bool(cited_ids)
            and cited_ids <= known_ids
            and re.search(r"https?://", answer) is None
        )
        if self.require_citations and not citation_valid:
            fallback = (
                "No he podido generar una respuesta completamente respaldada por "
                "citas oficiales. Consulta las fuentes oficiales o reformula la pregunta."
                if language == "es"
                else "I could not generate a fully cited answer. Please consult the "
                "official sources or rephrase the question."
            )
            return RAGResult(
                fallback,
                (),
                route,
                language,
                self.retrieval_backend,
                call,
                context,
                False,
            )
        citations = tuple(
            citation for citation in citations if citation.citation_id in cited_ids
        )
        return RAGResult(
            answer,
            citations,
            route,
            language,
            self.retrieval_backend,
            call,
            context,
            citation_valid,
        )
