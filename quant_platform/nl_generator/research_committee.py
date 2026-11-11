"""Multi-Agent Closed-Loop Strategy Research Committee (Director -> Quant Coder -> Real Backtest -> Risk Critic).

Inspired by multi-role investment committee workflows (e.g. AutoHedge's role separation),
but anchored strictly to QuantPlatform's 10 Red Lines:
  1. Strategies are deterministic Python classes using StrictDecimal (zero runtime LLM hallucinations).
  2. Every candidate passes ASTSecurityChecker + Pydantic ParamModel + ContainerStrategySandbox.
  3. Every round is evaluated by the real BacktestEngine on historical OHLCV + funding settlements.
  4. Risk Critic diagnoses empirical metrics (turnover/fee drag, drawdown, payoff asymmetry, Sharpe)
     and drives concrete structural code evolutions across rounds.
  5. All versions remain ApprovalStatus.PENDING (mandatory human approval before Shadow/Live).
"""

from __future__ import annotations

from decimal import Decimal
import json
import math
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from quant_platform.backtest.engine import BacktestEngine
from quant_platform.backtest.report import build_and_persist_backtest_report
from quant_platform.backtest.research import periods_per_year_for_interval
from quant_platform.core.decimal_utils import ZERO, StrictDecimal, format_decimal
from quant_platform.core.enums import ApprovalStatus, MarginMode, RunStatus, StrategySourceType
from quant_platform.core.interfaces import Clock
from quant_platform.core.models import Bar, FundingRatePoint, StrategyConfig, SymbolSpec
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db.schema import StrategyVersionRecord
from quant_platform.strategy.ast_checker import ASTSecurityChecker
from quant_platform.strategy.registry import (
    compile_strategy_class,
    extract_param_schema_and_defaults,
)
from quant_platform.strategy.version_store import StrategyVersionStore


# ==============================================================================
# Typed Multi-Agent Committee Domain Models
# ==============================================================================


class MarketRegimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str
    symbol: str
    interval: str
    bar_count: int
    start_price: str
    end_price: str
    buy_and_hold_return_pct: float
    annualized_volatility_pct: float
    efficiency_ratio: float
    regime_label: str  # STRONG_TREND | VOLATILE_SWING | MEAN_REVERTING_CHOP
    regime_summary_zh: str


class DirectorThesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archetype: str  # donchian_breakout | ema_crossover | sma_crossover | rsi_reversion | bollinger_reversion
    symbol: str
    exchange: str
    market_regime: str
    thesis_title: str
    thesis_summary: str
    entry_logic_zh: str
    exit_logic_zh: str
    risk_plan_zh: str
    fast_window: int
    slow_window: int
    rsi_window: int = 14
    rsi_oversold: str = "30"
    rsi_overbought: str = "70"
    base_order_qty: str


class RiskCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str  # PASS_ELITE | NEEDS_REFINEMENT | HIGH_RISK_WARNING
    composite_score: float
    findings: list[str] = Field(default_factory=list)
    recommended_mutations: list[str] = Field(default_factory=list)


class CommitteeRoundRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_index: int
    version_id: str
    version: int
    run_id: str
    mutation_tags: list[str]
    coder_summary: str
    cumulative_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    win_rate: float
    payoff_ratio: float
    closed_trade_count: int
    turnover_rate: float
    total_fees_usdt: str
    total_funding_usdt: str
    composite_score: float
    critic_verdict: str
    critic_findings: list[str]
    recommended_mutations: list[str]


# ==============================================================================
# Agent 1: Empirical Market Profiler + Director Agent (总监立意官)
# ==============================================================================


