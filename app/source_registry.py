"""Reproducible registry for the initial official RAG corpus."""

from dataclasses import dataclass


MAX_PDF_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Source:
    source_id: str
    organization: str
    title: str
    url: str
    language: str
    content_type: str
    source_type: str
    publication_date: str | None = None
    max_bytes: int | None = None


SOURCES = (
    Source(
        source_id="euskalmet-climate-bulletins",
        organization="Euskalmet",
        title="Informes climatologicos",
        url="https://www.euskalmet.euskadi.eus/clima/boletines-climatologicos/",
        language="es",
        content_type="text/html",
        source_type="climate_history",
    ),
    Source(
        source_id="euskalmet-recommendations",
        organization="Euskalmet",
        title="Recomendaciones ante meteorologia adversa",
        url="https://www.euskalmet.euskadi.eus/servicios/recomendaciones/",
        language="es",
        content_type="text/html",
        source_type="climate_risk_guidance",
    ),
    Source(
        source_id="basque-government-climate",
        organization="Gobierno Vasco",
        title="Cambio climatico",
        url="https://www.euskadi.eus/informacion/cambio-climatico/web01-a2ingkli/es/",
        language="es",
        content_type="text/html",
        source_type="climate_risk_guidance",
        publication_date="2018-04-30",
    ),
    Source(
        source_id="euskalmet-season-winter-2021",
        organization="Euskalmet",
        title="Climatologia del invierno 2020-2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_season_2021/es_def/adjuntos/invierno_2020-21.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-03-31",
        max_bytes=MAX_PDF_BYTES,
    ),
    Source(
        source_id="euskalmet-season-spring-2021",
        organization="Euskalmet",
        title="Climatologia de la primavera de 2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_season_2021/es_def/adjuntos/primavera_2021.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-06-30",
        max_bytes=MAX_PDF_BYTES,
    ),
    Source(
        source_id="euskalmet-season-summer-2021",
        organization="Euskalmet",
        title="Climatologia del verano de 2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_season_2021/es_def/adjuntos/verano_2021.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-09-30",
        max_bytes=MAX_PDF_BYTES,
    ),
    Source(
        source_id="euskalmet-season-autumn-2021",
        organization="Euskalmet",
        title="Climatologia del otono de 2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_season_2021/es_def/adjuntos/otono_2021.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-12-31",
        max_bytes=MAX_PDF_BYTES,
    ),
    Source(
        source_id="euskalmet-month-january-2021",
        organization="Euskalmet",
        title="Climatologia de enero de 2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_clima_2021/es_def/adjuntos/01_2021.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-01-31",
        max_bytes=MAX_PDF_BYTES,
    ),
    Source(
        source_id="euskalmet-month-july-2021",
        organization="Euskalmet",
        title="Climatologia de julio de 2021",
        url="https://www.euskalmet.euskadi.eus/contenidos/informacion/meteo_report_clima_2021/es_def/adjuntos/07_2021.pdf",
        language="es",
        content_type="application/pdf",
        source_type="climate_history",
        publication_date="2021-07-31",
        max_bytes=MAX_PDF_BYTES,
    ),
)

SOURCES_BY_ID = {source.source_id: source for source in SOURCES}
