"""JWT authentication and API access for Euskalmet Meteo services."""

import os
import time
from datetime import date
from pathlib import Path

import jwt
import requests


BASE_URL = "https://api.euskadi.eus"
DEFAULT_KEY_DIRECTORY = Path("docs/api_keys/Euskalmet Apikey")
AUDIENCE = "met01.apikey"
API_KEY_VERSION = "1.0.0"
ISSUER = "gipuzkoa-weather-climate-askbot"
ALERT_ZONES = (
    "SEA",
    "BIZKAIA_COAST",
    "GIPUZKOA_COAST",
    "BIZKAIA_INTERIOR",
    "GIPUZKOA_INTERIOR",
    "TRANSITION",
    "CORE",
)


class EuskalmetClient:
    def __init__(
        self,
        private_key_path: Path | None = None,
        login_id_path: Path | None = None,
        token_path: Path | None = None,
        issuer: str | None = None,
        email: str | None = None,
        session=None,
    ):
        self.private_key_path = private_key_path or Path(
            os.getenv(
                "EUSKALMET_PRIVATE_KEY_PATH",
                DEFAULT_KEY_DIRECTORY / "privateKey.pem",
            )
        )
        self.login_id_path = login_id_path or Path(
            os.getenv(
                "EUSKALMET_LOGIN_ID_PATH",
                DEFAULT_KEY_DIRECTORY / "fingerPrint.txt",
            )
        )
        configured_token_path = os.getenv("EUSKALMET_TOKEN_PATH")
        self.token_path = token_path or (
            Path(configured_token_path) if configured_token_path else None
        )
        self.issuer = issuer or os.getenv("EUSKALMET_ISSUER", ISSUER)
        self.email = email or os.getenv("EUSKALMET_EMAIL")
        self.session = session or requests.Session()

    def _claims(self, now: int):
        if not self.email:
            raise RuntimeError("Set EUSKALMET_EMAIL before signing an Euskalmet JWT")
        return {
            "aud": AUDIENCE,
            "iss": self.issuer,
            "iat": now,
            # The provider's test-token guidance uses a one-hour expiration.
            "exp": now + 3600,
            "version": API_KEY_VERSION,
            "email": self.email,
            "loginId": self.login_id_path.read_text(encoding="utf-8").strip(),
        }

    def _token(self):
        if self.token_path is not None and self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token

        now = int(time.time())
        private_key = self.private_key_path.read_text(encoding="utf-8")
        return jwt.encode(
            self._claims(now),
            private_key,
            algorithm="RS256",
            headers={"typ": "JWT"},
        )

    def get(self, path: str, params: dict | None = None):
        response = self.session.get(
            f"{BASE_URL}{path}",
            params=params,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token()}",
            },
            timeout=30,
        )
        if response.status_code == 403:
            raise RuntimeError(
                "Euskalmet rejected the JWT. Verify that the API key is active and "
                "authorized for the met01 initiative."
            )
        response.raise_for_status()
        return response.json()

    def regions(self):
        return self.get("/euskalmet/geo/regions")

    def zones(self, region_id: str):
        return self.get(f"/euskalmet/geo/regions/{region_id}/zones")

    def locations(self, region_id: str, zone_id: str):
        return self.get(
            f"/euskalmet/geo/regions/{region_id}/zones/{zone_id}/locations"
        )

    def location_forecast(
        self,
        region_id: str,
        zone_id: str,
        location_id: str,
        issued_date: date,
        target_date: date,
    ):
        return self.get(
            f"/euskalmet/weather/regions/{region_id}/zones/{zone_id}/"
            f"locations/{location_id}/forecast/at/{issued_date:%Y/%m/%d}/"
            f"for/{target_date:%Y%m%d}"
        )

    def current_station(self, station_id: str):
        return self.get(f"/euskalmet/stations/{station_id}/current")

    def readings(
        self,
        station_id: str,
        sensor_id: str,
        measure_type_id: str,
        measure_id: str,
        observed_at: date,
        hour: int,
    ):
        if not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        return self.get(
            f"/euskalmet/readings/forStation/{station_id}/{sensor_id}/"
            f"measures/{measure_type_id}/{measure_id}/"
            f"at/{observed_at:%Y/%m/%d}/{hour:02d}"
        )

    def alert_forecast(
        self, zone_id: str, issued_date: date, target_date: date | None = None
    ):
        if zone_id not in ALERT_ZONES:
            raise ValueError(f"Unknown Euskalmet alert zone: {zone_id}")
        path = (
            f"/euskalmet/alerts/zones/{zone_id}/forecast/"
            f"at/{issued_date:%Y/%m/%d}"
        )
        if target_date is not None:
            path += f"/for/{target_date:%Y%m%d}"
        return self.get(path)
