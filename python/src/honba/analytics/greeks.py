"""Black-Scholes analytical pricing and option Greeks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from honba.domain.option import OptionKind

__all__ = ["Greeks", "bs_greeks", "bs_price", "implied_volatility"]

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class Greeks:
    """Option Greeks and theoretical price."""

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def __getitem__(self, key: str) -> float:
        return getattr(self, key)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def bs_price(
    kind: OptionKind,
    spot: float,
    strike: float,
    tau: float,
    iv: float,
    r: float = 0.07,
) -> float:
    """Black-Scholes theoretical price for European option."""
    if tau <= 0.0 or iv <= 0.0:
        if kind == OptionKind.CALL:
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)

    sigma_sqrt_tau = iv * math.sqrt(tau)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * tau) / sigma_sqrt_tau
    d2 = d1 - sigma_sqrt_tau

    discount = math.exp(-r * tau)
    if kind == OptionKind.CALL:
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_greeks(
    kind: OptionKind,
    spot: float,
    strike: float,
    tau: float,
    iv: float,
    r: float = 0.07,
) -> Greeks:
    """Compute analytical Black-Scholes Greeks."""
    price = bs_price(kind, spot, strike, tau, iv, r)
    if tau <= 0.0 or iv <= 0.0:
        delta = 1.0 if (kind == OptionKind.CALL and spot >= strike) else 0.0
        if kind == OptionKind.PUT:
            delta = -1.0 if spot <= strike else 0.0
        return Greeks(
            price=price,
            delta=delta,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            rho=0.0,
        )

    sigma_sqrt_tau = iv * math.sqrt(tau)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * tau) / sigma_sqrt_tau
    d2 = d1 - sigma_sqrt_tau

    pdf_d1 = _norm_pdf(d1)
    discount = math.exp(-r * tau)

    if kind == OptionKind.CALL:
        delta = _norm_cdf(d1)
        theta = -(spot * pdf_d1 * iv) / (2.0 * math.sqrt(tau)) - r * strike * discount * _norm_cdf(d2)
        rho = strike * tau * discount * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = -(spot * pdf_d1 * iv) / (2.0 * math.sqrt(tau)) + r * strike * discount * _norm_cdf(-d2)
        rho = -strike * tau * discount * _norm_cdf(-d2)

    gamma = pdf_d1 / (spot * sigma_sqrt_tau)
    vega = spot * pdf_d1 * math.sqrt(tau)

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta / 365.0,  # daily theta convention
        vega=vega / 100.0,    # per 1% vol convention
        rho=rho / 100.0,
    )


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    tau: float,
    r: float = 0.07,
    kind: OptionKind = OptionKind.CALL,
    tol: float = 1e-5,
    max_iter: int = 100,
) -> float | None:
    """Solve for implied volatility via Newton-Raphson with bisection fallback."""
    if tau <= 0.0 or price <= 0.0:
        return None

    # Newton-Raphson
    iv = 0.2
    for _ in range(max_iter):
        p = bs_price(kind, spot, strike, tau, iv, r)
        diff = p - price
        if abs(diff) < tol:
            return iv
        greeks = bs_greeks(kind, spot, strike, tau, iv, r)
        vega = greeks.vega * 100.0
        if abs(vega) < 1e-12:
            break
        iv -= diff / vega
        if iv <= 0.001 or iv > 5.0:
            break

    # Bisection fallback
    low, high = 0.001, 5.0
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        p = bs_price(kind, spot, strike, tau, mid, r)
        if abs(p - price) < tol or (high - low) < tol:
            return mid
        if p > price:
            high = mid
        else:
            low = mid

    return None
