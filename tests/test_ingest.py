from datetime import date
from pathlib import Path

import pytest

from app.aemet import AemetClient
from app.documents import TextExtractor, extract_html_text, fetch_source_document
from app.euskalmet import EuskalmetClient
from app.euskalmet_web import EuskalmetHomepageParser
from app.ingest import (
    get_database,
    save_euskalmet_homepage,
    save_euskalmet_forecast,
    save_euskalmet_stations,
    save_weather_snapshot,
)
from app.source_registry import Source


def test_saves_only_gipuzkoa_station_metadata(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    features = [
        {
            "geometry": {"coordinates": [-2.02109, 43.2527]},
            "properties": {
                "codigo": "C0EC",
                "nombre": "Lasarte",
                "municipio": "Lasarte-Oria",
                "provincia": "Gipuzkoa",
            },
        },
        {
            "geometry": {"coordinates": [-2.9, 43.4]},
            "properties": {
                "codigo": "B090",
                "nombre": "Puerto de Bilbao",
                "provincia": "Bizkaia",
            },
        },
    ]

    assert save_euskalmet_stations(connection, features) == 1
    assert connection.execute("SELECT station_id FROM weather_stations").fetchone()[0] == "C0EC"


def test_saves_public_euskalmet_forecast(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
        """
        <weatherForecast><forecasts><forecast forecastDate="21/07/2026">
          <description><es>Caluroso</es><eu>Beroa</eu></description>
          <cityForecastDataList><cityForecastData cityName="Donostia" cityCode="18">
            <tempMin>19</tempMin><tempMax>26</tempMax>
          </cityForecastData></cityForecastDataList>
        </forecast></forecasts></weatherForecast>
        """
    )

    assert save_euskalmet_forecast(connection, root) == 1
    row = connection.execute(
        "SELECT location_name, temperature_min, temperature_max FROM weather_forecasts"
    ).fetchone()
    assert row == ("Donostia", 19.0, 26.0)


def test_saves_authenticated_weather_separately_from_alerts(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")

    snapshot_id = save_weather_snapshot(
        connection,
        "euskalmet-location-forecast",
        {"city": "Donostia"},
        "https://api.euskadi.eus/forecast",
    )

    assert len(snapshot_id) == 64
    assert connection.execute("SELECT COUNT(*) FROM weather_api_snapshots").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM hazard_alerts").fetchone()[0] == 0


def test_aemet_client_rejects_an_empty_key_file(tmp_path: Path):
    key_path = tmp_path / "api.pem"
    key_path.touch()

    with pytest.raises(RuntimeError, match="AEMET API key file is empty"):
        AemetClient(key_path=key_path).station_inventory()


def test_aemet_client_does_not_include_a_key_in_http_errors(tmp_path: Path):
    class Response:
        ok = False
        status_code = 401

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    key_path = tmp_path / "api.pem"
    key_path.write_text("private-test-key", encoding="utf-8")

    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        AemetClient(key_path=key_path, session=Session()).station_inventory()

    assert "private-test-key" not in str(error.value)


def test_aemet_client_strips_optional_pem_wrapper(tmp_path: Path):
    key_path = tmp_path / "api.pem"
    key_path.write_text(
        "-----BEGIN PUBLIC KEY-----\napi-token\n-----END PUBLIC KEY-----\n",
        encoding="utf-8",
    )

    assert AemetClient(key_path=key_path)._read_api_key() == "api-token"


def test_extracts_explicit_homepage_warnings_only():
    parser = EuskalmetHomepageParser()
    parser.feed(
        """
        <h3>HOY, Miércoles 22</h3>
        <h4>Aviso amarillo</h4><p>Temperaturas altas extremas</p>
        """
    )
    parser.close()

    assert parser.alerts == [
        {
            "date_label": "HOY, Miércoles 22",
            "severity": "Aviso amarillo",
            "text": "HOY, Miércoles 22 Aviso amarillo Temperaturas altas extremas",
        }
    ]
def test_saves_explicit_homepage_alerts(tmp_path: Path):
    connection = get_database(tmp_path / "ingestion.sqlite")
    homepage = {
        "source_id": "euskalmet-homepage",
        "url": "https://www.euskalmet.euskadi.eus/webmet00-home/es/",
        "alerts": [{"text": "Aviso amarillo por viento"}],
    }

    assert save_euskalmet_homepage(connection, homepage) == 1
    assert connection.execute("SELECT COUNT(*) FROM hazard_alerts").fetchone()[0] == 1

    save_euskalmet_homepage(connection, homepage)

    assert connection.execute("SELECT COUNT(*) FROM hazard_alerts").fetchone()[0] == 1


def test_document_text_extractor_skips_script_content():
    parser = TextExtractor()
    parser.feed("<h1>Guia de inundaciones</h1><script>secret()</script><p>Texto oficial</p>")

    assert parser.text() == "Guia de inundaciones\nTexto oficial"


def test_html_extraction_prefers_the_main_content():
    html = "<nav>Menu global</nav><main><h1>Cambio climatico</h1><p>Texto oficial</p></main>"

    assert extract_html_text(html) == "Cambio climatico\nTexto oficial"


def test_rejects_an_oversized_allowlisted_pdf():
    class Response:
        content = b"oversized"
        url = "https://example.test/report.pdf"

        def raise_for_status(self):
            pass

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    source = Source(
        source_id="bounded-pdf",
        organization="Euskalmet",
        title="Bounded report",
        url="https://example.test/report.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        max_bytes=4,
    )

    with pytest.raises(ValueError, match="Document exceeds 4 bytes"):
        fetch_source_document(source, session=Session())


def test_euskalmet_client_uses_a_provider_generated_test_token(tmp_path: Path):
    token_path = tmp_path / "token.txt"
    token_path.write_text("test-token\n", encoding="utf-8")

    client = EuskalmetClient(token_path=token_path)

    assert client._token() == "test-token"


def test_euskalmet_signed_claims_include_both_owner_identifiers(tmp_path: Path):
    login_id_path = tmp_path / "fingerPrint.txt"
    login_id_path.write_text("fingerprint", encoding="utf-8")
    client = EuskalmetClient(
        email="owner@example.com", login_id_path=login_id_path
    )

    claims = client._claims(100)

    assert claims["email"] == "owner@example.com"
    assert claims["loginId"] == "fingerprint"
    assert claims["exp"] - claims["iat"] == 3600


def test_euskalmet_signed_claims_require_an_owner_email(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EUSKALMET_EMAIL", raising=False)
    login_id_path = tmp_path / "fingerPrint.txt"
    login_id_path.write_text("fingerprint", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Set EUSKALMET_EMAIL"):
        EuskalmetClient(login_id_path=login_id_path)._claims(100)


def test_euskalmet_location_forecast_uses_documented_hierarchy():
    client = EuskalmetClient()
    paths = []
    client.get = lambda path: paths.append(path) or {}

    client.location_forecast(
        "basque_country",
        "donostialdea",
        "donostia",
        date(2026, 7, 21),
        date(2026, 7, 22),
    )

    assert paths == [
        "/euskalmet/weather/regions/basque_country/zones/donostialdea/"
        "locations/donostia/forecast/at/2026/07/21/for/20260722"
    ]


def test_euskalmet_alerts_use_the_documented_alert_zone():
    client = EuskalmetClient()
    paths = []
    client.get = lambda path: paths.append(path) or []

    client.alert_forecast("GIPUZKOA_COAST", date(2026, 7, 21))

    assert paths == [
        "/euskalmet/alerts/zones/GIPUZKOA_COAST/forecast/at/2026/07/21"
    ]
