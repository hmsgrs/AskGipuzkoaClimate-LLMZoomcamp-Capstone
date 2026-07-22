from app.climate_ingest import (
    DEFAULT_VARIABLES,
    GIPUZKOA_AREA,
    HOURLY_TIMES,
    build_request,
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
