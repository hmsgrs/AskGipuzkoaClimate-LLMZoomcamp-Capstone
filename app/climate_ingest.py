"""Download bounded ERA5-Land climate data through the configured CDS API."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import cdsapi


DATASET = "reanalysis-era5-land"
# CDS expects an area as north, west, south, east. This bounds all of Gipuzkoa.
GIPUZKOA_AREA = [43.45, -2.65, 42.85, -1.65]
DEFAULT_VARIABLES = (
    "2m_temperature",
    "total_precipitation",
    "volumetric_soil_water_layer_1",
)
HOURLY_TIMES = tuple(f"{hour:02d}:00" for hour in range(24))


def build_request(year: int, month: int, days: Sequence[int], variables: Sequence[str]):
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if not days or any(not 1 <= day <= 31 for day in days):
        raise ValueError("days must contain values between 1 and 31")
    if not variables:
        raise ValueError("at least one ERA5-Land variable is required")

    return {
        "variable": list(variables),
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": [f"{day:02d}" for day in days],
        "time": list(HOURLY_TIMES),
        "area": GIPUZKOA_AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def download_era5_land(
    output_path: Path,
    year: int,
    month: int,
    days: Sequence[int],
    variables: Sequence[str] = DEFAULT_VARIABLES,
    client=None,
):
    """Download an ERA5-Land subset and save a retrieval manifest beside it."""
    request = build_request(year, month, days, variables)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if client is None:
        # cdsapi.Client reads the user's configured ~/.cdsapirc credentials.
        client = cdsapi.Client()

    client.retrieve(DATASET, request, str(output_path))

    manifest = {
        "dataset": DATASET,
        "request": request,
        "output": str(output_path),
    }
    output_path.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download ERA5-Land hourly data for the Gipuzkoa bounding box."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--days", type=int, nargs="+", required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="NetCDF output path"
    )
    parser.add_argument(
        "--variable",
        action="append",
        choices=DEFAULT_VARIABLES,
        help="Repeat to override the default variable set.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    variables = args.variable or DEFAULT_VARIABLES
    download_era5_land(args.output, args.year, args.month, args.days, variables)


if __name__ == "__main__":
    main()
