"""Structured NLStrategySpec parser translating Chinese/English strategy prompts into typed parameters (P10)."""

from __future__ import annotations

from decimal import Decimal
import re

from pydantic import BaseModel, ConfigDict

from quant_platform.core.decimal_utils import StrictDecimal


class NLStrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_prompt: str
    archetype: str = "sma_crossover"  # sma_crossover | ema_crossover | rsi_reversion
    exchange: str = "binance"
    symbol: str = "BTC/USDT:USDT"
    interval: str = "1m"
    fast_window: int = 5
    slow_window: int = 15
    rsi_window: int = 14
    rsi_oversold: StrictDecimal = Decimal("30")
    rsi_overbought: StrictDecimal = Decimal("70")
    order_qty: StrictDecimal = Decimal("0.05")


def parse_nl_prompt_to_spec(prompt: str) -> NLStrategySpec:
    text = prompt.strip()
    lower = text.lower()

    archetype = "sma_crossover"
    if "rsi" in lower:
        archetype = "rsi_reversion"
    elif "ema" in lower or "指数均线" in text:
        archetype = "ema_crossover"

    sym_match = re.search(r"([A-Z]{2,10}/[A-Z]{2,10}(?::[A-Z]{2,10})?)", text)
    symbol = sym_match.group(1) if sym_match else "BTC/USDT:USDT"

    win_matches = [int(m) for m in re.findall(r"(\d+)\s*(?:周期|日|根|bar|period|均线|sma|ema)", lower)]
    if len(win_matches) >= 2:
        fast_w, slow_w = sorted(win_matches[:2])
    else:
        fast_w, slow_w = 5, 12

    qty_match = re.search(r"(?:买入|卖出|下单|buy|sell|qty)\s*(\d+(?:\.\d+)?)", lower)
    order_qty = Decimal(qty_match.group(1)) if qty_match else Decimal("0.05")

    return NLStrategySpec(
        raw_prompt=prompt,
        archetype=archetype,
        symbol=symbol,
        fast_window=max(2, fast_w),
        slow_window=max(fast_w + 1, slow_w),
        order_qty=order_qty,
    )
