"""Download bounded ERA5-Land climate data through the configured CDS API."""

import argparse
import calendar
import hashlib
import json
from collections.abc import Sequence
from datetime import date
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
    last_day = calendar.monthrange(year, month)[1]
    if any(day > last_day for day in days):
        raise ValueError("days must be valid for the requested month")
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


def previous_month(as_of: date | None = None):
    as_of = as_of or date.today()
    previous = as_of.replace(day=1)
    if previous.month == 1:
        return previous.year - 1, 12
    return previous.year, previous.month - 1


def monthly_output_path(output_root: Path, year: int, month: int):
    return output_root / f"{year:04d}" / f"{month:02d}" / f"era5-land-{year:04d}-{month:02d}.nc"


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def published_pair_valid(output: Path, manifest_path: Path, request: dict | None = None):
    if not output.exists() or output.stat().st_size == 0 or not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        manifest.get("dataset") == DATASET
        and (request is None or manifest.get("request") == request)
        and manifest.get("sha256") == file_sha256(output)
    )


def recover_monthly_publication(output: Path, manifest_path: Path):
    temporary_output = output.with_suffix(".nc.part")
    temporary_manifest = manifest_path.with_suffix(".json.part")
    backup_output = output.with_suffix(".nc.bak")
    backup_manifest = manifest_path.with_suffix(".json.bak")

    if published_pair_valid(output, manifest_path):
        backup_output.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)
    elif published_pair_valid(output, temporary_manifest):
        manifest_path.unlink(missing_ok=True)
        temporary_manifest.replace(manifest_path)
        backup_output.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)
    elif published_pair_valid(backup_output, backup_manifest):
        output.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        backup_output.replace(output)
        backup_manifest.replace(manifest_path)
    elif published_pair_valid(backup_output, manifest_path):
        output.unlink(missing_ok=True)
        backup_output.replace(output)
        backup_manifest.unlink(missing_ok=True)
    elif published_pair_valid(output, backup_manifest):
        manifest_path.unlink(missing_ok=True)
        backup_manifest.replace(manifest_path)
        backup_output.unlink(missing_ok=True)

    temporary_output.unlink(missing_ok=True)
    temporary_manifest.unlink(missing_ok=True)


def download_monthly_era5_land(
    output_root: Path,
    year: int,
    month: int,
    variables: Sequence[str] = DEFAULT_VARIABLES,
    force: bool = False,
    client=None,
):
    output = monthly_output_path(output_root, year, month)
    manifest_path = output.with_suffix(".json")
    request = build_request(
        year,
        month,
        range(1, calendar.monthrange(year, month)[1] + 1),
        variables,
    )
    recover_monthly_publication(output, manifest_path)
    if not force and published_pair_valid(output, manifest_path, request):
        return {"status": "skipped", "output": str(output)}

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(".nc.part")
    temporary_manifest = manifest_path.with_suffix(".json.part")
    backup_output = output.with_suffix(".nc.bak")
    backup_manifest = manifest_path.with_suffix(".json.bak")
    temporary_output.unlink(missing_ok=True)
    temporary_manifest.unlink(missing_ok=True)
    client = client or cdsapi.Client()
    publication_started = False
    had_output = output.exists()
    had_manifest = manifest_path.exists()
    try:
        client.retrieve(DATASET, request, str(temporary_output))
        if not temporary_output.exists() or temporary_output.stat().st_size == 0:
            raise RuntimeError("ERA5-Land download did not produce a non-empty file")
        temporary_manifest.write_text(
            json.dumps(
                {
                    "dataset": DATASET,
                    "request": request,
                    "output": str(output),
                    "sha256": file_sha256(temporary_output),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        publication_started = True
        backup_output.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)
        if output.exists():
            output.replace(backup_output)
        if manifest_path.exists():
            manifest_path.replace(backup_manifest)
        temporary_output.replace(output)
        temporary_manifest.replace(manifest_path)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        if publication_started:
            if backup_output.exists():
                output.unlink(missing_ok=True)
                backup_output.replace(output)
            elif not had_output:
                output.unlink(missing_ok=True)
            if backup_manifest.exists():
                manifest_path.unlink(missing_ok=True)
                backup_manifest.replace(manifest_path)
            elif not had_manifest:
                manifest_path.unlink(missing_ok=True)
        raise
    backup_output.unlink(missing_ok=True)
    backup_manifest.unlink(missing_ok=True)
    return {"status": "downloaded", "output": str(output)}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download ERA5-Land hourly data for the Gipuzkoa bounding box."
    )
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--days", type=int, nargs="+")
    parser.add_argument(
        "--output", type=Path, help="NetCDF output path"
    )
    parser.add_argument("--monthly", action="store_true")
    parser.add_argument("--as-of", help="Date used to select the previous month, YYYY-MM-DD")
    parser.add_argument("--output-root", type=Path, default=Path("data/raw/era5-land"))
    parser.add_argument("--force", action="store_true")
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
    if args.monthly:
        if args.year is None or args.month is None:
            as_of = date.fromisoformat(args.as_of) if args.as_of else None
            year, month = previous_month(as_of)
        else:
            year, month = args.year, args.month
        print(
            json.dumps(
                download_monthly_era5_land(
                    args.output_root, year, month, variables, args.force
                ),
                indent=2,
            )
        )
        return
    if args.year is None or args.month is None or not args.days or args.output is None:
        raise SystemExit("--year, --month, --days, and --output are required")
    download_era5_land(args.output, args.year, args.month, args.days, variables)


if __name__ == "__main__":
    main()
