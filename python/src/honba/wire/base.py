"""Shared base types of the wire models (ADR 006).

Kept apart from ``honba.entities.wire`` so that wire-model modules such as
``honba.entities.screener`` can use them while ``wire`` registers their models
in ``MODELS`` without a circular import. ``wire`` re-exports these names.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Strict

Str = Annotated[str, Strict()]


def _canonical(enum: type[Enum]) -> BeforeValidator:
    """Accept only the canonical wire value, not aliases resolved by ``_missing_``."""
    values = {member.value for member in enum}

    def check(value: Any) -> Any:
        if isinstance(value, str) and value not in values:
            raise ValueError(f"{value!r} is not a valid {enum.__name__}")
        return value

    return BeforeValidator(check)


class _Wire(BaseModel):
    """A wire *record*: data a newer producer may extend.

    Unknown fields are ignored (dropped on parse), as the Rust serde readers do
    (ADR 0012 rule 1); an unknown ``schema_version`` is what a reader rejects.
    """

    model_config = ConfigDict(
        extra="ignore", frozen=True, allow_inf_nan=False, populate_by_name=True
    )


class _Command(_Wire):
    """A wire *command*: authored input (an order intent, a screener request).

    Unknown fields are rejected, as in Rust (``deny_unknown_fields``): a dropped
    field would silently change what the sender asked for, so a typo is an error.
    """

    model_config = ConfigDict(extra="forbid")
