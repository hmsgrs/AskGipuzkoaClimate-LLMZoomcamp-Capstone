"""Read public Meteoadversa warning summaries from Euskalmet's official homepage."""

from html.parser import HTMLParser
import re

import requests


HOMEPAGE_URL = "https://www.euskalmet.euskadi.eus/webmet00-home/es/"
SOURCE_ID = "euskalmet-homepage"
_WARNING_LEVEL = re.compile(r"^(?:aviso|alerta|abisu)\b", re.IGNORECASE)


class EuskalmetHomepageParser(HTMLParser):
    """Extract explicit warning cards from the official homepage."""

    def __init__(self):
        super().__init__()
        self.alerts = []
        self._current_alert = None
        self._current_heading_tag = None
        self._heading_parts = []
        self._current_date = None
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in {"h3", "h4"}:
            self._finish_heading()
            self._finish_alert()
            self._current_heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if not self._ignored_depth and tag == self._current_heading_tag:
            self._finish_heading()

    def handle_data(self, data):
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._current_heading_tag:
            self._heading_parts.append(text)
        elif self._current_alert is not None:
            self._current_alert["text_parts"].append(text)

    def close(self):
        super().close()
        self._finish_heading()
        self._finish_alert()

    def _finish_heading(self):
        if not self._current_heading_tag:
            return
        text = " ".join(self._heading_parts)
        tag = self._current_heading_tag
        self._current_heading_tag = None
        self._heading_parts = []
        if tag == "h3":
            self._current_date = text
        elif tag == "h4" and _WARNING_LEVEL.match(text):
            self._current_alert = {
                "date_label": self._current_date,
                "severity": text,
                "text_parts": [],
            }

    def _finish_alert(self):
        if self._current_alert is None:
            return
        alert = self._current_alert
        self._current_alert = None
        details = " ".join(alert.pop("text_parts")).strip()
        text = " ".join(
            part for part in (alert["date_label"], alert["severity"], details) if part
        )
        if text:
            self.alerts.append({**alert, "text": text})


def fetch_euskalmet_homepage(session=None):
    response = (session or requests).get(
        HOMEPAGE_URL,
        headers={"User-Agent": "GipuzkoaClimateAskbot/0.1"},
        timeout=60,
    )
    response.raise_for_status()
    parser = EuskalmetHomepageParser()
    parser.feed(response.text)
    parser.close()
    return {"source_id": SOURCE_ID, "url": response.url, "alerts": parser.alerts}