def build_market_regime_profile(
    bars: list[Bar],
    *,
    exchange: str,
    symbol: str,
) -> MarketRegimeProfile:
    if not bars:
        return MarketRegimeProfile(
            exchange=exchange,
            symbol=symbol,
            interval="1h",
            bar_count=0,
            start_price="0",
            end_price="0",
            buy_and_hold_return_pct=0.0,
            annualized_volatility_pct=0.0,
            efficiency_ratio=0.0,
            regime_label="VOLATILE_SWING",
            regime_summary_zh="样本为空，采用均衡趋势摆动假设。",
        )

    closes = [float(b.close) for b in bars]
    start_px = bars[0].close
    end_px = bars[-1].close
    bnh_ret = ((closes[-1] / closes[0]) - 1.0) * 100.0 if closes[0] > 0 else 0.0

    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] / closes[i - 1]) - 1.0)

    ppy = periods_per_year_for_interval(bars[0].interval)
    if len(rets) >= 2:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
        ann_vol = math.sqrt(var_r * ppy) * 100.0
    else:
        ann_vol = 0.0

    # Rolling 168-bar (1-week on 1H) Kaufman Efficiency Ratio median/mean
    chunk = min(168, max(10, len(closes) // 4))
    ers: list[float] = []
    for start_idx in range(0, max(1, len(closes) - chunk), max(1, chunk // 2)):
        seg = closes[start_idx : start_idx + chunk]
        if len(seg) < 5:
            continue
        net_move = abs(seg[-1] - seg[0])
        path_move = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg)))
        if path_move > 0:
            ers.append(net_move / path_move)
    eff_ratio = (sum(ers) / len(ers)) if ers else 0.20

    if eff_ratio >= 0.24 or abs(bnh_ret) >= 45.0:
        label = "STRONG_TREND"
        summary = (
            f"{symbol} 区间涨跌幅 {bnh_ret:+.1f}%，年化波动率 {ann_vol:.1f}%，趋势效率系数 {eff_ratio:.2f}："
            "呈现显著中大级别趋势扩张特征，适合顺大势突破或均线趋势跟踪，并拉宽周期抑制手续费磨损。"
        )
    elif eff_ratio <= 0.14 and abs(bnh_ret) < 20.0:
        label = "MEAN_REVERTING_CHOP"
        summary = (
            f"{symbol} 区间涨跌幅 {bnh_ret:+.1f}%，年化波动率 {ann_vol:.1f}%，趋势效率系数 {eff_ratio:.2f}："
            "呈现箱体震荡与均值回归特征，需严控追涨杀跌假突破，优先结合超卖反弹或宏观均线过滤。"
        )
    else:
        label = "VOLATILE_SWING"
        summary = (
            f"{symbol} 区间涨跌幅 {bnh_ret:+.1f}%，年化波动率 {ann_vol:.1f}%，趋势效率系数 {eff_ratio:.2f}："
            "呈现高波动宽幅波段交替特征，需兼顾中周期趋势确认与移动止盈止损保护。"
        )

    return MarketRegimeProfile(
        exchange=exchange,
        symbol=symbol,
        interval=bars[0].interval,
        bar_count=len(bars),
        start_price=format_decimal(start_px),
        end_price=format_decimal(end_px),
        buy_and_hold_return_pct=round(bnh_ret, 2),
        annualized_volatility_pct=round(ann_vol, 2),
        efficiency_ratio=round(eff_ratio, 4),
        regime_label=label,
        regime_summary_zh=summary,
    )


def _compute_reasonable_order_qty(
    prompt: str,
    bars: list[Bar],
    spec: SymbolSpec,
    initial_usdt: Decimal,
) -> Decimal:
    """Derive a realistic base-asset order quantity (~75% notional of initial capital) if not explicit."""
    lower = prompt.lower()
    qty_match = re.search(
        r"(?:买入|卖出|下单|委托|仓位|buy|sell|qty)\s*(\d+(?:\.\d+)?)\s*(?:btc|eth|sol|bnb|枚|张|个)?",
        lower,
    )
    if qty_match:
        candidate = Decimal(qty_match.group(1))
        # Guard against percentage numbers like '25%' accidentally matched if no unit
        if "%" not in lower[qty_match.start() : min(len(lower), qty_match.end() + 2)]:
            step = spec.step_size if spec.step_size > ZERO else Decimal("0.001")
            snapped = (candidate // step) * step
            if snapped >= spec.min_qty:
                return snapped

    ref_px = bars[-1].close if bars else Decimal("50000")
    if ref_px <= ZERO:
        ref_px = Decimal("50000")
    target_notional = initial_usdt * Decimal("0.85")
    raw_qty = target_notional / ref_px
    step = spec.step_size if spec.step_size > ZERO else Decimal("0.001")
    snapped = (raw_qty // step) * step
    return max(spec.min_qty, snapped)


class DirectorAgent:
    """Translates operator research intent + empirical MarketRegimeProfile into a structured DirectorThesis."""

    def formulate_thesis(
        self,
        *,
        prompt: str,
        profile: MarketRegimeProfile,
        bars: list[Bar],
        spec: SymbolSpec,
        initial_usdt: Decimal,
    ) -> DirectorThesis:
        text = prompt.strip()
        lower = text.lower()
        is_hourly_or_slower = profile.interval.endswith(("h", "d", "w"))

        # Detect archetype from user prompt + market regime
        if any(k in lower for k in ("布林", "bollinger", "bband")):
            archetype = "bollinger_reversion"
        elif "rsi" in lower or any(k in text for k in ("超卖", "超买", "均值回归", "抄底")):
            archetype = "rsi_reversion"
        elif any(k in lower for k in ("突破", "breakout", "donchian", "唐奇安", "通道", "新高")):
            archetype = "donchian_breakout"
        elif "ema" in lower or "指数均线" in text:
            archetype = "ema_crossover"
        elif any(k in text for k in ("均线", "金叉", "死叉")) or "sma" in lower:
            archetype = "sma_crossover"
        else:
            # Director autonomous selection based on empirical market regime!
            if profile.regime_label == "STRONG_TREND":
                archetype = "donchian_breakout"
            elif profile.regime_label == "MEAN_REVERTING_CHOP":
                archetype = "rsi_reversion"
            else:
                archetype = "ema_crossover"

        win_matches = [
            int(m)
            for m in re.findall(r"(\d+)\s*(?:周期|小时|日|根|h|bar|period|均线|sma|ema)", lower)
            if 2 <= int(m) <= 400
        ]
        if len(win_matches) >= 2:
            fast_w, slow_w = sorted(win_matches[:2])
        elif archetype == "donchian_breakout":
            fast_w, slow_w = (12, 24) if is_hourly_or_slower else (8, 20)
        elif is_hourly_or_slower:
            # Initial Round 1 starts with responsive windows so Risk Critic can empirically benchmark churn vs lag
            fast_w, slow_w = 8, 24
        else:
            fast_w, slow_w = 5, 15

        order_qty = _compute_reasonable_order_qty(prompt, bars, spec, initial_usdt)

        archetype_titles = {
            "donchian_breakout": "唐奇安通道趋势突破体系",
            "ema_crossover": "双指数均线 (EMA) 动量跟踪体系",
            "sma_crossover": "双简单均线 (SMA) 趋势跟踪体系",
            "rsi_reversion": "RSI 极值超卖均值回归体系",
            "bollinger_reversion": "布林带下轨偏离回归体系",
        }
        entry_desc = {
            "donchian_breakout": f"收盘价突破过去 {slow_w} 根 K 线最高价形成动量共振时顺势开多",
            "ema_crossover": f"快线 EMA({fast_w}) 上穿慢线 EMA({slow_w}) 确认短期动量转强时做多",
            "sma_crossover": f"快线 SMA({fast_w}) 上穿慢线 SMA({slow_w}) 形成金叉时顺势做多",
            "rsi_reversion": "RSI(14) 跌破超卖阈值 30 释放恐慌抛压后低吸做多",
            "bollinger_reversion": "价格跌破 20 周期布林带下轨（2倍标准差）触发超跌回归买入",
        }[archetype]
        exit_desc = {
            "donchian_breakout": f"跌破 {fast_w} 周期防守低点通道或触发风控保护线时平仓落袋",
            "ema_crossover": f"快线 EMA({fast_w}) 下穿慢线 EMA({slow_w}) 或触发风控保护时平仓",
            "sma_crossover": f"快线 SMA({fast_w}) 下穿慢线 SMA({slow_w}) 或触发风控保护时平仓",
            "rsi_reversion": "RSI 回升至超买区或回到均值上方时止盈离场",
            "bollinger_reversion": "价格回归布林带中轨上方或触发保护止损时平仓",
        }[archetype]

        return DirectorThesis(
            archetype=archetype,
            symbol=spec.symbol,
            exchange=spec.exchange,
            market_regime=profile.regime_label,
            thesis_title=f"{spec.symbol} · {archetype_titles[archetype]}",
            thesis_summary=(
                f"基于 {profile.bar_count} 根真实 {profile.interval} K 线诊断（{profile.regime_summary_zh}），"
                f"立意采用「{archetype_titles[archetype]}」作为第 1 轮基准原型，并在后续轮次由风控评审官检验换手损耗、回撤与盈亏比。"
            ),
            entry_logic_zh=entry_desc,
            exit_logic_zh=exit_desc,
            risk_plan_zh=(
                f"单笔基准仓位 {format_decimal(order_qty)} {spec.base_asset}（约占初始本金 85% 名义敞口），"
                "若首轮回测暴露震荡磨损或深回撤，自动迭代注入 200 周期宏观趋势过滤、冷却窗口与移动止盈止损。"
            ),
            fast_window=max(2, fast_w),
            slow_window=max(fast_w + 2, slow_w),
            base_order_qty=format_decimal(order_qty),
        )


# ==============================================================================
# Agent 2: Quant Coder Agent (量化策略工程师 — AST 安全、纯 Decimal 演化代码合成)
# ==============================================================================


class QuantCoderAgent:
    """
    Synthesizes AST-safe, StrictDecimal Python Strategy code for a given DirectorThesis
    and cumulative set of structural RiskCritic mutations.
    """

    def __init__(
        self,
        *,
        llm_api_base: str = "",
        llm_api_key: str = "",
        llm_model: str = "gpt-4o-mini",
        timeout_sec: float = 25.0,
    ) -> None:
        self.llm_api_base = llm_api_base.rstrip("/") if llm_api_base else ""
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.timeout_sec = timeout_sec

    @property
    def has_external_llm(self) -> bool:
        return bool(self.llm_api_base and self.llm_api_key)

    def synthesize_round_code(
        self,
        *,
        thesis: DirectorThesis,
        round_index: int,
        active_mutations: list[str],
        bar_count: int,
        previous_code: str = "",
        previous_critique: RiskCritique | None = None,
        repair_error: str | None = None,
    ) -> tuple[str, str, bool]:
        """
        Return (python_code, coder_summary_zh, used_external_llm).
        Tries external OpenAI-compatible LLM if configured (and no fallback triggered),
        otherwise uses deterministic structural specialist synthesis.
        """
        det_code, det_summary = self._synthesize_deterministic(
            thesis=thesis,
            round_index=round_index,
            active_mutations=active_mutations,
            bar_count=bar_count,
        )
        if not self.has_external_llm or repair_error is not None:
            return det_code, det_summary, False

        try:
            ext_code = self._call_external_llm(
                thesis=thesis,
                round_index=round_index,
                active_mutations=active_mutations,
                reference_code=det_code,
                previous_code=previous_code,
                previous_critique=previous_critique,
            )
            return ext_code, f"[LLM 协同合成] {det_summary}", True
        except Exception:
            return det_code, det_summary, False

    def _call_external_llm(
        self,
        *,
        thesis: DirectorThesis,
        round_index: int,
        active_mutations: list[str],
        reference_code: str,
        previous_code: str,
        previous_critique: RiskCritique | None,
    ) -> str:
        sys_msg = (
            "You are the Quant Coder Agent inside QuantPlatform. "
            "Output ONLY valid Python source code (no markdown fences) defining a Pydantic BaseModel "
            "using Decimal + ui_param and a Strategy subclass using quant_platform.core.interfaces.Strategy. "
            "STRICT RULES: Never use float literals in orders/prices; never import os, sys, time, datetime, "
            "random, socket, subprocess, http; never call getattr, hasattr, setattr, eval, exec, open; "
            "always rebuild ParamModel via ParamModel.model_rebuild(_types_namespace={'Decimal': Decimal}) if needed."
        )
        critique_txt = (
            json.dumps(previous_critique.model_dump(), ensure_ascii=False)
            if previous_critique
            else "Initial Round 1 baseline."
        )
        user_msg = (
            f"Round {round_index} Thesis: {thesis.model_dump_json()}\n"
            f"Required structural mutations: {active_mutations}\n"
            f"Previous critique: {critique_txt}\n"
            f"Previous code:\n{previous_code[:2500]}\n"
            f"Compliant structural baseline template you may refine:\n{reference_code}"
        )
        resp = httpx.post(
            f"{self.llm_api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.llm_api_key}"},
            json={
                "model": self.llm_model,
                "temperature": 0.1,
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

    def _synthesize_deterministic(
        self,
        *,
        thesis: DirectorThesis,
        round_index: int,
        active_mutations: list[str],
        bar_count: int,
    ) -> tuple[str, str]:
        widen = "WIDEN_HORIZON_COOLDOWN" in active_mutations
        macro_filter = "ADD_MACRO_TREND_FILTER" in active_mutations
        stop_trail = "ADD_STOP_AND_TRAILING" in active_mutations
        asym_hold = "WIDEN_PROFIT_TARGET" in active_mutations

        is_long_series = bar_count >= 600
        macro_win = 180 if is_long_series else max(25, min(60, bar_count // 3))
        cooldown_bars = 12 if (widen and is_long_series) else (3 if widen else 0)

        fast_w = thesis.fast_window
        slow_w = thesis.slow_window
        if widen:
            if is_long_series:
                if thesis.archetype == "donchian_breakout":
                    fast_w = max(fast_w * 2, 24)
                    slow_w = max(slow_w * 2, 48)
                else:
                    fast_w = max(fast_w * 3, 24)
                    slow_w = max(slow_w * 3, 72)
            else:
                fast_w = max(fast_w + 3, int(fast_w * 1.5))
                slow_w = max(slow_w + 6, int(slow_w * 1.5))

        if asym_hold and thesis.archetype == "donchian_breakout":
            fast_w = max(fast_w, int(slow_w * 0.6))

        stop_pct = "0.038" if asym_hold else "0.045"
        trail_pct = "0.095" if asym_hold else "0.075"
        rsi_exit = "76" if asym_hold else thesis.rsi_overbought
        rsi_entry = "28" if widen else thesis.rsi_oversold

        notes: list[str] = []
        if round_index == 1 and not active_mutations:
            notes.append(f"实现第 1 轮基准「{thesis.thesis_title}」核心信号逻辑")
        else:
            if widen:
                notes.append(f"拉宽信号窗口至 ({fast_w}/{slow_w}) 并引入 {cooldown_bars} 根 K 线冷却期，抑制频繁换手与手续费磨损")
            if macro_filter:
                notes.append(f"注入 EMA({macro_win}) 宏观多头体制过滤，仅在顺大势区间开仓做多")
            if stop_trail:
                notes.append(f"注入 {Decimal(stop_pct)*100:.1f}% 硬止损 + {Decimal(trail_pct)*100:.1f}% 峰值移动止盈保护")
            if asym_hold:
                notes.append("放宽右侧趋势持仓容忍度，提升单笔盈亏比右尾捕获能力")

        summary_zh = "；".join(notes) if notes else "完成策略代码构建"
        base_unit = thesis.symbol.split("/")[0] if "/" in thesis.symbol else "Asset"

        if thesis.archetype in {"rsi_reversion", "bollinger_reversion"}:
            code = self._render_reversion_code(
                archetype=thesis.archetype,
                rsi_win=thesis.rsi_window,
                rsi_entry=rsi_entry,
                rsi_exit=rsi_exit,
                order_qty=thesis.base_order_qty,
                base_unit=base_unit,
                cooldown_bars=cooldown_bars,
                use_macro=macro_filter,
                macro_win=macro_win,
                use_stop_trail=stop_trail,
                stop_pct=stop_pct,
                trail_pct=trail_pct,
            )
            return code, summary_zh

        if thesis.archetype == "donchian_breakout":
            code = self._render_breakout_code(
                entry_win=slow_w,
                exit_win=fast_w,
                order_qty=thesis.base_order_qty,
                base_unit=base_unit,
                cooldown_bars=cooldown_bars,
                use_macro=macro_filter,
                macro_win=macro_win,
                use_stop_trail=stop_trail,
                stop_pct=stop_pct,
                trail_pct=trail_pct,
            )
            return code, summary_zh

        fn_name = "ema" if thesis.archetype == "ema_crossover" else "sma"
        code = self._render_ma_code(
            fn_name=fn_name,
            fast_w=fast_w,
            slow_w=slow_w,
            order_qty=thesis.base_order_qty,
            base_unit=base_unit,
            cooldown_bars=cooldown_bars,
            use_macro=macro_filter,
            macro_win=macro_win,
            use_stop_trail=stop_trail,
            stop_pct=stop_pct,
            trail_pct=trail_pct,
        )
        return code, summary_zh

    @staticmethod
    def _render_breakout_code(
        *,
        entry_win: int,
        exit_win: int,
        order_qty: str,
        base_unit: str,
        cooldown_bars: int,
        use_macro: bool,
        macro_win: int,
        use_stop_trail: bool,
        stop_pct: str,
        trail_pct: str,
    ) -> str:
        extra_params = ""
        if cooldown_bars > 0:
            extra_params += f'    cooldown_bars: int = ui_param({cooldown_bars}, ge=0, le=96, title="平仓冷却周期", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_macro:
            extra_params += f'    macro_ema_window: int = ui_param({macro_win}, ge=20, le=400, title="宏观趋势过滤均线", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_stop_trail:
            extra_params += (
                f'    stop_loss_pct: Decimal = ui_param(Decimal("{stop_pct}"), title="硬止损比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
                f'    trailing_stop_pct: Decimal = ui_param(Decimal("{trail_pct}"), title="峰值移动止盈回撤比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
            )

        macro_import = ", ema" if use_macro else ""
        macro_check = (
            "        macro_ok = len(self._closes) >= self.params.macro_ema_window and bar.close > ema(self._closes[-self.params.macro_ema_window * 2:], self.params.macro_ema_window)\n"
            if use_macro
            else "        macro_ok = True\n"
        )
        cd_check = (
            "        in_cooldown = (self._bar_idx - self._last_exit_idx) < self.params.cooldown_bars\n"
            if cooldown_bars > 0
            else "        in_cooldown = False\n"
        )
        stop_logic = ""
        if use_stop_trail:
            stop_logic = """        if cur_qty > Decimal("0") and self._entry_px > Decimal("0"):
            if bar.high > self._peak_px:
                self._peak_px = bar.high
            hard_stop = self._entry_px * (Decimal("1") - self.params.stop_loss_pct)
            trail_stop = self._peak_px * (Decimal("1") - self.params.trailing_stop_pct)
            active_stop = hard_stop if hard_stop > trail_stop else trail_stop
            if bar.close <= active_stop:
                self._last_exit_idx = self._bar_idx
                self._entry_px = Decimal("0")
                self._peak_px = Decimal("0")
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=cur_qty,
                        reduce_only=True,
                        reason="committee_protective_stop",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
"""

        return f'''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import sma{macro_import}
from quant_platform.strategy.ui_meta import ui_param


class CommitteeBreakoutParams(BaseModel):
    entry_lookback: int = ui_param({entry_win}, ge=4, le=240, title="突破开仓通道周期", ui_group="signal", unit="Bars", danger_level="safe")
    exit_lookback: int = ui_param({exit_win}, ge=2, le=180, title="防守离场通道周期", ui_group="signal", unit="Bars", danger_level="safe")
{extra_params}    order_qty: Decimal = ui_param(Decimal("{order_qty}"), title="单笔下单数量", ui_group="risk_sizing", unit="{base_unit}", danger_level="high", danger_hint="放大下单数量将直接提升保证金占用与强平风险")


CommitteeBreakoutParams.model_rebuild(_types_namespace={{"Decimal": Decimal}})


class CommitteeBreakoutStrategy(Strategy):
    ParamModel = CommitteeBreakoutParams

    def on_start(self):
        self._highs = []
        self._lows = []
        self._closes = []
        self._bar_idx = 0
        self._last_exit_idx = -9999
        self._entry_px = Decimal("0")
        self._peak_px = Decimal("0")
        return None

    def on_bar(self, bar: Bar):
        self._bar_idx += 1
        if len(self._highs) < self.params.entry_lookback:
            self._highs.append(bar.high)
            self._lows.append(bar.low)
            self._closes.append(bar.close)
            return None

        prev_channel_high = max(self._highs[-self.params.entry_lookback:])
        prev_channel_low = min(self._lows[-self.params.exit_lookback:])

        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._closes.append(bar.close)
        if len(self._closes) > 600:
            self._highs = self._highs[-600:]
            self._lows = self._lows[-600:]
            self._closes = self._closes[-600:]

        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")

{stop_logic}{macro_check}{cd_check}        if cur_qty == Decimal("0") and not in_cooldown and macro_ok and bar.close > prev_channel_high:
            self._entry_px = bar.close
            self._peak_px = bar.high
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.params.order_qty,
                    reason="committee_donchian_breakout",
                    ts_ms=self.ctx.now_ms,
                )
            ]

        if cur_qty > Decimal("0") and bar.close < prev_channel_low:
            self._last_exit_idx = self._bar_idx
            self._entry_px = Decimal("0")
            self._peak_px = Decimal("0")
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=cur_qty,
                    reduce_only=True,
                    reason="committee_channel_exit",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None
'''

    @staticmethod
    def _render_ma_code(
        *,
        fn_name: str,
        fast_w: int,
        slow_w: int,
        order_qty: str,
        base_unit: str,
        cooldown_bars: int,
        use_macro: bool,
        macro_win: int,
        use_stop_trail: bool,
        stop_pct: str,
        trail_pct: str,
    ) -> str:
        extra_params = ""
        if cooldown_bars > 0:
            extra_params += f'    cooldown_bars: int = ui_param({cooldown_bars}, ge=0, le=96, title="平仓冷却周期", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_macro:
            extra_params += f'    macro_ema_window: int = ui_param({macro_win}, ge=20, le=400, title="宏观趋势过滤均线", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_stop_trail:
            extra_params += (
                f'    stop_loss_pct: Decimal = ui_param(Decimal("{stop_pct}"), title="硬止损比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
                f'    trailing_stop_pct: Decimal = ui_param(Decimal("{trail_pct}"), title="峰值移动止盈回撤比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
            )

        imports = "ema" if fn_name == "ema" else ("sma, ema" if use_macro else "sma")
        macro_check = (
            "        macro_ok = len(self._closes) >= self.params.macro_ema_window and bar.close > ema(self._closes[-self.params.macro_ema_window * 2:], self.params.macro_ema_window)\n"
            if use_macro
            else "        macro_ok = True\n"
        )
        cd_check = (
            "        in_cooldown = (self._bar_idx - self._last_exit_idx) < self.params.cooldown_bars\n"
            if cooldown_bars > 0
            else "        in_cooldown = False\n"
        )
        stop_logic = ""
        if use_stop_trail:
            stop_logic = """        if cur_qty > Decimal("0") and self._entry_px > Decimal("0"):
            if bar.high > self._peak_px:
                self._peak_px = bar.high
            hard_stop = self._entry_px * (Decimal("1") - self.params.stop_loss_pct)
            trail_stop = self._peak_px * (Decimal("1") - self.params.trailing_stop_pct)
            active_stop = hard_stop if hard_stop > trail_stop else trail_stop
            if bar.close <= active_stop:
                self._last_exit_idx = self._bar_idx
                self._entry_px = Decimal("0")
                self._peak_px = Decimal("0")
                self._prev_fast = cur_fast
                self._prev_slow = cur_slow
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=cur_qty,
                        reduce_only=True,
                        reason="committee_protective_stop",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
"""

        return f'''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import {imports}
from quant_platform.strategy.ui_meta import ui_param


class CommitteeTrendParams(BaseModel):
    fast_window: int = ui_param({fast_w}, ge=2, le=200, title="快线均线周期", ui_group="signal", unit="Bars", danger_level="safe")
    slow_window: int = ui_param({slow_w}, ge=3, le=400, title="慢线均线周期", ui_group="signal", unit="Bars", danger_level="safe")
{extra_params}    order_qty: Decimal = ui_param(Decimal("{order_qty}"), title="单笔下单数量", ui_group="risk_sizing", unit="{base_unit}", danger_level="high", danger_hint="放大下单数量将直接提升保证金占用与强平风险")


CommitteeTrendParams.model_rebuild(_types_namespace={{"Decimal": Decimal}})


class CommitteeTrendStrategy(Strategy):
    ParamModel = CommitteeTrendParams

    def on_start(self):
        self._closes = []
        self._prev_fast = None
        self._prev_slow = None
        self._bar_idx = 0
        self._last_exit_idx = -9999
        self._entry_px = Decimal("0")
        self._peak_px = Decimal("0")
        return None

    def on_bar(self, bar: Bar):
        self._bar_idx += 1
        self._closes.append(bar.close)
        if len(self._closes) > 800:
            self._closes = self._closes[-800:]
        if len(self._closes) < self.params.slow_window:
            return None

        tail = self._closes[-max(self.params.slow_window * 3, 180):]
        cur_fast = {fn_name}(tail, self.params.fast_window)
        cur_slow = {fn_name}(tail, self.params.slow_window)

        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")
{stop_logic}{macro_check}{cd_check}        intents = None
        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and cur_fast > cur_slow
            crossed_down = self._prev_fast >= self._prev_slow and cur_fast < cur_slow
            if crossed_up and cur_qty == Decimal("0") and not in_cooldown and macro_ok:
                self._entry_px = bar.close
                self._peak_px = bar.high
                intents = [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.BUY,
                        order_type=OrderType.MARKET,
                        quantity=self.params.order_qty,
                        reason="committee_{fn_name}_cross_up",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            elif crossed_down and cur_qty > Decimal("0"):
                self._last_exit_idx = self._bar_idx
                self._entry_px = Decimal("0")
                self._peak_px = Decimal("0")
                intents = [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=cur_qty,
                        reduce_only=True,
                        reason="committee_{fn_name}_cross_down",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
        self._prev_fast = cur_fast
        self._prev_slow = cur_slow
        return intents
'''

    @staticmethod
    def _render_reversion_code(
        *,
        archetype: str,
        rsi_win: int,
        rsi_entry: str,
        rsi_exit: str,
        order_qty: str,
        base_unit: str,
        cooldown_bars: int,
        use_macro: bool,
        macro_win: int,
        use_stop_trail: bool,
        stop_pct: str,
        trail_pct: str,
    ) -> str:
        extra_params = ""
        if cooldown_bars > 0:
            extra_params += f'    cooldown_bars: int = ui_param({cooldown_bars}, ge=0, le=96, title="平仓冷却周期", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_macro:
            extra_params += f'    macro_ema_window: int = ui_param({macro_win}, ge=20, le=400, title="宏观趋势过滤均线", ui_group="signal", unit="Bars", danger_level="safe")\n'
        if use_stop_trail:
            extra_params += (
                f'    stop_loss_pct: Decimal = ui_param(Decimal("{stop_pct}"), title="硬止损比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
                f'    trailing_stop_pct: Decimal = ui_param(Decimal("{trail_pct}"), title="峰值移动止盈回撤比例", ui_group="risk_sizing", unit="比例", danger_level="caution")\n'
            )

        use_bb = archetype == "bollinger_reversion"
        ind_import = "bollinger_bands" if use_bb else "rsi"
        if use_macro:
            ind_import += ", ema"

        macro_check = (
            "        macro_ok = len(self._closes) < self.params.macro_ema_window or bar.close >= ema(self._closes[-self.params.macro_ema_window * 2:], self.params.macro_ema_window) * Decimal(\"0.94\")\n"
            if use_macro
            else "        macro_ok = True\n"
        )
        cd_check = (
            "        in_cooldown = (self._bar_idx - self._last_exit_idx) < self.params.cooldown_bars\n"
            if cooldown_bars > 0
            else "        in_cooldown = False\n"
        )
        stop_logic = ""
        if use_stop_trail:
            stop_logic = """        if cur_qty > Decimal("0") and self._entry_px > Decimal("0"):
            if bar.high > self._peak_px:
                self._peak_px = bar.high
            hard_stop = self._entry_px * (Decimal("1") - self.params.stop_loss_pct)
            trail_stop = self._peak_px * (Decimal("1") - self.params.trailing_stop_pct)
            active_stop = hard_stop if hard_stop > trail_stop else trail_stop
            if bar.close <= active_stop:
                self._last_exit_idx = self._bar_idx
                self._entry_px = Decimal("0")
                self._peak_px = Decimal("0")
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=cur_qty,
                        reduce_only=True,
                        reason="committee_protective_stop",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
"""

        if use_bb:
            sig_params = (
                '    bb_window: int = ui_param(20, ge=5, le=120, title="布林带周期", ui_group="signal", unit="Bars", danger_level="safe")\n'
                '    bb_std: Decimal = ui_param(Decimal("2.0"), title="布林带标准差倍数", ui_group="signal", unit="σ", danger_level="safe")\n'
            )
            sig_calc = (
                "        if len(self._closes) < self.params.bb_window:\n"
                "            return None\n"
                "        lower_b, mid_b, upper_b = bollinger_bands(self._closes, self.params.bb_window, self.params.bb_std)\n"
                "        buy_cond = bar.close <= lower_b\n"
                "        sell_cond = bar.close >= mid_b\n"
            )
        else:
            sig_params = (
                f'    rsi_window: int = ui_param({rsi_win}, ge=2, le=100, title="RSI 计算周期", ui_group="signal", unit="Bars", danger_level="safe")\n'
                f'    oversold: Decimal = ui_param(Decimal("{rsi_entry}"), title="超卖开多阈值", ui_group="signal", unit="RSI", danger_level="safe")\n'
                f'    overbought: Decimal = ui_param(Decimal("{rsi_exit}"), title="超买止盈阈值", ui_group="signal", unit="RSI", danger_level="safe")\n'
            )
            sig_calc = (
                "        need = self.params.rsi_window + 1\n"
                "        if len(self._closes) < need:\n"
                "            return None\n"
                "        val = rsi(self._closes[-need:], self.params.rsi_window)\n"
                "        buy_cond = val <= self.params.oversold\n"
                "        sell_cond = val >= self.params.overbought\n"
            )

        return f'''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import {ind_import}
from quant_platform.strategy.ui_meta import ui_param


class CommitteeReversionParams(BaseModel):
{sig_params}{extra_params}    order_qty: Decimal = ui_param(Decimal("{order_qty}"), title="单笔下单数量", ui_group="risk_sizing", unit="{base_unit}", danger_level="high", danger_hint="放大下单数量将直接提升保证金占用与强平风险")


CommitteeReversionParams.model_rebuild(_types_namespace={{"Decimal": Decimal}})


class CommitteeReversionStrategy(Strategy):
    ParamModel = CommitteeReversionParams

    def on_start(self):
        self._closes = []
        self._bar_idx = 0
        self._last_exit_idx = -9999
        self._entry_px = Decimal("0")
        self._peak_px = Decimal("0")
        return None

    def on_bar(self, bar: Bar):
        self._bar_idx += 1
        self._closes.append(bar.close)
        if len(self._closes) > 800:
            self._closes = self._closes[-800:]
{sig_calc}
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")
{stop_logic}{macro_check}{cd_check}        if buy_cond and cur_qty == Decimal("0") and not in_cooldown and macro_ok:
            self._entry_px = bar.close
            self._peak_px = bar.high
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.params.order_qty,
                    reason="committee_reversion_entry",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if sell_cond and cur_qty > Decimal("0"):
            self._last_exit_idx = self._bar_idx
            self._entry_px = Decimal("0")
            self._peak_px = Decimal("0")
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=cur_qty,
                    reduce_only=True,
                    reason="committee_reversion_exit",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None
'''


# ==============================================================================
# Agent 3: Risk Critic Agent (首席风控评审官 — 基于真实回测指标诊断与开具改进处方)
# ==============================================================================


class RiskCriticAgent:
    """Evaluates authentic BacktestEngine reports and prescribes structural code mutations."""

    def audit_backtest_report(
        self,
        *,
        report: dict[str, Any],
        target_max_drawdown_pct: float,
        already_applied: list[str],
    ) -> RiskCritique:
        sm = report.get("summary_metrics", {})
        tm = report.get("trade_metrics", {})
        cb = report.get("cost_breakdown", {})

        cum_ret = float(sm.get("cumulative_return", 0.0) or 0.0)
        max_dd = abs(float(sm.get("max_drawdown", 0.0) or 0.0))
        sharpe = float(sm.get("sharpe", 0.0) or 0.0)
        turnover = float(sm.get("turnover_rate", 0.0) or 0.0)
        trades = int(tm.get("closed_trade_count", 0) or 0)
        win_rate = float(tm.get("win_rate", 0.0) or 0.0)
        payoff = float(tm.get("payoff_ratio", 0.0) or 0.0)
        total_fees = abs(float(cb.get("total_fees", 0.0) or 0.0))
        init_eq = float(sm.get("initial_equity", 10000.0) or 10000.0)
        fee_drag_ratio = (total_fees / init_eq) if init_eq > 0 else 0.0

        # Composite risk-adjusted score (0..100 scale centered around 50)
        dd_excess = max(0.0, max_dd - target_max_drawdown_pct)
        trade_penalty = 22.0 if trades == 0 else (8.0 if trades < 3 else 0.0)
        fee_penalty = min(18.0, fee_drag_ratio * 35.0)
        raw_score = (
            50.0
            + (sharpe * 16.0)
            + (cum_ret * 28.0)
            - (max_dd * 32.0)
            - (dd_excess * 55.0)
            - fee_penalty
            - trade_penalty
        )
        score = round(max(0.0, min(99.9, raw_score)), 2)

        findings: list[str] = []
        mutations: list[str] = []

        if trades == 0:
            findings.append("当前参数条件下未触发完整闭环交易，需适度放宽信号灵敏度或检查过滤门槛。")
        if turnover > 95.0 or trades > 90 or fee_drag_ratio > 0.12:
            findings.append(
                f"换手率偏高（{turnover:.1f}x，闭环交易 {trades} 笔，累计手续费 ${total_fees:,.1f} 占初始本金 {fee_drag_ratio*100:.1f}%），"
                "短周期噪音导致双边 Taker 手续费持续侵蚀净值。"
            )
            if "WIDEN_HORIZON_COOLDOWN" not in already_applied:
                mutations.append("WIDEN_HORIZON_COOLDOWN")

        if max_dd > target_max_drawdown_pct:
            findings.append(
                f"最大回撤 {max_dd*100:.2f}% 超出预设风控上限 {target_max_drawdown_pct*100:.1f}%，逆势震荡段缺乏保护性截断。"
            )
            if "ADD_MACRO_TREND_FILTER" not in already_applied:
                mutations.append("ADD_MACRO_TREND_FILTER")
            if "ADD_STOP_AND_TRAILING" not in already_applied:
                mutations.append("ADD_STOP_AND_TRAILING")

        if payoff < 1.75 or sharpe < 0.75:
            findings.append(
                f"当前盈亏比 {payoff:.2f}x、夏普比率 {sharpe:.2f}（胜率 {win_rate*100:.1f}%），右尾利润释放与顺大势过滤仍有提升空间。"
            )
            if "ADD_MACRO_TREND_FILTER" not in already_applied and "ADD_MACRO_TREND_FILTER" not in mutations:
                mutations.append("ADD_MACRO_TREND_FILTER")
            elif "ADD_STOP_AND_TRAILING" not in already_applied and "ADD_STOP_AND_TRAILING" not in mutations:
                mutations.append("ADD_STOP_AND_TRAILING")
            elif "WIDEN_PROFIT_TARGET" not in already_applied and "WIDEN_PROFIT_TARGET" not in mutations:
                mutations.append("WIDEN_PROFIT_TARGET")

        # Ensure progressive exploration if more rounds remain and not all structural upgrades were tested yet
        if not mutations:
            for candidate_mut in (
                "WIDEN_HORIZON_COOLDOWN",
                "ADD_MACRO_TREND_FILTER",
                "ADD_STOP_AND_TRAILING",
                "WIDEN_PROFIT_TARGET",
            ):
                if candidate_mut not in already_applied:
                    mutations.append(candidate_mut)
                    findings.append(
                        f"各项核心风险指标达标（收益 {cum_ret*100:+.2f}%，最大回撤 -{max_dd*100:.2f}%，夏普 {sharpe:.2f}），"
                        f"下一轮继续探索结构增强项 `{candidate_mut}` 验证是否能进一步提升夏普与卡玛比率。"
                    )
                    break

        if max_dd <= target_max_drawdown_pct and sharpe >= 0.75 and cum_ret > 0 and trades >= 3:
            verdict = "PASS_ELITE"
            if not findings:
                findings.append(
                    f"通过首席风控官量化门禁：累计收益 {cum_ret*100:+.2f}%，最大回撤 -{max_dd*100:.2f}%，夏普 {sharpe:.2f}，盈亏比 {payoff:.2f}x。"
                )
        elif max_dd > target_max_drawdown_pct * 1.35 or cum_ret < -0.15:
            verdict = "HIGH_RISK_WARNING"
        else:
            verdict = "NEEDS_REFINEMENT"

        return RiskCritique(
            verdict=verdict,
            composite_score=score,
            findings=findings,
            recommended_mutations=mutations[:2],
        )


# ==============================================================================
# Closed-Loop Multi-Agent Orchestrator
# ==============================================================================


class AutoResearchCommittee:
    """
    Orchestrates Director -> Quant Coder -> AST/Sandbox -> Real Backtest -> Risk Critic
    across multiple evolutionary rounds, promoting the champion version in PENDING state.
    """

    def __init__(
        self,
        *,
        session: Session,
        version_store: StrategyVersionStore,
        clock: Clock,
        llm_api_base: str = "",
        llm_api_key: str = "",
        llm_model: str = "gpt-4o-mini",
    ) -> None:
        self.session = session
        self.version_store = version_store
        self.clock = clock
        self._checker = ASTSecurityChecker()
        self.director = DirectorAgent()
        self.coder = QuantCoderAgent(
            llm_api_base=llm_api_base,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
        )
        self.critic = RiskCriticAgent()

    def evolve_and_register(
        self,
        *,
        strategy_id: str,
        name: str,
        prompt: str,
        author: str,
        bars: list[Bar],
        spec: SymbolSpec,
        fundings: list[FundingRatePoint] | None = None,
        max_rounds: int = 3,
        target_max_drawdown_pct: StrictDecimal = Decimal("0.25"),
        initial_usdt: StrictDecimal = Decimal("10000"),
        leverage: StrictDecimal = Decimal("2"),
        description: str = "",
    ) -> dict[str, Any]:
        rounds_cap = max(1, min(5, int(max_rounds)))
        profile = build_market_regime_profile(bars, exchange=spec.exchange, symbol=spec.symbol)
        thesis = self.director.formulate_thesis(
            prompt=prompt,
            profile=profile,
            bars=bars,
            spec=spec,
            initial_usdt=initial_usdt,
        )

        active_mutations: list[str] = []
        round_records: list[CommitteeRoundRecord] = []
        round_artifacts: list[dict[str, Any]] = []
        prev_code = ""
        prev_critique: RiskCritique | None = None
        any_external_llm = False

        for r_idx in range(1, rounds_cap + 1):
            code = ""
            coder_summary = ""
            cls = None
            validated_params: dict[str, Any] = {}
            repair_err: str | None = None

            for _attempt in range(2):
                code, coder_summary, used_ext = self.coder.synthesize_round_code(
                    thesis=thesis,
                    round_index=r_idx,
                    active_mutations=list(active_mutations),
                    bar_count=len(bars),
                    previous_code=prev_code,
                    previous_critique=prev_critique,
                    repair_error=repair_err,
                )
                any_external_llm = any_external_llm or used_ext
                try:
                    self._checker.assert_safe(code)
                    cls = compile_strategy_class(code, checker=self._checker)
                    _, validated_params = extract_param_schema_and_defaults(cls, None)
                    self.version_store.sandbox.execute_isolated(
                        code,
                        {"action": "smoke_test", "params": validated_params},
                    )
                    repair_err = None
                    break
                except Exception as exc:
                    repair_err = f"{type(exc).__name__}: {exc}"

            if repair_err is not None or cls is None:
                raise RuntimeError(f"Committee round {r_idx} failed validation: {repair_err}")

            # Persist candidate version first so RunRecord links to exact immutable version_id
            ver_rec = self.version_store.submit_strategy_code(
                strategy_id=strategy_id,
                name=name,
                code=code,
                author=author,
                ts_ms=self.clock.now_ms() + r_idx,
                description=description or thesis.thesis_summary,
                params=validated_params,
                source_type=StrategySourceType.NL_GENERATED,
                generation_prompt=f"[Round {r_idx}/{rounds_cap}] {prompt}",
                backtest_gate_report={"committee_round": r_idx, "status": "EVALUATING"},
            )
            assert ver_rec.approval_status == ApprovalStatus.PENDING.value

            # Execute authentic historical BacktestEngine + full report persistence
            bt_clock = SimulatedClock(bars[0].open_time_ms if bars else self.clock.now_ms())
            bt_feed = HistoricalDataFeed(clock=bt_clock)
            bt_feed.register_symbol_spec(spec)
            if bars:
                bt_feed.load_bars(spec.exchange, spec.symbol, bars[0].interval, bars)
            if fundings:
                bt_feed.load_funding_rates(spec.exchange, spec.symbol, fundings)

            strat_cfg = StrategyConfig(
                strategy_id=strategy_id,
                strategy_version_id=ver_rec.version_id,
                name=name,
                exchanges=[spec.exchange],
                symbols=[spec.symbol],
                bar_interval=bars[0].interval if bars else "1h",
                params=validated_params,
                margin_mode=MarginMode.CROSS,
                default_leverage=leverage,
                initial_balances={spec.margin_asset: initial_usdt},
            )
            bt_engine = BacktestEngine(
                db_session=self.session,
                clock=bt_clock,
                data_feed=bt_feed,
                strategy_cls=cls,
                strategy_code=code,
                config=strat_cfg,
            )
            bt_res = bt_engine.run()
            assert bt_res.run_record.status == RunStatus.COMPLETED

            report = build_and_persist_backtest_report(
                db_session=self.session,
                result=bt_res,
                benchmark_bars=bars,
                periods_per_year=periods_per_year_for_interval(bars[0].interval if bars else "1h"),
            )

            critique = self.critic.audit_backtest_report(
                report=report,
                target_max_drawdown_pct=float(target_max_drawdown_pct),
                already_applied=active_mutations,
            )

            sm = report.get("summary_metrics", {})
            tm = report.get("trade_metrics", {})
            cb = report.get("cost_breakdown", {})

            r_record = CommitteeRoundRecord(
                round_index=r_idx,
                version_id=ver_rec.version_id,
                version=ver_rec.version,
                run_id=bt_res.run_record.run_id,
                mutation_tags=list(active_mutations) if active_mutations else ["BASELINE_THESIS"],
                coder_summary=coder_summary,
                cumulative_return=float(sm.get("cumulative_return", 0.0) or 0.0),
                annualized_return=float(sm.get("annualized_return", 0.0) or 0.0),
                max_drawdown=float(sm.get("max_drawdown", 0.0) or 0.0),
                sharpe=float(sm.get("sharpe", 0.0) or 0.0),
                sortino=float(sm.get("sortino", 0.0) or 0.0),
                win_rate=float(tm.get("win_rate", 0.0) or 0.0),
                payoff_ratio=float(tm.get("payoff_ratio", 0.0) or 0.0),
                closed_trade_count=int(tm.get("closed_trade_count", 0) or 0),
                turnover_rate=float(sm.get("turnover_rate", 0.0) or 0.0),
                total_fees_usdt=str(cb.get("total_fees", "0")),
                total_funding_usdt=str(cb.get("total_funding", "0")),
                composite_score=critique.composite_score,
                critic_verdict=critique.verdict,
                critic_findings=critique.findings,
                recommended_mutations=critique.recommended_mutations,
            )
            round_records.append(r_record)
            round_artifacts.append(
                {
                    "round": r_record,
                    "version_record": ver_rec,
                    "code": code,
                    "params": validated_params,
                }
            )

            prev_code = code
            prev_critique = critique
            for mut in critique.recommended_mutations:
                if mut not in active_mutations:
                    active_mutations.append(mut)

        # Pick champion round by highest risk-adjusted composite_score
        best_item = max(round_artifacts, key=lambda item: item["round"].composite_score)
        best_round: CommitteeRoundRecord = best_item["round"]
        champion_ver: StrategyVersionRecord = best_item["version_record"]

        # If an earlier round beat the final round, promote the champion code to latest version head
        if champion_ver.version_id != round_artifacts[-1]["version_record"].version_id:
            champion_ver = self.version_store.submit_strategy_code(
                strategy_id=strategy_id,
                name=name,
                code=best_item["code"],
                author=author,
                ts_ms=self.clock.now_ms() + rounds_cap + 2,
                description=description or thesis.thesis_summary,
                params=best_item["params"],
                source_type=StrategySourceType.NL_GENERATED,
                generation_prompt=f"[Champion Promoted from Round {best_round.round_index}] {prompt}",
                backtest_gate_report={"status": "PROMOTED_CHAMPION"},
            )

        llm_mode = "openai_compatible_hybrid" if any_external_llm else "deterministic_specialist"
        committee_summary_payload = {
            "passed": best_round.critic_verdict != "HIGH_RISK_WARNING",
            "mode": "multi_agent_committee",
            "llm_mode": llm_mode,
            "champion_round": best_round.round_index,
            "run_id": best_round.run_id,
            "total_trades": best_round.closed_trade_count,
            "cumulative_return_pct": f"{best_round.cumulative_return * 100:+.2f}%",
            "max_drawdown_pct": f"{abs(best_round.max_drawdown) * 100:.2f}%",
            "sharpe": round(best_round.sharpe, 3),
            "composite_score": best_round.composite_score,
            "market_profile": profile.model_dump(mode="json"),
            "director_thesis": thesis.model_dump(mode="json"),
            "rounds": [r.model_dump(mode="json") for r in round_records],
        }

        # Attach full committee audit summary onto all versions created in this session (especially champion head)
        for item in round_artifacts:
            v_obj: StrategyVersionRecord = item["version_record"]
            r_obj: CommitteeRoundRecord = item["round"]
            v_obj.backtest_gate_json = {
                **committee_summary_payload,
                "this_round_index": r_obj.round_index,
                "run_id": r_obj.run_id,
                "total_trades": r_obj.closed_trade_count,
                "cumulative_return_pct": f"{r_obj.cumulative_return * 100:+.2f}%",
                "max_drawdown_pct": f"{abs(r_obj.max_drawdown) * 100:.2f}%",
                "sharpe": round(r_obj.sharpe, 3),
                "composite_score": r_obj.composite_score,
            }
        champion_ver.backtest_gate_json = committee_summary_payload
        self.session.flush()

        return {
            "strategy_id": strategy_id,
            "champion_version": champion_ver,
            "champion_round": best_round.round_index,
            "champion_run_id": best_round.run_id,
            "llm_mode": llm_mode,
            "market_profile": profile,
            "director_thesis": thesis,
            "rounds": round_records,
        }
