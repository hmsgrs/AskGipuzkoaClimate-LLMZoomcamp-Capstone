"""Small client for AEMET OpenData's two-step download API."""

import os
from datetime import date
from pathlib import Path

import requests

from app.http_client import retry_session


BASE_URL = "https://opendata.aemet.es/opendata/api"
DEFAULT_KEY_PATH = Path("docs/api_keys/AEMET Apikey/api.pem")


class AemetClient:
    def __init__(self, key_path: Path | None = None, session=None):
        self.key_path = key_path or Path(
            os.getenv("AEMET_API_KEY_PATH", DEFAULT_KEY_PATH)
        )
        self.session = session or retry_session()

    def _get_data(self, path: str):
        key = self._read_api_key()
        if not key:
            raise RuntimeError(f"AEMET API key file is empty: {self.key_path}")
        try:
            response = self.session.get(
                f"{BASE_URL}{path}", params={"api_key": key}, timeout=30
            )
        except requests.RequestException:
            raise RuntimeError("AEMET request failed due to a transport error") from None
        if not response.ok:
            # The request URL contains the API key, so never re-raise its HTTP error.
            raise RuntimeError(f"AEMET request failed with HTTP {response.status_code}")
        metadata = response.json()

        if metadata.get("estado") != 200 or not metadata.get("datos"):
            raise RuntimeError(metadata.get("descripcion", "AEMET did not return data"))

        payload_response = self.session.get(metadata["datos"], timeout=60)
        if not payload_response.ok:
            raise RuntimeError(
                f"AEMET data download failed with HTTP {payload_response.status_code}"
            )
        return payload_response.json()

    def _read_api_key(self):
        """Load AEMET's JWT key, accepting an optional PEM-style wrapper."""
        lines = self.key_path.read_text(encoding="utf-8").splitlines()
        key = "".join(line.strip() for line in lines if not line.startswith("-----"))
        if not key:
            raise RuntimeError(f"AEMET API key file is empty: {self.key_path}")
        return key

    def station_inventory(self):
        return self._get_data("/valores/climatologicos/inventarioestaciones/todasestaciones")

    def daily_observations(self, station_id: str, start_date: str, end_date: str):
        start_value = date.fromisoformat(start_date)
        end_value = date.fromisoformat(end_date)
        if start_value > end_value:
            raise ValueError("AEMET start date must not be after end date")
        start = f"{start_date}T00:00:00UTC"
        end = f"{end_date}T23:59:59UTC"
        return self._get_data(
            "/valores/climatologicos/diarios/datos/"
            f"fechaini/{start}/fechafin/{end}/estacion/{station_id}"
        )
