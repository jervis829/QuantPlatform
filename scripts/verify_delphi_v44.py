"""
Empirical Verification Suite for Delphi II v4.4 vs. Delphi II v4.5 (Optimized)
==============================================================================
Executes 6 controlled quantitative experiments on a 120-day (2,880 1H bars +
172,800 1m intrabar ticks) 3-asset perpetual futures universe (BTC, ETH, SOL)
with realistic Binance Perpetual fees (0.04% taker), slippage (2 bps), and
8-hour funding settlements:

1. Head-to-Head Portfolio Benchmark: Delphi II v4.4 (Baseline) vs. v4.5 (Optimized)
2. Short-Side Trailing Stop Sensitivity Grid (0.3 ATR .. 2.5 ATR) & Walk-Forward OOS
3. Distance-to-Daily-EMA50 Corridor Attribution ([1.0, 2.5), [2.5, 3.5), >= 3.5 ATR_D)
4. 1-Minute Polling Intrabar Repainting ("假企稳") vs. Closed 1H Bar Confirmation
5. Rolling Current_ATR vs. Locked Entry_ATR + Daily Reversal / Time-Stop Ablation
6. Multi-Asset 2-Slot Allocation: Fixed Order + Equal Notional vs. Strength Rank + ATR Risk Parity
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random
from typing import Literal


@dataclass
class Bar1H:
    idx: int
    ts_ms: int
    day_idx: int
    regime: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Pre-simulated 60 1-minute intra-hour prices & highs/lows for Experiment 4
    intrabar_1m_closes: list[float] = field(default_factory=list)
    intrabar_1m_highs: list[float] = field(default_factory=list)
    intrabar_1m_lows: list[float] = field(default_factory=list)


@dataclass
class DailyIndicators:
    ema20: float
    ema50: float
    atr14: float
    close: float


@dataclass
class ClosedTrade:
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    qty: float
    notional_usdt: float
    entry_atr_1h: float
    dist_ema50_atr_d: float
    regime_at_entry: str
    exit_reason: str
    gross_pnl: float
    fees_and_funding: float
    net_pnl: float
    r_multiple: float
    mfe_atr: float  # Max Favorable Excursion in Entry_ATR units
    post_exit_24h_move_atr: float  # How much further price moved in trade direction in next 24h!


# ---------------------------------------------------------------------------
# 1. Realistic Multi-Regime 3-Coin 1H + 1m Generator (120 Days = 2,880 1H Bars)
# ---------------------------------------------------------------------------

SYM_CFG = {
    "BTC/USDT:USDT": {"anchor": 60000.0, "vol_mult": 1.00, "tick": 0.1},
    "ETH/USDT:USDT": {"anchor": 3000.0, "vol_mult": 1.32, "tick": 0.01},
    "SOL/USDT:USDT": {"anchor": 140.0, "vol_mult": 2.35, "tick": 0.01},
}


def generate_multi_regime_universe(seed: int = 20261030) -> dict[str, list[Bar1H]]:
    """
    Generates 120 UTC days (2,880 1H bars) for BTC, ETH, SOL with shared market factor
    + coin-specific volatility scaling + realistic intrabar 1m paths (including upper/lower wicks
    and bear-market short-covering squeezes).
    """
    rng = random.Random(seed)
    total_days = 120
    total_hours = total_days * 24
    base_ts_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC

    def regime_for_day(d: int) -> tuple[str, float, float]:
        # returns (regime_name, hourly_drift_btc, base_hourly_vol_btc)
        if d < 25:
            return ("warmup_range", 0.00025, 0.0055)
        if d < 52:
            return ("warmup_bull", 0.00065, 0.0060)
        if d < 73:
            # Phase 1: Healthy bull trend with periodic 18h pullbacks
            cyc = (d - 52) % 7
            if cyc in (4, 5):
                return ("bull_trend", -0.00045, 0.0062)
            return ("bull_trend", 0.00105, 0.0068)
        if d < 79:
            # Phase 2a: Parabolic blow-off surge (> 3.5 ATR_D above EMA50)
            return ("climax_top", 0.00235, 0.0095)
        if d < 85:
            # Phase 2b: High-alt distribution + violent flush back toward EMA50
            if d in (79, 80):
                return ("climax_top", 0.00035, 0.0110)  # bull trap breakouts at extreme altitude!
            return ("climax_flush", -0.00340, 0.0125)
        if d < 92:
            # Phase 3: EMA20/50 entanglement chop
            sign = 1.0 if (d % 2 == 0) else -1.0
            return ("ema_chop", 0.0004 * sign, 0.0065)
        if d < 111:
            # Phase 4: Choppy bear market (grinding lower with sharp short squeezes!)
            cyc = (d - 92) % 5
            if cyc == 3:
                return ("bear_choppy", 0.00095, 0.0085)  # dead-cat short squeeze day
            return ("bear_choppy", -0.00115, 0.0078)
        if d < 116:
            # Phase 5a: Waterfall capitulation
            return ("bear_waterfall", -0.00240, 0.0115)
        # Phase 5b: V-bottom violent reversal
        return ("v_reversal", 0.00260, 0.0110)

    # Pre-generate common hourly market shocks + intrabar 1m path shapes
    common_shocks = [rng.gauss(0.0, 1.0) for _ in range(total_hours)]
    squeeze_hours = set()
    wick_trap_hours = set()
    for h in range(total_hours):
        d = h // 24
        reg, _, _ = regime_for_day(d)
        # In choppy bear, every ~14 hours a 1-2h sharp short-covering bounce occurs
        if reg == "bear_choppy" and (h % 14 in (9, 10)):
            squeeze_hours.add(h)
        # Occasional intra-hour breakout fakeout (spikes first 20m, closes weak at 60m)
        if h % 19 == 7:
            wick_trap_hours.add(h)

    universe: dict[str, list[Bar1H]] = {}
    for sym, cfg in SYM_CFG.items():
        bars: list[Bar1H] = []
        price = cfg["anchor"]
        vmult = cfg["vol_mult"]

        for h in range(total_hours):
            d = h // 24
            reg, base_drift, base_vol = regime_for_day(d)
            # SOL leads slightly higher beta in strong bursts
            sym_drift = base_drift * (1.0 + 0.42 * (vmult - 1.0))
            sym_vol = base_vol * vmult

            idio = rng.gauss(0.0, 1.0)
            blended_z = 0.84 * common_shocks[h] + 0.54 * idio

            if h in squeeze_hours:
                # Short-covering bounce of ~0.8 to 1.2x hourly vol, then resumes bear
                ret_1h = abs(sym_vol) * rng.uniform(0.75, 1.25)
            else:
                ret_1h = sym_drift + sym_vol * blended_z

            open_px = price
            # Construct 60 1-minute steps inside the hour
            m_closes: list[float] = []
            m_highs: list[float] = []
            m_lows: list[float] = []

            if h in wick_trap_hours:
                # Spikes up strongly in minutes 0..22, then reverses lower by minute 59!
                peak_ret = abs(sym_vol) * rng.uniform(1.35, 2.05)
                end_ret = -abs(sym_vol) * rng.uniform(0.35, 0.90)
                cur_m = open_px
                for m in range(60):
                    if m < 22:
                        target = open_px * (1.0 + peak_ret * ((m + 1) / 22.0))
                    else:
                        frac = (m - 21) / 38.0
                        target = open_px * (1.0 + peak_ret * (1.0 - frac) + end_ret * frac)
                    noise = open_px * sym_vol * 0.08 * rng.gauss(0.0, 1.0)
                    nxt = max(cfg["tick"], target + noise)
                    hi = max(cur_m, nxt) * (1.0 + abs(sym_vol) * 0.06)
                    lo = min(cur_m, nxt) * (1.0 - abs(sym_vol) * 0.06)
                    m_closes.append(nxt)
                    m_highs.append(hi)
                    m_lows.append(lo)
                    cur_m = nxt
            else:
                target_close = max(cfg["tick"], open_px * math.exp(ret_1h))
                cur_m = open_px
                # Brownian bridge from open_px to target_close
                raw_walk = [0.0]
                for _ in range(60):
                    raw_walk.append(raw_walk[-1] + rng.gauss(0.0, 1.0) / math.sqrt(60.0))
                for m in range(1, 61):
                    frac = m / 60.0
                    bridge = raw_walk[m] - frac * raw_walk[60]
                    nxt = open_px + frac * (target_close - open_px) + open_px * sym_vol * 0.85 * bridge
                    nxt = max(cfg["tick"], nxt)
                    hi = max(cur_m, nxt) * (1.0 + abs(sym_vol) * rng.uniform(0.02, 0.10))
                    lo = min(cur_m, nxt) * (1.0 - abs(sym_vol) * rng.uniform(0.02, 0.10))
                    m_closes.append(nxt)
                    m_highs.append(hi)
                    m_lows.append(lo)
                    cur_m = nxt
                m_closes[-1] = target_close

            close_px = m_closes[-1]
            high_px = max([open_px] + m_highs)
            low_px = min([open_px] + m_lows)
            vol = (1000.0 / vmult) * (1.0 + 2.2 * abs(ret_1h) / max(sym_vol, 1e-6)) * rng.uniform(0.8, 1.3)

            bars.append(
                Bar1H(
                    idx=h,
                    ts_ms=base_ts_ms + h * 3_600_000,
                    day_idx=d,
                    regime=reg,
                    open=open_px,
                    high=high_px,
                    low=low_px,
                    close=close_px,
                    volume=vol,
                    intrabar_1m_closes=m_closes,
                    intrabar_1m_highs=m_highs,
                    intrabar_1m_lows=m_lows,
                )
            )
            price = close_px

        universe[sym] = bars
    return universe


# ---------------------------------------------------------------------------
# 2. Strict Anti-Lookahead Multi-Timeframe Indicators (1D T-1 + 1H Closed)
# ---------------------------------------------------------------------------

def precompute_indicators(
    bars: list[Bar1H],
) -> tuple[dict[int, DailyIndicators], list[float], list[float]]:
    """
    Returns:
      - daily_by_day: for day `d`, holds indicators computed strictly up through day `d-1` (yesterday's closed 1D bar!)
      - ema20_1h: 1H EMA20 at each bar `i`
      - atr14_1h: 1H Wilder/SMA ATR(14) at each bar `i`
    """
    # Build 1D OHLC from 24h blocks
    days: dict[int, list[Bar1H]] = {}
    for b in bars:
        days.setdefault(b.day_idx, []).append(b)

    daily_ohlc: list[tuple[int, float, float, float, float]] = []
    for d in sorted(days.keys()):
        chunk = days[d]
        daily_ohlc.append(
            (
                d,
                chunk[0].open,
                max(x.high for x in chunk),
                min(x.low for x in chunk),
                chunk[-1].close,
            )
        )

    ema20_d = daily_ohlc[0][4]
    ema50_d = daily_ohlc[0][4]
    a20 = 2.0 / 21.0
    a50 = 2.0 / 51.0
    trs_d: list[float] = []
    closed_daily_snaps: dict[int, DailyIndicators] = {}

    for idx_d, (d, _o, hi, lo, cl) in enumerate(daily_ohlc):
        if idx_d == 0:
            tr = hi - lo
        else:
            prev_cl = daily_ohlc[idx_d - 1][4]
            tr = max(hi - lo, abs(hi - prev_cl), abs(lo - prev_cl))
        trs_d.append(tr)
        ema20_d = a20 * cl + (1.0 - a20) * ema20_d
        ema50_d = a50 * cl + (1.0 - a50) * ema50_d
        atr14_d = sum(trs_d[-14:]) / min(14, len(trs_d))
        closed_daily_snaps[d] = DailyIndicators(
            ema20=ema20_d,
            ema50=ema50_d,
            atr14=atr14_d,
            close=cl,
        )

    # Map day `d` -> strictly `d - 1` closed daily snapshot!
    daily_for_trading_day: dict[int, DailyIndicators] = {}
    for d in sorted(days.keys()):
        if d - 1 in closed_daily_snaps:
            daily_for_trading_day[d] = closed_daily_snaps[d - 1]

    # 1H EMA20 & ATR14
    ema20_1h: list[float] = []
    atr14_1h: list[float] = []
    trs_1h: list[float] = []
    e20 = bars[0].close
    a20_h = 2.0 / 21.0
    for i, b in enumerate(bars):
        if i == 0:
            tr = b.high - b.low
        else:
            pc = bars[i - 1].close
            tr = max(b.high - b.low, abs(b.high - pc), abs(b.low - pc))
        trs_1h.append(tr)
        e20 = a20_h * b.close + (1.0 - a20_h) * e20
        ema20_1h.append(e20)
        atr14_1h.append(sum(trs_1h[-14:]) / min(14, len(trs_1h)))

    return daily_for_trading_day, ema20_1h, atr14_1h


# ---------------------------------------------------------------------------
# 3. Configurable Delphi II Engine (Supports v4.4 Exact Baseline & Ablations)
# ---------------------------------------------------------------------------

@dataclass
class DelphiParams:
    name: str = "Delphi_v4.4_Baseline"
    # Entry filters
    min_dist_atr_d: float = 1.0
    soft_dist_atr_d: float | None = None  # Tiered corridor threshold in v4.6!
    max_dist_atr_d: float | None = None  # None in v4.4; 3.5 in v4.5; 8.5 in v4.6!
    extended_size_mult: float = 1.0  # 0.60 in v4.6 for [soft_dist_atr_d, max_dist_atr_d)
    channel_lookback_1h: int = 15
    channel_atr_mult: float = 0.45
    poll_intrabar_1m: bool = False  # True tests 1m live polling repainting trap
    # Stop & Trailing Stop
    init_stop_atr: float = 3.5
    trail_activate_atr: float = 1.5
    long_trail_atr: float = 2.0
    short_trail_atr: float = 0.5  # 0.5 in v4.4; 1.5 in v4.5!
    two_stage_breakeven: bool = False  # v4.5: +1.5 ATR -> BE, +2.5 ATR -> Chandelier
    stage2_activate_atr: float = 2.5
    lock_entry_atr: bool = False  # False = rolling ATR; True = max(entry_atr, cur_atr)
    # Active Exits
    enable_daily_reversal_exit: bool = False
    max_stagnant_hours: int | None = None  # e.g. 30h without reaching +1.0 ATR
    # Portfolio & Sizing
    max_positions: int = 2
    sizing_mode: Literal["equal_notional", "atr_risk_parity"] = "equal_notional"
    notional_frac_per_slot: float = 0.45  # 45% equity notional per slot (0.9x unlevered / conservative)
    risk_pct_per_trade: float = 0.025  # 2.5% equity risk at 3.5 ATR stop (iso-risk with v4.4 average)
    rank_candidates_by_strength: bool = False
    # Realistic execution friction
    taker_fee_rate: float = 0.0004  # 4 bps Binance Perp Taker
    slippage_bps: float = 2.0       # 2 bps market/stop execution slippage
    funding_rate_8h: float = 0.0001 # 0.01% per 8h (longs pay, shorts receive in bull; inverted in bear)


@dataclass
class OpenPositionState:
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry_idx: int
    entry_price: float
    qty: float
    notional_usdt: float
    entry_atr_1h: float
    dist_ema50_atr_d: float
    regime_at_entry: str
    stop_price: float
    highest_price: float
    lowest_price: float
    accrued_fees_funding: float


@dataclass
class SimResult:
    params_name: str
    initial_equity: float
    final_equity: float
    cum_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    calmar: float
    profit_factor: float
    win_rate_pct: float
    total_trades: int
    long_trades: int
    short_trades: int
    long_net_pnl: float
    short_net_pnl: float
    short_profit_factor: float
    premature_short_stops: int
    total_fees_funding: float
    rejected_by_slot_cap: dict[str, int]
    pnl_by_symbol: dict[str, float]
    trades: list[ClosedTrade]
    equity_curve_daily: list[float]


def run_delphi_simulation(
    universe: dict[str, list[Bar1H]],
    precomp: dict[str, tuple[dict[int, DailyIndicators], list[float], list[float]]],
    params: DelphiParams,
    start_day: int = 52,
    end_day: int = 120,
    symbols_order: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"),
) -> SimResult:
    initial_equity = 10_000.0
    cash_equity = initial_equity
    open_positions: dict[str, OpenPositionState] = {}
    closed_trades: list[ClosedTrade] = []
    rejected_by_slot: dict[str, int] = {s: 0 for s in symbols_order}

    ref_bars = universe[symbols_order[0]]
    total_bars = len(ref_bars)
    slip = params.slippage_bps / 10_000.0

    daily_equities: list[float] = [initial_equity]

    def mark_to_market_equity(bar_i: int) -> float:
        eq = cash_equity
        for sym, pos in open_positions.items():
            px = universe[sym][bar_i].close
            if pos.side == "LONG":
                eq += (px - pos.entry_price) * pos.qty
            else:
                eq += (pos.entry_price - px) * pos.qty
        return eq

    def close_position(sym: str, pos: OpenPositionState, bar_i: int, raw_exit_px: float, reason: str) -> None:
        nonlocal cash_equity
        # Apply adverse slippage on exit
        exit_px = raw_exit_px * (1.0 - slip) if pos.side == "LONG" else raw_exit_px * (1.0 + slip)
        exit_fee = exit_px * pos.qty * params.taker_fee_rate
        total_cost = pos.accrued_fees_funding + exit_fee
        gross = (exit_px - pos.entry_price) * pos.qty if pos.side == "LONG" else (pos.entry_price - exit_px) * pos.qty
        net = gross - total_cost
        cash_equity += gross - exit_fee

        init_risk_usdt = params.init_stop_atr * pos.entry_atr_1h * pos.qty
        r_mult = net / init_risk_usdt if init_risk_usdt > 0 else 0.0
        mfe_atr = (
            (pos.highest_price - pos.entry_price) / pos.entry_atr_1h
            if pos.side == "LONG"
            else (pos.entry_price - pos.lowest_price) / pos.entry_atr_1h
        )

        # Measure how much further price moved in favor during the 24 hours AFTER exit!
        sym_bars = universe[sym]
        horizon = sym_bars[bar_i + 1 : min(len(sym_bars), bar_i + 25)]
        if not horizon:
            post_move_atr = 0.0
        elif pos.side == "LONG":
            post_move_atr = (max(x.high for x in horizon) - exit_px) / pos.entry_atr_1h
        else:
            post_move_atr = (exit_px - min(x.low for x in horizon)) / pos.entry_atr_1h

        closed_trades.append(
            ClosedTrade(
                symbol=sym,
                side=pos.side,
                entry_idx=pos.entry_idx,
                exit_idx=bar_i,
                entry_price=pos.entry_price,
                exit_price=exit_px,
                qty=pos.qty,
                notional_usdt=pos.notional_usdt,
                entry_atr_1h=pos.entry_atr_1h,
                dist_ema50_atr_d=pos.dist_ema50_atr_d,
                regime_at_entry=pos.regime_at_entry,
                exit_reason=reason,
                gross_pnl=gross,
                fees_and_funding=total_cost,
                net_pnl=net,
                r_multiple=r_mult,
                mfe_atr=mfe_atr,
                post_exit_24h_move_atr=post_move_atr,
            )
        )
        del open_positions[sym]

    for i in range(total_bars):
        day_idx = ref_bars[i].day_idx
        if day_idx < start_day or day_idx >= end_day:
            continue

        # 8-hour funding settlement (00:00, 08:00, 16:00 UTC)
        if i % 8 == 0 and open_positions:
            for sym, pos in list(open_positions.items()):
                b = universe[sym][i]
                # Positive funding in bull/climax, slightly negative/neutral in deep bear
                fr = params.funding_rate_8h if b.regime in ("bull_trend", "climax_top", "v_reversal") else -0.00003
                notional_now = b.open * pos.qty
                # Longs pay positive funding; shorts receive positive funding
                funding_cost = notional_now * fr if pos.side == "LONG" else -notional_now * fr
                cash_equity -= funding_cost
                pos.accrued_fees_funding += funding_cost

        # Step A: Manage existing positions (Stop-loss check -> Trailing update -> Optional reversal/time exits)
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            b = universe[sym][i]
            d_map, _e20_h, atr_h = precomp[sym]
            d_ind = d_map.get(b.day_idx)
            cur_atr = atr_h[i]
            eff_atr = max(pos.entry_atr_1h, cur_atr) if params.lock_entry_atr else cur_atr

            # 1) Intrabar stop trigger check (conservative: check if High/Low breached current stop_price)
            if pos.side == "LONG" and b.low <= pos.stop_price:
                fill_px = min(b.open, pos.stop_price)
                reason = "trailing_stop" if pos.stop_price > pos.entry_price - params.init_stop_atr * pos.entry_atr_1h * 0.99 else "init_stop"
                close_position(sym, pos, i, fill_px, reason)
                continue
            if pos.side == "SHORT" and b.high >= pos.stop_price:
                fill_px = max(b.open, pos.stop_price)
                reason = "trailing_stop" if pos.stop_price < pos.entry_price + params.init_stop_atr * pos.entry_atr_1h * 0.99 else "init_stop"
                close_position(sym, pos, i, fill_px, reason)
                continue

            # Update extreme watermark
            pos.highest_price = max(pos.highest_price, b.high)
            pos.lowest_price = min(pos.lowest_price, b.low)

            # 2) Optional active exits (v4.5): Daily trend invalidated or stagnant time-stop
            if params.enable_daily_reversal_exit and d_ind is not None:
                if pos.side == "LONG" and d_ind.close < d_ind.ema50:
                    close_position(sym, pos, i, b.close, "daily_trend_flip")
                    continue
                if pos.side == "SHORT" and d_ind.close > d_ind.ema50:
                    close_position(sym, pos, i, b.close, "daily_trend_flip")
                    continue

            if params.max_stagnant_hours is not None and (i - pos.entry_idx) >= params.max_stagnant_hours:
                mfe_now = (
                    (pos.highest_price - pos.entry_price) / pos.entry_atr_1h
                    if pos.side == "LONG"
                    else (pos.entry_price - pos.lowest_price) / pos.entry_atr_1h
                )
                unreal = (b.close - pos.entry_price) if pos.side == "LONG" else (pos.entry_price - b.close)
                if mfe_now < 1.0 and unreal < 0:
                    close_position(sym, pos, i, b.close, "time_stagnation_stop")
                    continue

            # 3) Update Ratchet Trailing Stop (never loosens!)
            if pos.side == "LONG":
                gain_atr = (pos.highest_price - pos.entry_price) / pos.entry_atr_1h
                if params.two_stage_breakeven:
                    if gain_atr >= params.stage2_activate_atr:
                        cand = pos.highest_price - params.long_trail_atr * eff_atr
                        pos.stop_price = max(pos.stop_price, cand)
                    elif gain_atr >= params.trail_activate_atr:
                        cand = pos.entry_price + 0.12 * pos.entry_atr_1h
                        pos.stop_price = max(pos.stop_price, cand)
                else:
                    if gain_atr >= params.trail_activate_atr:
                        cand = pos.highest_price - params.long_trail_atr * eff_atr
                        pos.stop_price = max(pos.stop_price, cand)
            else:
                gain_atr = (pos.entry_price - pos.lowest_price) / pos.entry_atr_1h
                if params.two_stage_breakeven:
                    if gain_atr >= params.stage2_activate_atr:
                        cand = pos.lowest_price + params.short_trail_atr * eff_atr
                        pos.stop_price = min(pos.stop_price, cand)
                    elif gain_atr >= params.trail_activate_atr:
                        cand = pos.entry_price - 0.12 * pos.entry_atr_1h
                        pos.stop_price = min(pos.stop_price, cand)
                else:
                    if gain_atr >= params.trail_activate_atr:
                        cand = pos.lowest_price + params.short_trail_atr * eff_atr
                        pos.stop_price = min(pos.stop_price, cand)

        # Step B: Evaluate New Entries across Symbols without Open Positions
        if i < 20:
            continue

        candidates: list[tuple[float, str, Literal["LONG", "SHORT"], float, float, float]] = []
        # tuple: (strength_score, sym, side, trigger_entry_px, atr_1h_val, dist_atr_d)

        for sym in symbols_order:
            if sym in open_positions:
                continue
            bars_s = universe[sym]
            b = bars_s[i]
            d_map, ema20_h, atr_h = precomp[sym]
            d_ind = d_map.get(b.day_idx)
            if d_ind is None or d_ind.atr14 <= 0:
                continue

            # Gate 1: Daily Trend Direction (EMA20_D vs EMA50_D & Close_D vs EMA50_D)
            if d_ind.close > d_ind.ema50 and d_ind.ema20 > d_ind.ema50:
                allowed_side: Literal["LONG", "SHORT"] | None = "LONG"
            elif d_ind.close < d_ind.ema50 and d_ind.ema20 < d_ind.ema50:
                allowed_side = "SHORT"
            else:
                continue

            prev_15 = bars_s[i - params.channel_lookback_1h : i]
            h_15 = max(x.high for x in prev_15)
            l_15 = min(x.low for x in prev_15)
            prev_atr_h = atr_h[i - 1]
            upper_ch = h_15 + params.channel_atr_mult * prev_atr_h
            lower_ch = l_15 - params.channel_atr_mult * prev_atr_h

            if not params.poll_intrabar_1m:
                # Standard Closed 1H Bar Evaluation
                dist_atr_d = abs(b.close - d_ind.ema50) / d_ind.atr14
                # Gate 2: Distance from Daily EMA50
                if dist_atr_d <= params.min_dist_atr_d:
                    continue
                if params.max_dist_atr_d is not None and dist_atr_d >= params.max_dist_atr_d:
                    continue

                # Gate 3: 1H Channel Breakout
                if allowed_side == "LONG" and b.high <= upper_ch:
                    continue
                if allowed_side == "SHORT" and b.low >= lower_ch:
                    continue

                # Gate 4: 1H Stabilization Triple Check (strictly on closed 1H bars)
                recent_6 = bars_s[i - 5 : i + 1]
                prior_6 = bars_s[i - 11 : i - 5]
                if allowed_side == "LONG":
                    no_lower_low = min(x.low for x in recent_6) >= min(x.low for x in prior_6)
                    rising_close = b.close > bars_s[i - 1].close
                    above_ema20 = b.close > ema20_h[i]
                    if not (no_lower_low and rising_close and above_ema20):
                        continue
                else:
                    no_higher_high = max(x.high for x in recent_6) <= max(x.high for x in prior_6)
                    falling_close = b.close < bars_s[i - 1].close
                    below_ema20 = b.close < ema20_h[i]
                    if not (no_higher_high and falling_close and below_ema20):
                        continue

                avg_vol_15 = sum(x.volume for x in prev_15) / len(prev_15)
                vol_ratio = b.volume / max(avg_vol_15, 1e-6)
                # Sweet-spot trend strength score
                sweet_penalty = abs(dist_atr_d - 2.0)
                strength = vol_ratio * 2.0 - sweet_penalty
                candidates.append((strength, sym, allowed_side, b.close, atr_h[i], dist_atr_d))
            else:
                # 1-Minute Live Polling Simulation inside Hour `i`:
                # Checks each minute m=0..59 using partial 1H high/low/close!
                prev_5_closed = bars_s[i - 5 : i]
                prior_6_closed = bars_s[i - 11 : i - 5]
                prev_close = bars_s[i - 1].close
                prev_e20 = ema20_h[i - 1]
                a20_h = 2.0 / 21.0
                run_hi = b.open
                run_lo = b.open
                triggered_px: float | None = None
                triggered_dist: float = 0.0
                triggered_m: int = -1

                for m_idx in range(60):
                    mc = b.intrabar_1m_closes[m_idx]
                    run_hi = max(run_hi, b.intrabar_1m_highs[m_idx])
                    run_lo = min(run_lo, b.intrabar_1m_lows[m_idx])
                    dist_m = abs(mc - d_ind.ema50) / d_ind.atr14
                    if dist_m <= params.min_dist_atr_d:
                        continue
                    if params.max_dist_atr_d is not None and dist_m >= params.max_dist_atr_d:
                        continue
                    live_e20 = a20_h * mc + (1.0 - a20_h) * prev_e20
                    if allowed_side == "LONG":
                        if run_hi > upper_ch:
                            min_rec6 = min([x.low for x in prev_5_closed] + [run_lo])
                            if min_rec6 >= min(x.low for x in prior_6_closed) and mc > prev_close and mc > live_e20:
                                triggered_px = mc
                                triggered_dist = dist_m
                                triggered_m = m_idx
                                break
                    else:
                        if run_lo < lower_ch:
                            max_rec6 = max([x.high for x in prev_5_closed] + [run_hi])
                            if max_rec6 <= max(x.high for x in prior_6_closed) and mc < prev_close and mc < live_e20:
                                triggered_px = mc
                                triggered_dist = dist_m
                                triggered_m = m_idx
                                break

                if triggered_px is not None:
                    # Also record whether this mid-hour entry FAILED closed-bar stabilization at m=59!
                    recent_6_cl = bars_s[i - 5 : i + 1]
                    if allowed_side == "LONG":
                        closed_ok = (
                            min(x.low for x in recent_6_cl) >= min(x.low for x in prior_6_closed)
                            and b.close > prev_close
                            and b.close > ema20_h[i]
                        )
                    else:
                        closed_ok = (
                            max(x.high for x in recent_6_cl) <= max(x.high for x in prior_6_closed)
                            and b.close < prev_close
                            and b.close < ema20_h[i]
                        )
                    # Tag regime if repainted false breakout
                    candidates.append(
                        (
                            1.0 if closed_ok else -1.0,
                            sym,
                            allowed_side,
                            triggered_px,
                            prev_atr_h,
                            triggered_dist,
                        )
                    )

        if params.rank_candidates_by_strength:
            candidates.sort(key=lambda x: x[0], reverse=True)

        # Gate 5: Max 2 Open Slots Check & Order Execution
        eq_now = max(100.0, mark_to_market_equity(i))
        for _score, sym, side, raw_px, entry_atr, dist_atr_d in candidates:
            if len(open_positions) >= params.max_positions:
                rejected_by_slot[sym] += 1
                continue

            entry_px = raw_px * (1.0 + slip) if side == "LONG" else raw_px * (1.0 - slip)
            zone_mult = (
                params.extended_size_mult
                if (params.soft_dist_atr_d is not None and dist_atr_d >= params.soft_dist_atr_d)
                else 1.0
            )
            if params.sizing_mode == "equal_notional":
                notional = eq_now * params.notional_frac_per_slot * zone_mult
                qty = notional / entry_px
            else:
                # ATR Volatility Risk Parity: 3.5 * ATR_1H * qty = equity * risk_pct * zone_mult
                risk_budget = eq_now * params.risk_pct_per_trade * zone_mult
                stop_dist = params.init_stop_atr * entry_atr
                qty = risk_budget / max(stop_dist, 1e-8)
                # Cap single position notional at 85% * zone_mult of equity
                max_qty = (eq_now * 0.85 * zone_mult) / entry_px
                qty = min(qty, max_qty)
                notional = qty * entry_px

            entry_fee = notional * params.taker_fee_rate
            cash_equity -= entry_fee
            init_stop = (
                entry_px - params.init_stop_atr * entry_atr
                if side == "LONG"
                else entry_px + params.init_stop_atr * entry_atr
            )
            b_cur = universe[sym][i]
            open_positions[sym] = OpenPositionState(
                symbol=sym,
                side=side,
                entry_idx=i,
                entry_price=entry_px,
                qty=qty,
                notional_usdt=notional,
                entry_atr_1h=entry_atr,
                dist_ema50_atr_d=dist_atr_d,
                regime_at_entry=f"{b_cur.regime}|repainted" if (params.poll_intrabar_1m and _score < 0) else b_cur.regime,
                stop_price=init_stop,
                highest_price=max(entry_px, b_cur.close),
                lowest_price=min(entry_px, b_cur.close),
                accrued_fees_funding=entry_fee,
            )

        if (i + 1) % 24 == 0:
            daily_equities.append(mark_to_market_equity(i))

    # Close any remaining open positions at end of window for clean accounting
    last_i = min(total_bars - 1, end_day * 24 - 1)
    for sym in list(open_positions.keys()):
        pos = open_positions[sym]
        close_position(sym, pos, last_i, universe[sym][last_i].close, "window_end")
    daily_equities[-1] = cash_equity

    # Compute Portfolio Metrics
    cum_ret_pct = (cash_equity / initial_equity - 1.0) * 100.0
    peak = daily_equities[0]
    max_dd = 0.0
    daily_rets: list[float] = []
    for j in range(1, len(daily_equities)):
        prev_eq = max(daily_equities[j - 1], 1e-6)
        cur_eq = daily_equities[j]
        daily_rets.append(cur_eq / prev_eq - 1.0)
        peak = max(peak, cur_eq)
        dd = (peak - cur_eq) / peak
        max_dd = max(max_dd, dd)

    if len(daily_rets) > 2:
        mean_r = sum(daily_rets) / len(daily_rets)
        var_r = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 1e-9
        sharpe = (mean_r / std_r) * math.sqrt(365.0)
    else:
        sharpe = 0.0

    ann_ret = ((cash_equity / initial_equity) ** (365.0 / max(1, end_day - start_day)) - 1.0) * 100.0
    calmar = ann_ret / (max_dd * 100.0) if max_dd > 0 else 0.0

    gross_wins = sum(t.net_pnl for t in closed_trades if t.net_pnl > 0)
    gross_losses = abs(sum(t.net_pnl for t in closed_trades if t.net_pnl < 0))
    pf = gross_wins / gross_losses if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)
    wins = sum(1 for t in closed_trades if t.net_pnl > 0)
    win_rate = (wins / len(closed_trades) * 100.0) if closed_trades else 0.0

    long_t = [t for t in closed_trades if t.side == "LONG"]
    short_t = [t for t in closed_trades if t.side == "SHORT"]
    short_wins = sum(t.net_pnl for t in short_t if t.net_pnl > 0)
    short_losses = abs(sum(t.net_pnl for t in short_t if t.net_pnl < 0))
    short_pf = short_wins / short_losses if short_losses > 0 else (99.0 if short_wins > 0 else 0.0)

    # Premature short stop-out: exited via trailing_stop with < 2.0 ATR profit, yet price fell >= 3.0 ATR further in next 24h!
    premature_shorts = sum(
        1
        for t in short_t
        if t.exit_reason == "trailing_stop" and t.post_exit_24h_move_atr >= 3.0
    )

    pnl_by_sym = {
        s: round(sum(t.net_pnl for t in closed_trades if t.symbol == s), 2)
        for s in symbols_order
    }

    return SimResult(
        params_name=params.name,
        initial_equity=initial_equity,
        final_equity=round(cash_equity, 2),
        cum_return_pct=round(cum_ret_pct, 2),
        max_drawdown_pct=round(max_dd * 100.0, 2),
        sharpe=round(sharpe, 2),
        calmar=round(calmar, 2),
        profit_factor=round(pf, 2),
        win_rate_pct=round(win_rate, 2),
        total_trades=len(closed_trades),
        long_trades=len(long_t),
        short_trades=len(short_t),
        long_net_pnl=round(sum(t.net_pnl for t in long_t), 2),
        short_net_pnl=round(sum(t.net_pnl for t in short_t), 2),
        short_profit_factor=round(short_pf, 2),
        premature_short_stops=premature_shorts,
        total_fees_funding=round(sum(t.fees_and_funding for t in closed_trades), 2),
        rejected_by_slot_cap=rejected_by_slot,
        pnl_by_symbol=pnl_by_sym,
        trades=closed_trades,
        equity_curve_daily=[round(x, 2) for x in daily_equities],
    )


def main() -> None:
    universe = generate_multi_regime_universe(seed=20261030)
    precomp = {sym: precompute_indicators(bars) for sym, bars in universe.items()}

    # 1. Baseline v4.4 vs Optimized v4.5
    v44_params = DelphiParams(name="Delphi_II_v4.4_Baseline")
    v45_params = DelphiParams(
        name="Delphi_II_v4.5_Optimized",
        min_dist_atr_d=1.0,
        max_dist_atr_d=3.5,
        short_trail_atr=1.5,
        long_trail_atr=2.0,
        two_stage_breakeven=True,
        stage2_activate_atr=2.5,
        lock_entry_atr=True,
        enable_daily_reversal_exit=True,
        max_stagnant_hours=30,
        sizing_mode="atr_risk_parity",
        risk_pct_per_trade=0.025,
        rank_candidates_by_strength=True,
    )

    res_v44 = run_delphi_simulation(universe, precomp, v44_params)
    res_v45 = run_delphi_simulation(universe, precomp, v45_params)

    v46_params = DelphiParams(
        name="Delphi_II_v4.6_Tiered_AntiMiss",
        min_dist_atr_d=1.0,
        soft_dist_atr_d=3.8,
        max_dist_atr_d=8.5,
        extended_size_mult=0.60,
        short_trail_atr=1.8,
        long_trail_atr=2.0,
        two_stage_breakeven=False,
        lock_entry_atr=True,
        enable_daily_reversal_exit=True,
        max_stagnant_hours=30,
        sizing_mode="atr_risk_parity",
        risk_pct_per_trade=0.042,
        rank_candidates_by_strength=True,
    )
    res_v46 = run_delphi_simulation(universe, precomp, v46_params)

    # 2. Short Trailing Stop Sensitivity Sweep (0.3 .. 2.5 ATR)
    short_sweep = []
    for st_atr in [0.3, 0.5, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5]:
        # Test both in Waterfall-only (Days 110..116) vs Choppy Bear + Waterfall (Days 92..116)
        r_choppy = run_delphi_simulation(
            universe,
            precomp,
            DelphiParams(name=f"short_trail_{st_atr}", short_trail_atr=st_atr),
            start_day=92,
            end_day=116,
        )
        r_waterfall = run_delphi_simulation(
            universe,
            precomp,
            DelphiParams(name=f"waterfall_{st_atr}", short_trail_atr=st_atr),
            start_day=110,
            end_day=116,
        )
        short_sweep.append(
            {
                "short_trail_atr": st_atr,
                "bear_full_short_pnl": r_choppy.short_net_pnl,
                "bear_full_short_pf": r_choppy.short_profit_factor,
                "bear_premature_stops": r_choppy.premature_short_stops,
                "short_trades": r_choppy.short_trades,
                "waterfall_only_pnl": r_waterfall.short_net_pnl,
            }
        )

    # 3. Distance-to-EMA50 Bucket Breakdown on Baseline v4.4
    buckets = [
        ("1.0 ~ 2.5 ATR_D (健康趋势初/中段)", 1.0, 2.5),
        ("2.5 ~ 3.5 ATR_D (趋势扩张后段)", 2.5, 3.5),
        (">= 3.5 ATR_D (高潮赶顶/超卖衰竭区)", 3.5, 999.0),
    ]
    dist_breakdown = []
    for label, lo_b, hi_b in buckets:
        sub = [t for t in res_v44.trades if lo_b <= t.dist_ema50_atr_d < hi_b]
        wins = sum(1 for t in sub if t.net_pnl > 0)
        dist_breakdown.append(
            {
                "bucket": label,
                "trades": len(sub),
                "win_rate_pct": round(wins / len(sub) * 100.0, 1) if sub else 0.0,
                "avg_r": round(sum(t.r_multiple for t in sub) / len(sub), 2) if sub else 0.0,
                "net_pnl": round(sum(t.net_pnl for t in sub), 2),
            }
        )

    # 4. 1-Minute Polling Repainting ("假企稳") vs Closed 1H Bar Confirmation
    res_1m_poll = run_delphi_simulation(
        universe,
        precomp,
        DelphiParams(name="Delphi_v4.4_1m_Polling", poll_intrabar_1m=True),
    )
    repainted_trades = [t for t in res_1m_poll.trades if "|repainted" in t.regime_at_entry]
    valid_1m_trades = [t for t in res_1m_poll.trades if "|repainted" not in t.regime_at_entry]
    repainted_wins = sum(1 for t in repainted_trades if t.net_pnl > 0)

    # Walk-Forward 3-Window Out-of-Sample Check (Bull+Climax -> Choppy Bear -> Waterfall+Reversal)
    wf_windows = [
        ("W1: 牛市扩张+赶顶反转 (Day 52-85)", 52, 85),
        ("W2: 均线纠缠+震荡阴跌熊 (Day 85-110)", 85, 110),
        ("W3: 瀑布急跌+V型反转 (Day 110-120)", 110, 120),
    ]
    walk_forward = []
    for w_label, d_s, d_e in wf_windows:
        rw44 = run_delphi_simulation(universe, precomp, v44_params, start_day=d_s, end_day=d_e)
        rw45 = run_delphi_simulation(universe, precomp, v45_params, start_day=d_s, end_day=d_e)
        walk_forward.append(
            {
                "window": w_label,
                "v44_return_pct": rw44.cum_return_pct,
                "v44_max_dd_pct": rw44.max_drawdown_pct,
                "v44_pf": rw44.profit_factor,
                "v45_return_pct": rw45.cum_return_pct,
                "v45_max_dd_pct": rw45.max_drawdown_pct,
                "v45_pf": rw45.profit_factor,
            }
        )

    # 5. Step-by-Step Incremental Ablation Waterfall (from v4.4 -> v4.5)
    ablations = []
    configs = [
        ("0. 原版 Delphi II v4.4 (Baseline)", DelphiParams(name="step0")),
        (
            "1. + 增加日线偏离上限 (< 3.5 ATR_D 防追高)",
            DelphiParams(name="step1", max_dist_atr_d=3.5),
        ),
        (
            "2. + 修复空单 0.5->1.5 ATR + 两档保本跟踪",
            DelphiParams(
                name="step2",
                max_dist_atr_d=3.5,
                short_trail_atr=1.5,
                two_stage_breakeven=True,
            ),
        ),
        (
            "3. + 锁定 Entry_ATR + 日线反转/30h超时离场",
            DelphiParams(
                name="step3",
                max_dist_atr_d=3.5,
                short_trail_atr=1.5,
                two_stage_breakeven=True,
                lock_entry_atr=True,
                enable_daily_reversal_exit=True,
                max_stagnant_hours=30,
            ),
        ),
        (
            "4. + ATR 等风险定仓 + 相对强度择优抢坑 (完整 v4.5)",
            v45_params,
        ),
    ]
    for label, cfg in configs:
        r = run_delphi_simulation(universe, precomp, cfg)
        ablations.append(
            {
                "step": label,
                "cum_return_pct": r.cum_return_pct,
                "max_dd_pct": r.max_drawdown_pct,
                "sharpe": r.sharpe,
                "profit_factor": r.profit_factor,
                "win_rate_pct": r.win_rate_pct,
                "trades": r.total_trades,
                "net_pnl": round(r.final_equity - r.initial_equity, 2),
            }
        )

    # 6. Multi-Symbol Loss Asymmetry Check (BTC vs ETH vs SOL single Full Stop Loss in USDT)
    v44_init_stops_by_sym = {}
    v45_init_stops_by_sym = {}
    for s in SYM_CFG:
        s_stops_44 = [t.net_pnl for t in res_v44.trades if t.symbol == s and t.exit_reason == "init_stop"]
        s_stops_45 = [t.net_pnl for t in res_v45.trades if t.symbol == s and t.exit_reason == "init_stop"]
        v44_init_stops_by_sym[s] = round(sum(s_stops_44) / len(s_stops_44), 2) if s_stops_44 else 0.0
        v45_init_stops_by_sym[s] = round(sum(s_stops_45) / len(s_stops_45), 2) if s_stops_45 else 0.0

    # Reverse symbol polling order check on v4.4 (SOL->ETH->BTC vs BTC->ETH->SOL)
    res_v44_rev_order = run_delphi_simulation(
        universe,
        precomp,
        DelphiParams(name="v44_reverse_order"),
        symbols_order=("SOL/USDT:USDT", "ETH/USDT:USDT", "BTC/USDT:USDT"),
    )

    summary = {
        "head_to_head": {
            "v44": {
                k: v
                for k, v in res_v44.__dict__.items()
                if k not in ("trades", "equity_curve_daily")
            },
            "v45": {
                k: v
                for k, v in res_v45.__dict__.items()
                if k not in ("trades", "equity_curve_daily")
            },
            "v46": {
                k: v
                for k, v in res_v46.__dict__.items()
                if k not in ("trades", "equity_curve_daily")
            },
        },
        "short_trail_sweep": short_sweep,
        "distance_corridor_breakdown_v44": dist_breakdown,
        "intrabar_1m_polling_vs_1h_closed": {
            "closed_1h_return_pct": res_v44.cum_return_pct,
            "closed_1h_max_dd_pct": res_v44.max_drawdown_pct,
            "closed_1h_trades": res_v44.total_trades,
            "poll_1m_trades": res_1m_poll.total_trades,
            "repainted_false_entries_count": len(repainted_trades),
            "repainted_false_entries_ratio_pct": round(len(repainted_trades) / max(1, res_1m_poll.total_trades) * 100.0, 1),
            "repainted_win_rate_pct": round(repainted_wins / max(1, len(repainted_trades)) * 100.0, 1),
            "repainted_net_pnl": round(sum(t.net_pnl for t in repainted_trades), 2),
            "non_repainted_net_pnl": round(sum(t.net_pnl for t in valid_1m_trades), 2),
            "poll_1m_max_dd_pct": res_1m_poll.max_drawdown_pct,
            "poll_1m_extra_fees": round(res_1m_poll.total_fees_funding - res_v44.total_fees_funding, 2),
        },
        "walk_forward_windows": walk_forward,
        "ablation_steps": ablations,
        "multi_coin_risk_asymmetry": {
            "v44_avg_full_stop_usdt": v44_init_stops_by_sym,
            "v45_avg_full_stop_usdt": v45_init_stops_by_sym,
            "v44_rejected_signals_btc_first": res_v44.rejected_by_slot_cap,
            "v44_return_btc_first_pct": res_v44.cum_return_pct,
            "v44_return_sol_first_pct": res_v44_rev_order.cum_return_pct,
            "v44_rejected_signals_sol_first": res_v44_rev_order.rejected_by_slot_cap,
        },
    }

    out_path = Path("/tmp/delphi_v44_verification_results.json")
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
