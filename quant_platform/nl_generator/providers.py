"""Pluggable LLM Strategy Code Generators: offline deterministic synthesizer + OpenAI-compatible client (P10)."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re

import httpx

from quant_platform.nl_generator.spec_parser import NLStrategySpec


class LLMCodeGenerator(ABC):
    """Abstract code synthesizer turning structured NLStrategySpec (+ optional repair error) into Strategy code."""

    @abstractmethod
    def synthesize_code(
        self,
        spec: NLStrategySpec,
        *,
        previous_error: str | None = None,
    ) -> str:
        raise NotImplementedError


class DeterministicRuleLLMProvider(LLMCodeGenerator):
    """
    Self-contained offline code synthesizer producing clean, sandbox-compliant Strategy + Pydantic ParamModel code
    with strict Decimal arithmetic (no external paid API key required).
    """

    def synthesize_code(
        self,
        spec: NLStrategySpec,
        *,
        previous_error: str | None = None,
    ) -> str:
        fn_name = "ema" if spec.archetype == "ema_crossover" else "sma"
        if spec.archetype == "rsi_reversion":
            return f'''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import rsi
from quant_platform.strategy.ui_meta import ui_param


class NLGeneratedRsiParams(BaseModel):
    rsi_window: int = ui_param({spec.rsi_window}, ge=2, le=200, ui_group="signal", unit="Bars", description="RSI 计算回看周期")
    oversold: Decimal = ui_param(Decimal("{spec.rsi_oversold}"), ui_group="signal", unit="RSI", description="超卖开多阈值")
    overbought: Decimal = ui_param(Decimal("{spec.rsi_overbought}"), ui_group="signal", unit="RSI", description="超买平仓阈值")
    order_qty: Decimal = ui_param(Decimal("{spec.order_qty}"), ui_group="risk_sizing", unit="BTC", danger_level="high", danger_hint="单笔委托基础资产数量，放大将等比例增加保证金占用与强平风险")
''' + f'''

class NLGeneratedStrategy(Strategy):
    ParamModel = NLGeneratedRsiParams

    def on_start(self):
        self._closes = []
        return None

    def on_bar(self, bar: Bar):
        self._closes.append(bar.close)
        need = self.params.rsi_window + 1
        if len(self._closes) < need:
            return None
        val = rsi(self._closes[-need:], self.params.rsi_window)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")
        if val <= self.params.oversold and cur_qty == Decimal("0"):
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.params.order_qty,
                    reason="nl_rsi_oversold",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if val >= self.params.overbought and cur_qty > Decimal("0"):
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=cur_qty,
                    reduce_only=True,
                    reason="nl_rsi_overbought",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None
'''

        return f'''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import {fn_name}
from quant_platform.strategy.ui_meta import ui_param


class NLGeneratedCrossoverParams(BaseModel):
    fast_window: int = ui_param({spec.fast_window}, ge=2, le=200, ui_group="signal", unit="Bars", description="快线周期窗口")
    slow_window: int = ui_param({spec.slow_window}, ge=3, le=500, ui_group="signal", unit="Bars", description="慢线周期窗口")
    order_qty: Decimal = ui_param(Decimal("{spec.order_qty}"), ui_group="risk_sizing", unit="BTC", danger_level="high", danger_hint="单笔下单数量，放大将直接提升单笔名义敞口与保证金需求")


class NLGeneratedStrategy(Strategy):
    ParamModel = NLGeneratedCrossoverParams

    def on_start(self):
        self._closes = []
        self._prev_fast = None
        self._prev_slow = None
        return None

    def on_bar(self, bar: Bar):
        self._closes.append(bar.close)
        if len(self._closes) < self.params.slow_window:
            return None
        tail = self._closes[-max(self.params.slow_window * 4, 160):]
        cur_fast = {fn_name}(tail, self.params.fast_window)
        cur_slow = {fn_name}(tail, self.params.slow_window)
        intents = None
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")

        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and cur_fast > cur_slow
            crossed_down = self._prev_fast >= self._prev_slow and cur_fast < cur_slow
            if crossed_up and cur_qty == Decimal("0"):
                intents = [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.BUY,
                        order_type=OrderType.MARKET,
                        quantity=self.params.order_qty,
                        reason="nl_ma_cross_up",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            elif crossed_down and cur_qty > Decimal("0"):
                intents = [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=cur_qty,
                        reduce_only=True,
                        reason="nl_ma_cross_down",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
        self._prev_fast = cur_fast
        self._prev_slow = cur_slow
        return intents
'''


class OpenAICompatibleLLMProvider(LLMCodeGenerator):
    """OpenAI-compatible chat-completions LLM provider (supports self-hosted vLLM/Ollama or OpenAI)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec

    def synthesize_code(
        self,
        spec: NLStrategySpec,
        *,
        previous_error: str | None = None,
    ) -> str:
        sys_msg = (
            "Generate a single Python module defining a Pydantic ParamModel (Decimal only, NO float) "
            "and a Strategy subclass using quant_platform.core.interfaces.Strategy. "
            "Output ONLY raw Python code without markdown fences. Never import os, sys, subprocess, socket, datetime, or time."
        )
        user_msg = f"Strategy specification: {spec.model_dump_json()}"
        if previous_error:
            user_msg += f"\nFix previous validation error: {previous_error}"

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
            },
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        fenced = re.search(r"```(?:python)?\s*(.*?)```", content, flags=re.DOTALL)
        return (fenced.group(1) if fenced else content).strip() + "\n"
