"""Audited Euskalmet scope for the representative Gipuzkoa weather snapshot."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class ForecastLocation:
    region: str
    zone: str
    location: str
    display_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class AlertArea:
    zone: str
    display_name: str
    aliases: tuple[str, ...]


REPRESENTATIVE_LOCATIONS = (
    ForecastLocation(
        "basque_country",
        "donostialdea",
        "donostia",
        "Donostia / San Sebastian",
        ("donostia", "san sebastian", "donostia-san sebastian"),
    ),
    ForecastLocation(
        "basque_country",
        "coast_zone",
        "irun",
        "Irun",
        ("irun",),
    ),
    ForecastLocation(
        "basque_country",
        "coast_zone",
        "hondarribia",
        "Hondarribia",
        ("hondarribia", "fuenterrabia"),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "hernani",
        "Hernani",
        ("hernani",),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "lasarte",
        "Lasarte-Oria",
        ("lasarte", "lasarte oria", "lasarte-oria"),
    ),
    ForecastLocation(
        "basque_country",
        "coast_zone",
        "zarautz",
        "Zarautz",
        ("zarautz",),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "tolosa",
        "Tolosa",
        ("tolosa",),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "eibar",
        "Eibar",
        ("eibar",),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "arrasate",
        "Arrasate / Mondragon",
        ("arrasate", "mondragon", "arrasate mondragon"),
    ),
    ForecastLocation(
        "basque_country",
        "cantabrian_valleys",
        "beasain",
        "Beasain",
        ("beasain",),
    ),
)

GIPUZKOA_ALERT_AREAS = (
    AlertArea(
        "GIPUZKOA_COAST",
        "Gipuzkoa coast",
        ("gipuzkoa coast", "coast", "coastal", "costa", "litoral", "kostaldea"),
    ),
    AlertArea(
        "GIPUZKOA_INTERIOR",
        "Gipuzkoa interior",
        ("gipuzkoa interior", "interior", "inland", "barrualdea"),
    ),
)

PUBLIC_FORECAST_ALIASES = {
    "bilbao": ("bilbao",),
    "vitoria": ("vitoria", "vitoria gasteiz", "gasteiz"),
    "pamplona": ("pamplona", "iruna"),
    "laguardia": ("laguardia",),
}


def normalize_place(value: str):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _contains_alias(normalized_question: str, alias: str):
    normalized_alias = normalize_place(alias)
    return re.search(rf"(?:^|\s){re.escape(normalized_alias)}(?:$|\s)", normalized_question) is not None


def requested_location_ids(question: str):
    normalized = normalize_place(question)
    matches = []
    for location in REPRESENTATIVE_LOCATIONS:
        if any(_contains_alias(normalized, alias) for alias in location.aliases):
            matches.append(location.location)
    for location_id, aliases in PUBLIC_FORECAST_ALIASES.items():
        if any(_contains_alias(normalized, alias) for alias in aliases):
            matches.append(location_id)
    return tuple(dict.fromkeys(matches))


def canonical_location_id(value: str):
    normalized = normalize_place(value)
    for location in REPRESENTATIVE_LOCATIONS:
        candidates = (location.location, location.display_name, *location.aliases)
        if normalized in {normalize_place(candidate) for candidate in candidates}:
            return location.location
    for location_id, aliases in PUBLIC_FORECAST_ALIASES.items():
        if normalized in {normalize_place(location_id), *(normalize_place(alias) for alias in aliases)}:
            return location_id
    return normalized


def location_display_name(location_id: str):
    for location in REPRESENTATIVE_LOCATIONS:
        if location.location == location_id:
            return location.display_name
    return location_id.replace("_", " ").title()


def requested_alert_zones(question: str):
    normalized = normalize_place(question)
    return tuple(
        area.zone
        for area in GIPUZKOA_ALERT_AREAS
        if any(_contains_alias(normalized, alias) for alias in area.aliases)
    )


def alert_display_name(zone: str):
    for area in GIPUZKOA_ALERT_AREAS:
        if area.zone == zone:
            return area.display_name
    return zone.replace("_", " ").title()
