"""Strict fixed-point Decimal utilities forbidding binary float contamination."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

from quant_platform.exceptions import FloatForbiddenError

ZERO = Decimal("0")
ONE = Decimal("1")
BPS_DIVISOR = Decimal("10000")


def to_strict_decimal(value: Any, field_name: str = "value") -> Decimal:
    """Convert int/str/Decimal to Decimal, strictly rejecting float and bool."""
    if isinstance(value, bool):
        raise FloatForbiddenError(field_name)
    if isinstance(value, float):
        raise FloatForbiddenError(field_name)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"Non-finite Decimal not allowed for {field_name}: {value}")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"Empty string cannot convert to Decimal for {field_name}")
        try:
            dec = Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid Decimal string '{value}' for {field_name}") from exc
        if not dec.is_finite():
            raise ValueError(f"Non-finite Decimal not allowed for {field_name}: {value}")
        return dec
    raise TypeError(f"Unsupported type {type(value).__name__} for Decimal field '{field_name}'")


def _pydantic_validate_decimal(v: Any) -> Decimal:
    return to_strict_decimal(v)


def format_decimal(d: Decimal) -> str:
    """Format Decimal as canonical non-scientific string, trimming insignificant trailing zeros."""
    normalized = d.normalize()
    s = format(normalized, "f")
    if s == "-0":
        return "0"
    return s


StrictDecimal = Annotated[
    Decimal,
    BeforeValidator(_pydantic_validate_decimal),
    PlainSerializer(lambda v: format_decimal(v), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "decimal", "examples": ["0.00010000"]}),
]


def quantize_to_step(value: Decimal, step: Decimal, rounding: str = ROUND_DOWN) -> Decimal:
    """Quantize a positive/signed Decimal to an exact step size (e.g., tickSize or stepSize)."""
    if isinstance(value, float) or isinstance(step, float):
        raise FloatForbiddenError("quantize_to_step")
    if step <= ZERO:
        raise ValueError(f"step must be positive, got {step}")
    units = (value / step).to_integral_value(rounding=rounding)
    return (units * step).quantize(step)


def quantize_price_for_side(price: Decimal, tick_size: Decimal, is_buy: bool) -> Decimal:
    """Conservative tick rounding: BUY rounds down to tick, SELL rounds up/down per exchange convention (default floor to tick)."""
    if isinstance(price, float) or isinstance(tick_size, float):
        raise FloatForbiddenError("quantize_price_for_side")
    rounding = ROUND_DOWN if is_buy else ROUND_UP
    return quantize_to_step(price, tick_size, rounding=rounding)


def is_exact_step_multiple(value: Decimal, step: Decimal) -> bool:
    """Return True iff value is an exact integer multiple of step."""
    if isinstance(value, float) or isinstance(step, float):
        raise FloatForbiddenError("is_exact_step_multiple")
    if step <= ZERO:
        return False
    remainder = value % step
    return remainder == ZERO


def round_decimal(value: Decimal, decimals: int = 8) -> Decimal:
    if isinstance(value, float):
        raise FloatForbiddenError("round_decimal")
    quantum = Decimal(10) ** (-decimals)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
