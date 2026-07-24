from datetime import date
from pathlib import Path

import pytest

from app.climate_ingest import (
    DEFAULT_VARIABLES,
    GIPUZKOA_AREA,
    HOURLY_TIMES,
    build_request,
    download_monthly_era5_land,
    monthly_output_path,
    previous_month,
)


def test_build_request_uses_the_gipuzkoa_bounding_box():
    request = build_request(2024, 1, [1], DEFAULT_VARIABLES)

    assert request["area"] == GIPUZKOA_AREA
    assert request["day"] == ["01"]
    assert request["time"] == list(HOURLY_TIMES)
    assert request["data_format"] == "netcdf"


def test_build_request_rejects_invalid_days():
    try:
        build_request(2024, 1, [0], DEFAULT_VARIABLES)
    except ValueError as error:
        assert str(error) == "days must contain values between 1 and 31"
    else:
        raise AssertionError("expected an invalid day to fail")


def test_monthly_helpers_handle_leap_years_and_year_boundaries(tmp_path):
    assert previous_month(date(2026, 1, 10)) == (2025, 12)
    assert monthly_output_path(tmp_path, 2024, 2) == (
        tmp_path / "2024" / "02" / "era5-land-2024-02.nc"
    )
    assert build_request(2024, 2, [29], DEFAULT_VARIABLES)["day"] == ["29"]


def test_monthly_download_is_atomic_and_idempotent(tmp_path):
    class Client:
        calls = 0

        def retrieve(self, dataset, request, output):
            self.calls += 1
            from pathlib import Path

            Path(output).write_bytes(b"netcdf")

    client = Client()
    first = download_monthly_era5_land(tmp_path, 2024, 2, client=client)
    second = download_monthly_era5_land(tmp_path, 2024, 2, client=client)

    assert first["status"] == "downloaded"
    assert second["status"] == "skipped"
    assert client.calls == 1
    assert monthly_output_path(tmp_path, 2024, 2).read_bytes() == b"netcdf"
    assert monthly_output_path(tmp_path, 2024, 2).with_suffix(".json").exists()


def test_monthly_download_replaces_a_different_request(tmp_path):
    class Client:
        calls = 0

        def retrieve(self, dataset, request, output):
            self.calls += 1
            Path(output).write_bytes(f"netcdf-{self.calls}".encode())

    client = Client()
    download_monthly_era5_land(tmp_path, 2024, 2, client=client)
    result = download_monthly_era5_land(
        tmp_path, 2024, 2, variables=("2m_temperature",), client=client
    )

    assert result["status"] == "downloaded"
    assert client.calls == 2


def test_monthly_publication_failure_restores_previous_pair(tmp_path, monkeypatch):
    class Client:
        calls = 0

        def retrieve(self, dataset, request, output):
            self.calls += 1
            Path(output).write_bytes(f"netcdf-{self.calls}".encode())

    client = Client()
    download_monthly_era5_land(tmp_path, 2024, 2, client=client)
    output = monthly_output_path(tmp_path, 2024, 2)
    manifest = output.with_suffix(".json")
    original_data = output.read_bytes()
    original_manifest = manifest.read_text(encoding="utf-8")
    original_replace = Path.replace

    def fail_manifest_publication(path, target):
        if path.name.endswith(".json.part"):
            raise OSError("injected manifest publication failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_publication)
    with pytest.raises(OSError, match="injected manifest"):
        download_monthly_era5_land(tmp_path, 2024, 2, force=True, client=client)

    assert output.read_bytes() == original_data
    assert manifest.read_text(encoding="utf-8") == original_manifest


def test_monthly_download_recovers_an_interrupted_backup_pair(tmp_path):
    class Client:
        calls = 0

        def retrieve(self, dataset, request, output):
            self.calls += 1
            Path(output).write_bytes(b"netcdf")

    client = Client()
    download_monthly_era5_land(tmp_path, 2024, 2, client=client)
    output = monthly_output_path(tmp_path, 2024, 2)
    manifest = output.with_suffix(".json")
    output.replace(output.with_suffix(".nc.bak"))
    manifest.replace(manifest.with_suffix(".json.bak"))

    result = download_monthly_era5_land(tmp_path, 2024, 2, client=client)

    assert result["status"] == "skipped"
    assert client.calls == 1
    assert output.exists()
    assert manifest.exists()
