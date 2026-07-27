from app.euskalmet_scope import (
    GIPUZKOA_ALERT_AREAS,
    REPRESENTATIVE_LOCATIONS,
    canonical_location_id,
    requested_alert_zones,
    requested_location_ids,
)


def test_representative_scope_has_the_audited_catalogue_mappings():
    mappings = {
        location.location: (location.region, location.zone)
        for location in REPRESENTATIVE_LOCATIONS
    }

    assert mappings == {
        "donostia": ("basque_country", "donostialdea"),
        "irun": ("basque_country", "coast_zone"),
        "hondarribia": ("basque_country", "coast_zone"),
        "hernani": ("basque_country", "cantabrian_valleys"),
        "lasarte": ("basque_country", "cantabrian_valleys"),
        "zarautz": ("basque_country", "coast_zone"),
        "tolosa": ("basque_country", "cantabrian_valleys"),
        "eibar": ("basque_country", "cantabrian_valleys"),
        "arrasate": ("basque_country", "cantabrian_valleys"),
        "beasain": ("basque_country", "cantabrian_valleys"),
    }
    assert tuple(area.zone for area in GIPUZKOA_ALERT_AREAS) == (
        "GIPUZKOA_COAST",
        "GIPUZKOA_INTERIOR",
    )


def test_bilingual_location_and_warning_aliases_are_normalized():
    assert requested_location_ids("Prevision para Donostia-San Sebastian") == (
        "donostia",
    )
    assert requested_location_ids("Tiempo en Lasarte Oria e Irun") == (
        "irun",
        "lasarte",
    )
    assert requested_location_ids("Forecast for Mondragon") == ("arrasate",)
    assert canonical_location_id("Arrasate / Mondragon") == "arrasate"
    assert requested_alert_zones("Avisos en la costa") == ("GIPUZKOA_COAST",)
    assert requested_alert_zones("Warnings for inland Gipuzkoa") == (
        "GIPUZKOA_INTERIOR",
    )
