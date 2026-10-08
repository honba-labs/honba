"""The typed ApiError hierarchy built from the ErrorDetail envelope."""

from __future__ import annotations

import pytest

from honba.client import errors as er

CODES = [
    "validation_invalid_request",
    "not_found",
    "unauthorized",
    "forbidden",
    "rate_limited",
    "risk_max_notional_exceeded",
    "risk_max_position_exceeded",
    "risk_max_drawdown_exceeded",
    "risk_trading_halted",
    "risk_order_rate_exceeded",
    "risk_quantity_below_min",
    "risk_quantity_over_freeze",
    "risk_lot_multiple_violation",
    "risk_tick_size_violation",
    "risk_price_band_exceeded",
    "risk_reduce_only_violation",
    "risk_instrument_unknown",
    "risk_max_participation_exceeded",
    "order_rejected",
    "order_execution_unavailable",
    "order_not_found",
    "instrument_not_found",
    "market_data_unavailable",
    "timeout",
    "transport_error",
    "internal_error",
    "unsupported",
    "not_implemented",
]


def envelope(code: str, **extra: object) -> dict[str, object]:
    return {
        "api_version": "1.0.0",
        "schema_version": 1,
        "error": {"code": code, "message": "boom", "retryable": False, **extra},
    }


def test_every_generated_error_code_is_mapped() -> None:
    from pathlib import Path

    import honba

    stub = Path(honba.__file__).parent / "wire" / "generated" / "__init__.pyi"
    line = next(x for x in stub.read_text().splitlines() if x.startswith("ErrorCode = "))
    generated = [part.strip(' "') for part in line.split("[", 1)[1].rstrip("]").split(",")]
    assert generated == CODES
    assert set(er.CATEGORY_OF_CODE) == set(CODES)
    assert set(er.ERROR_CLASS_OF_CODE) == set(CODES)


@pytest.mark.parametrize(
    ("code", "cls", "category"),
    [
        ("validation_invalid_request", er.ValidationApiError, "validation"),
        ("not_found", er.NotFoundApiError, "not_found"),
        ("order_not_found", er.NotFoundApiError, "not_found"),
        ("instrument_not_found", er.NotFoundApiError, "not_found"),
        ("unauthorized", er.AuthApiError, "auth"),
        ("forbidden", er.AuthApiError, "auth"),
        ("rate_limited", er.RateLimitedApiError, "rate_limit"),
        ("risk_max_notional_exceeded", er.RiskApiError, "risk"),
        ("risk_max_position_exceeded", er.RiskApiError, "risk"),
        ("risk_max_drawdown_exceeded", er.RiskApiError, "risk"),
        ("risk_trading_halted", er.RiskApiError, "risk"),
        ("risk_order_rate_exceeded", er.RiskApiError, "risk"),
        ("risk_quantity_below_min", er.RiskApiError, "risk"),
        ("risk_quantity_over_freeze", er.RiskApiError, "risk"),
        ("risk_lot_multiple_violation", er.RiskApiError, "risk"),
        ("risk_tick_size_violation", er.RiskApiError, "risk"),
        ("risk_price_band_exceeded", er.RiskApiError, "risk"),
        ("risk_reduce_only_violation", er.RiskApiError, "risk"),
        ("risk_instrument_unknown", er.RiskApiError, "risk"),
        ("risk_max_participation_exceeded", er.RiskApiError, "risk"),
        ("order_rejected", er.OrderRejectedApiError, "order"),
        ("order_execution_unavailable", er.OrderRejectedApiError, "order"),
        ("market_data_unavailable", er.MarketDataUnavailableApiError, "market_data"),
        ("timeout", er.TransportApiError, "transport"),
        ("transport_error", er.TransportApiError, "transport"),
        ("internal_error", er.InternalApiError, "internal"),
        ("unsupported", er.UnsupportedApiError, "unsupported"),
        ("not_implemented", er.NotImplementedApiError, "unsupported"),
    ],
)
def test_a_code_maps_to_its_class_and_category(code: str, cls: type, category: str) -> None:
    err = er.error_from_envelope(422, envelope(code))
    assert type(err) is cls
    assert isinstance(err, er.ApiError)
    assert (err.code, err.category, err.status) == (code, category, 422)


def test_not_implemented_is_an_unsupported_error() -> None:
    err = er.error_from_envelope(501, envelope("not_implemented"))
    assert isinstance(err, er.UnsupportedApiError)


def test_the_envelope_fields_are_carried() -> None:
    err = er.error_from_envelope(
        422,
        envelope("validation_invalid_request", context={"field": "tf"}, retryable=False),
    )
    assert err.message == "boom"
    assert err.retryable is False
    assert err.context == {"field": "tf"}
    assert err.api_version == "1.0.0"
    assert "validation_invalid_request" in str(err) and "boom" in str(err)


def test_retryable_comes_from_the_envelope_not_the_class() -> None:
    err = er.error_from_envelope(503, envelope("timeout", retryable=True))
    assert err.retryable is True


def test_unknown_codes_become_a_base_api_error_keeping_the_code() -> None:
    err = er.error_from_envelope(418, envelope("teapot"))
    assert type(err) is er.ApiError
    assert (err.code, err.category) == ("teapot", "unknown")


def test_unknown_fields_in_the_error_are_ignored() -> None:
    body = envelope("not_found", future_field=[1, 2])
    assert er.error_from_envelope(404, body).code == "not_found"


@pytest.mark.parametrize(
    "body",
    [None, "text", [], {}, {"data": {}}, {"error": None}, {"error": "x"}, {"error": {"code": 1}}],
)
def test_a_non_envelope_failure_is_an_invalid_response_error(body: object) -> None:
    err = er.error_from_envelope(502, body)
    assert type(err) is er.InvalidResponseError
    assert err.status == 502
    assert err.code == "invalid_response"
    assert err.retryable is False
