"""Build one bounded all-source snapshot with explicit user activation."""

import argparse
import json
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.aemet import AemetClient
from app.climate_ingest import download_monthly_era5_land, monthly_output_path
from app.euskalmet import EuskalmetClient
from app.euskalmet_scope import GIPUZKOA_ALERT_AREAS, REPRESENTATIVE_LOCATIONS
from app.euskalmet_web import fetch_euskalmet_homepage
from app.ingest import (
    get_database,
    load_euskalmet_forecast,
    load_euskalmet_stations,
    refresh_euskalmet_alert_scope,
    refresh_euskalmet_forecast_scope,
    save_aemet_daily_observations,
    save_aemet_stations,
    save_euskalmet_forecast,
    save_euskalmet_homepage,
    save_euskalmet_stations,
)
from app.knowledge_base import ingest_corpus, load_evaluation_questions
from app.snapshot import create_snapshot


@dataclass(frozen=True)
class SnapshotRefreshConfig:
    snapshot_id: str
    output_root: Path
    as_of: str
    aemet_station: str
    aemet_start: str
    aemet_end: str
    forecast_horizon_days: int
    era5_year: int
    era5_month: int
    questions: Path

    def validate(self):
        as_of = date.fromisoformat(self.as_of)
        start = date.fromisoformat(self.aemet_start)
        end = date.fromisoformat(self.aemet_end)
        if start > end:
            raise ValueError("AEMET start date must not be after end date")
        if self.forecast_horizon_days <= 0:
            raise ValueError("Forecast horizon must be positive")
        if not 1 <= self.era5_month <= 12:
            raise ValueError("ERA5 month must be between 1 and 12")
        if self.era5_year > as_of.year or self.era5_year < 1950:
            raise ValueError("ERA5 year must be a published historical year")
        if not self.questions.is_file():
            raise ValueError(f"Evaluation fixture does not exist: {self.questions}")


def capture_all_sources(
    connection,
    climate_root: Path,
    config: SnapshotRefreshConfig,
    *,
    session=None,
    aemet_client=None,
    euskalmet_client=None,
    climate_client=None,
):
    """Populate a clean working database and return artifacts plus a safe receipt."""
    config.validate()
    aemet_client = aemet_client or AemetClient()
    euskalmet_client = euskalmet_client or EuskalmetClient()
    results = {}

    results["euskalmet_stations"] = save_euskalmet_stations(
        connection, load_euskalmet_stations(session=session)
    )
    results["euskalmet_forecasts"] = save_euskalmet_forecast(
        connection, load_euskalmet_forecast(session=session)
    )
    results["homepage_alerts"] = save_euskalmet_homepage(
        connection, fetch_euskalmet_homepage(session=session)
    )
    results["knowledge_documents"] = len(ingest_corpus(connection, session=session))
    results["evaluation_questions"] = load_evaluation_questions(
        connection, config.questions
    )

    results["aemet_stations"] = save_aemet_stations(
        connection, aemet_client.station_inventory()
    )
    observations = aemet_client.daily_observations(
        config.aemet_station, config.aemet_start, config.aemet_end
    )
    results["aemet_observations"] = save_aemet_daily_observations(
        connection, observations
    )
    if results["aemet_observations"] == 0:
        raise ValueError("AEMET returned no observations for the bounded range")

    results["authenticated_forecasts"] = refresh_euskalmet_forecast_scope(
        connection,
        tuple(
            (location.region, location.zone, location.location)
            for location in REPRESENTATIVE_LOCATIONS
        ),
        horizon_days=config.forecast_horizon_days,
        as_of=config.as_of,
        client=euskalmet_client,
    )
    results["authenticated_alerts"] = refresh_euskalmet_alert_scope(
        connection,
        tuple(area.zone for area in GIPUZKOA_ALERT_AREAS),
        as_of=config.as_of,
        client=euskalmet_client,
    )

    climate = download_monthly_era5_land(
        climate_root,
        config.era5_year,
        config.era5_month,
        client=climate_client,
    )
    climate_path = monthly_output_path(
        climate_root, config.era5_year, config.era5_month
    )
    results["era5_land"] = {
        "status": climate["status"],
        "year": config.era5_year,
        "month": config.era5_month,
    }
    return [climate_path, climate_path.with_suffix(".json")], results


def produce_all_source_snapshot(
    config: SnapshotRefreshConfig,
    *,
    session=None,
    aemet_client=None,
    euskalmet_client=None,
    climate_client=None,
    capture_function=capture_all_sources,
):
    """Capture all sources in staging and atomically publish only a complete snapshot."""
    config.validate()
    config.output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{config.snapshot_id}.build-", dir=config.output_root
    ) as temporary:
        build_root = Path(temporary)
        database = build_root / "working.sqlite"
        connection = get_database(database)
        try:
            artifacts, source_results = capture_function(
                connection,
                build_root / "climate",
                config,
                session=session,
                aemet_client=aemet_client,
                euskalmet_client=euskalmet_client,
                climate_client=climate_client,
            )
        finally:
            connection.close()
        snapshot = create_snapshot(
            database,
            config.output_root,
            snapshot_id=config.snapshot_id,
            artifacts=artifacts,
            notes=(
                "Explicit bounded all-source acquisition. Weather and warning data "
                "are historical after publication."
            ),
            required_tables=(
                "sources",
                "documents",
                "chunks",
                "weather_stations",
                "weather_forecasts",
                "weather_api_snapshots",
                "aemet_daily_observations",
                "hazard_alerts",
            ),
            require_nonempty=(
                "sources",
                "documents",
                "chunks",
                "weather_stations",
                "weather_forecasts",
                "weather_api_snapshots",
                "aemet_daily_observations",
                "hazard_alerts",
            ),
        )
    return {**snapshot, "sources": source_results}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a fresh bounded all-source snapshot."
    )
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--as-of", required=True, help="Acquisition date, YYYY-MM-DD")
    parser.add_argument("--aemet-station", default="1012P")
    parser.add_argument("--aemet-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--aemet-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--forecast-horizon-days", type=int, default=3)
    parser.add_argument("--era5-year", required=True, type=int)
    parser.add_argument("--era5-month", required=True, type=int)
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("evaluation/retrieval_questions.json"),
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = SnapshotRefreshConfig(
        snapshot_id=args.snapshot_id,
        output_root=args.output_root,
        as_of=args.as_of,
        aemet_station=args.aemet_station,
        aemet_start=args.aemet_start,
        aemet_end=args.aemet_end,
        forecast_horizon_days=args.forecast_horizon_days,
        era5_year=args.era5_year,
        era5_month=args.era5_month,
        questions=args.questions,
    )
    print(
        json.dumps(
            produce_all_source_snapshot(config),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
