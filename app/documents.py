"""Fetch and normalize allowlisted official HTML and PDF documents."""

from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
from io import BytesIO

from pypdf import PdfReader
import requests

from app.source_registry import SOURCES_BY_ID, Source


OFFICIAL_DOCUMENTS = {
    source_id: source.url for source_id, source in SOURCES_BY_ID.items()
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

    def text(self):
        return "\n".join(self.parts)


class MainTextExtractor(TextExtractor):
    def __init__(self):
        super().__init__()
        self.main_depth = 0

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == "main":
            self.main_depth += 1

    def handle_endtag(self, tag):
        if tag == "main" and self.main_depth:
            self.main_depth -= 1
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self.main_depth:
            super().handle_data(data)


def normalize_text(text: str):
    return "\n".join(line for line in (" ".join(line.split()) for line in text.splitlines()) if line)


def extract_html_text(html: str):
    parser = MainTextExtractor()
    parser.feed(html)
    text = parser.text()
    if not text:
        fallback = TextExtractor()
        fallback.feed(html)
        text = fallback.text()
    return normalize_text(text)


def extract_pdf_text(content: bytes):
    reader = PdfReader(BytesIO(content))
    return normalize_text("\n".join(page.extract_text() or "" for page in reader.pages))


def fetch_source_document(source: Source, session=None):
    response = (session or requests).get(
        source.url,
        headers={"User-Agent": "GipuzkoaClimateAskbot/0.1"},
        timeout=60,
    )
    response.raise_for_status()
    if source.content_type == "application/pdf":
        content = response.content
        if source.max_bytes is not None and len(content) > source.max_bytes:
            raise ValueError(
                f"Document exceeds {source.max_bytes} bytes: {source.source_id}"
            )
        text = extract_pdf_text(content)
    else:
        text = extract_html_text(response.text)
    if not text:
        raise ValueError(f"No text extracted from source: {source.source_id}")
    return {
        "source_id": source.source_id,
        "url": response.url,
        "title": source.title,
        "text": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def fetch_official_document(source_id: str, session=None):
    if source_id not in SOURCES_BY_ID:
        raise ValueError(f"Source is not allowlisted: {source_id}")
    return fetch_source_document(SOURCES_BY_ID[source_id], session=session)
