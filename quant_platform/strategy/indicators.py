"""Whitelisted pure technical indicator library operating strictly on closed historical series (P3)."""

from __future__ import annotations

from decimal import Decimal
import math
from typing import Sequence

from quant_platform.core.decimal_utils import ONE, ZERO


def sma(values: Sequence[Decimal], window: int) -> Decimal:
    if window <= 0:
        raise ValueError("window must be > 0")
    if len(values) < window:
        raise ValueError(f"Insufficient values ({len(values)}) for SMA({window})")
    subset = values[-window:]
    return sum(subset, ZERO) / Decimal(window)


def ema(values: Sequence[Decimal], window: int) -> Decimal:
    if window <= 0 or not values:
        raise ValueError("Invalid EMA inputs")
    alpha = Decimal("2") / Decimal(window + 1)
    one_minus_alpha = ONE - alpha
    result = values[0]
    for v in values[1:]:
        result = alpha * v + one_minus_alpha * result
    return result


def rsi(values: Sequence[Decimal], window: int = 14) -> Decimal:
    if window <= 0 or len(values) < window + 1:
        raise ValueError(f"Insufficient values ({len(values)}) for RSI({window})")
    gains = ZERO
    losses = ZERO
    recent = values[-(window + 1) :]
    for idx in range(1, len(recent)):
        diff = recent[idx] - recent[idx - 1]
        if diff > ZERO:
            gains += diff
        elif diff < ZERO:
            losses += -diff
    avg_gain = gains / Decimal(window)
    avg_loss = losses / Decimal(window)
    if avg_loss == ZERO:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (ONE + rs))


def bollinger_bands(
    values: Sequence[Decimal],
    window: int = 20,
    num_std: Decimal = Decimal("2"),
) -> tuple[Decimal, Decimal, Decimal]:
    mid = sma(values, window)
    subset = values[-window:]
    variance = sum((v - mid) * (v - mid) for v in subset) / Decimal(window)
    # Exact Decimal square root!
    std = variance.sqrt()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return lower, mid, upper


def zscore(values: Sequence[Decimal], window: int = 20) -> Decimal:
    if len(values) < window or window <= 1:
        raise ValueError("Insufficient values for zscore")
    mid = sma(values, window)
    subset = values[-window:]
    variance = sum((v - mid) * (v - mid) for v in subset) / Decimal(window)
    std = variance.sqrt()
    if std == ZERO:
        return ZERO
    return (values[-1] - mid) / std
