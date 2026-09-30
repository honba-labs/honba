"""Shared validation."""


def check(period: int) -> int:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return period
