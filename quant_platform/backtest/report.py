"""Backtest performance metrics, attribution, benchmark comparison, and JSON report builder (P5)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import math
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from quant_platform.backtest.engine import BacktestRunResult
from quant_platform.core.decimal_utils import ZERO, format_decimal
from quant_platform.core.enums import OrderSide, RunEnv
from quant_platform.core.models import Bar, EquityPoint, Fill
from quant_platform.db.ledger import UnifiedLedgerRepository


def _safe_float(val: float) -> float:
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return float(val)


def compute_return_series_metrics(
    equity_curve: list[EquityPoint],
    *,
    periods_per_year: int = 365,
) -> dict[str, Any]:
    """
    Compute cumulative return, CAGR, Max Drawdown (with start/trough/recovery/duration),
    Sharpe, Sortino, and Calmar matching quantstats.stats conventions (< 1e-6).
    """
    if len(equity_curve) < 2:
        return {
            "cumulative_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "drawdown_details": {
                "peak_ts_ms": equity_curve[0].ts_ms if equity_curve else 0,
                "trough_ts_ms": equity_curve[0].ts_ms if equity_curve else 0,
                "recovery_ts_ms": None,
                "duration_ms": 0,
            },
        }

    dedup: list[EquityPoint] = []
    for pt in equity_curve:
        if dedup and dedup[-1].ts_ms == pt.ts_ms:
            dedup[-1] = pt
        else:
            dedup.append(pt)

    eq_vals = np.array([float(pt.equity) for pt in dedup], dtype=np.float64)
    ts_vals = [pt.ts_ms for pt in dedup]

    rets = np.diff(eq_vals) / eq_vals[:-1]
    n_obs = len(rets)
    cum_ret = float(np.prod(1.0 + rets) - 1.0)
    years = n_obs / float(periods_per_year)
    cagr = float((1.0 + cum_ret) ** (1.0 / years) - 1.0) if (years > 0 and (1.0 + cum_ret) > 0) else 0.0

    wealth = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    peaks = np.maximum.accumulate(wealth)
    dd_series = (wealth - peaks) / peaks
    trough_idx = int(np.argmin(dd_series))
    max_dd = float(dd_series[trough_idx])

    peak_idx = int(np.argmax(wealth[: trough_idx + 1])) if trough_idx > 0 else 0
    recovery_idx: int | None = None
    for idx in range(trough_idx + 1, len(wealth)):
        if wealth[idx] >= wealth[peak_idx]:
            recovery_idx = idx
            break
    end_dd_idx = recovery_idx if recovery_idx is not None else (len(wealth) - 1)
    dd_duration_ms = ts_vals[end_dd_idx] - ts_vals[peak_idx] if max_dd < 0 else 0

    mean_ret = float(np.mean(rets))
    std_ret = float(np.std(rets, ddof=1)) if n_obs > 1 else 0.0
    sharpe = (mean_ret / std_ret) * math.sqrt(periods_per_year) if std_ret > 0 else 0.0

    neg_sq_sum = float(np.sum(np.where(rets < 0.0, rets**2, 0.0)))
    downside_dev = math.sqrt(neg_sq_sum / n_obs) if n_obs > 0 else 0.0
    sortino = (mean_ret / downside_dev) * math.sqrt(periods_per_year) if downside_dev > 0 else 0.0

    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0

    return {
        "cumulative_return": _safe_float(cum_ret),
        "annualized_return": _safe_float(cagr),
        "max_drawdown": _safe_float(max_dd),
        "sharpe": _safe_float(sharpe),
        "sortino": _safe_float(sortino),
        "calmar": _safe_float(calmar),
        "drawdown_details": {
            "max_drawdown": _safe_float(max_dd),
            "peak_ts_ms": ts_vals[peak_idx],
            "trough_ts_ms": ts_vals[trough_idx],
            "recovery_ts_ms": ts_vals[recovery_idx] if recovery_idx is not None else None,
            "duration_ms": dd_duration_ms,
        },
    }


def _reconstruct_round_trip_trades(fills: list[Fill]) -> list[dict[str, Any]]:
    """FIFO round-trip lot matcher reconstructing closed trades with holding duration & net PnL."""
    open_lots: dict[tuple[str, str], list[dict[str, Any]]] = {}
    closed_trades: list[dict[str, Any]] = []

    for f in fills:
        key = (f.exchange, f.symbol)
        lots = open_lots.setdefault(key, [])
        signed_qty = f.quantity if f.side == OrderSide.BUY else -f.quantity
        remaining = f.quantity
        unit_fee = f.fee / f.quantity if f.quantity > ZERO else ZERO

        while remaining > ZERO and lots and (
            (lots[0]["signed_qty"] > ZERO and signed_qty < ZERO)
            or (lots[0]["signed_qty"] < ZERO and signed_qty > ZERO)
        ):
            head = lots[0]
            head_abs = abs(head["signed_qty"])
            matched = min(remaining, head_abs)
            side_label = "LONG" if head["signed_qty"] > ZERO else "SHORT"
            gross = (
                matched * (f.price - head["price"])
                if head["signed_qty"] > ZERO
                else matched * (head["price"] - f.price)
            )
            fees = matched * (head["unit_fee"] + unit_fee)
            net = gross - fees
            hold_ms = max(0, f.exchange_ts_ms - head["ts_ms"])

            closed_trades.append(
                {
                    "exchange": f.exchange,
                    "symbol": f.symbol,
                    "side": side_label,
                    "quantity": format_decimal(matched),
                    "entry_price": format_decimal(head["price"]),
                    "exit_price": format_decimal(f.price),
                    "entry_ts_ms": head["ts_ms"],
                    "exit_ts_ms": f.exchange_ts_ms,
                    "holding_ms": hold_ms,
                    "gross_pnl": float(gross),
                    "fees": float(fees),
                    "net_pnl": float(net),
                }
            )
            remaining -= matched
            if matched >= head_abs:
                lots.pop(0)
            else:
                new_abs = head_abs - matched
                head["signed_qty"] = new_abs if head["signed_qty"] > ZERO else -new_abs

        if remaining > ZERO:
            lots.append(
                {
                    "signed_qty": remaining if f.side == OrderSide.BUY else -remaining,
                    "price": f.price,
                    "unit_fee": unit_fee,
                    "ts_ms": f.exchange_ts_ms,
                }
            )

    return closed_trades


def _compute_monthly_matrix(equity_curve: list[EquityPoint]) -> dict[str, dict[str, float]]:
    if len(equity_curve) < 2:
        return {}
    records = [
        {
            "dt": datetime.fromtimestamp(pt.ts_ms / 1000.0, tz=timezone.utc),
            "equity": float(pt.equity),
        }
        for pt in equity_curve
    ]
    df = pd.DataFrame(records).set_index("dt").sort_index()
    # Keep daily/last observation per month plus initial equity anchor
    init_eq = float(df["equity"].iloc[0])
    monthly_end = df["equity"].resample("ME").last().dropna()
    matrix: dict[str, dict[str, float]] = {}
    prev_eq = init_eq
    for ts, eq_val in monthly_end.items():
        yr = f"{ts.year:04d}"
        mo = f"{ts.month:02d}"
        m_ret = (float(eq_val) / prev_eq) - 1.0 if prev_eq > 0 else 0.0
        prev_eq = float(eq_val)
        matrix.setdefault(yr, {})[mo] = round(m_ret, 8)

    for yr, months in matrix.items():
        comp = 1.0
        for mo_key, r in months.items():
            comp *= 1.0 + r
        months["ytd"] = round(comp - 1.0, 8)
    return matrix


def _compute_daily_attribution(
    equity_curve: list[EquityPoint],
    fills: list[Fill],
    funding_payments: list[Any],
) -> list[dict[str, Any]]:
    if not equity_curve:
        return []
    by_day_eq: dict[str, list[EquityPoint]] = {}
    for pt in equity_curve:
        day = datetime.fromtimestamp(pt.ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day_eq.setdefault(day, []).append(pt)

    fees_by_day: dict[str, Decimal] = {}
    rpnl_by_day: dict[str, Decimal] = {}
    for f in fills:
        day = datetime.fromtimestamp(f.exchange_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        fees_by_day[day] = fees_by_day.get(day, ZERO) + f.fee
        rpnl_by_day[day] = rpnl_by_day.get(day, ZERO) + f.realized_pnl

    fund_by_day: dict[str, Decimal] = {}
    for fp in funding_payments:
        day = datetime.fromtimestamp(fp.settlement_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        fund_by_day[day] = fund_by_day.get(day, ZERO) + fp.amount

    daily_rows: list[dict[str, Any]] = []
    prev_end: Decimal | None = None
    for day in sorted(by_day_eq.keys()):
        pts = by_day_eq[day]
        start_eq = prev_end if prev_end is not None else pts[0].equity
        end_eq = pts[-1].equity
        d_ret = float((end_eq - start_eq) / start_eq) if start_eq > ZERO else 0.0
        prev_end = end_eq
        daily_rows.append(
            {
                "date": day,
                "start_equity": format_decimal(start_eq),
                "end_equity": format_decimal(end_eq),
                "daily_return": round(d_ret, 8),
                "realized_pnl": format_decimal(rpnl_by_day.get(day, ZERO)),
                "fees": format_decimal(fees_by_day.get(day, ZERO)),
                "funding": format_decimal(fund_by_day.get(day, ZERO)),
            }
        )
    return daily_rows


def _compute_benchmark_comparison(
    equity_curve: list[EquityPoint],
    benchmark_bars: list[Bar] | None,
) -> dict[str, Any]:
    if not benchmark_bars or len(benchmark_bars) < 2 or len(equity_curve) < 2:
        return {
            "benchmark_symbol": benchmark_bars[0].symbol if benchmark_bars else None,
            "benchmark_cumulative_return": 0.0,
            "excess_return": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "information_ratio": 0.0,
        }
    b_closes = np.array([float(b.close) for b in benchmark_bars], dtype=np.float64)
    b_rets = np.diff(b_closes) / b_closes[:-1]
    bench_cum = float(b_closes[-1] / b_closes[0] - 1.0)

    eq_vals = np.array([float(pt.equity) for pt in equity_curve], dtype=np.float64)
    s_rets = np.diff(eq_vals) / eq_vals[:-1]
    strat_cum = float(eq_vals[-1] / eq_vals[0] - 1.0)

    min_len = min(len(b_rets), len(s_rets))
    if min_len < 2:
        return {
            "benchmark_symbol": benchmark_bars[0].symbol,
            "benchmark_cumulative_return": bench_cum,
            "excess_return": strat_cum - bench_cum,
            "alpha": 0.0,
            "beta": 0.0,
            "information_ratio": 0.0,
        }
    sr = s_rets[-min_len:]
    br = b_rets[-min_len:]
    var_b = float(np.var(br, ddof=1))
    beta = float(np.cov(sr, br, ddof=1)[0, 1] / var_b) if var_b > 0 else 0.0
    alpha_daily = float(np.mean(sr) - beta * np.mean(br))
    diff = sr - br
    diff_std = float(np.std(diff, ddof=1))
    ir = float(np.mean(diff) / diff_std * math.sqrt(365)) if diff_std > 0 else 0.0

    return {
        "benchmark_symbol": benchmark_bars[0].symbol,
        "benchmark_cumulative_return": _safe_float(bench_cum),
        "excess_return": _safe_float(strat_cum - bench_cum),
        "alpha": _safe_float(alpha_daily * 365),
        "beta": _safe_float(beta),
        "information_ratio": _safe_float(ir),
    }


def build_and_persist_backtest_report(
    *,
    db_session: Session,
    result: BacktestRunResult,
    benchmark_bars: list[Bar] | None = None,
    periods_per_year: int = 365,
) -> dict[str, Any]:
    """
    Build comprehensive JSON-serializable backtest report, persist it to runs.report_json,
    and link back to run_id, strategy_version_id, dataset_version, and symbol_spec_version.
    """
    ledger = UnifiedLedgerRepository(db_session)
    funding_list = ledger.list_funding_payments(RunEnv.BACKTEST, result.run_record.run_id)

    series_metrics = compute_return_series_metrics(result.equity_curve, periods_per_year=periods_per_year)
    closed_trades = _reconstruct_round_trip_trades(result.fills)

    wins = [t["net_pnl"] for t in closed_trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in closed_trades if t["net_pnl"] <= 0]
    n_trades = len(closed_trades)
    win_rate = (len(wins) / n_trades) if n_trades > 0 else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else (float(len(wins)) if wins else 0.0)
    expectancy = (win_rate * avg_win) + ((1.0 - win_rate) * avg_loss) if n_trades > 0 else 0.0

    hold_durations = [t["holding_ms"] for t in closed_trades]
    if hold_durations:
        arr_h = np.array(hold_durations, dtype=np.float64)
        holding_dist = {
            "count": len(hold_durations),
            "min_ms": int(np.min(arr_h)),
            "p25_ms": int(np.percentile(arr_h, 25)),
            "median_ms": int(np.percentile(arr_h, 50)),
            "p75_ms": int(np.percentile(arr_h, 75)),
            "max_ms": int(np.max(arr_h)),
            "mean_ms": float(np.mean(arr_h)),
        }
    else:
        holding_dist = {"count": 0, "min_ms": 0, "p25_ms": 0, "median_ms": 0, "p75_ms": 0, "max_ms": 0, "mean_ms": 0.0}

    avg_eq = float(np.mean([float(pt.equity) for pt in result.equity_curve])) if result.equity_curve else float(result.initial_equity)
    total_traded_quote = float(sum((f.quote_qty for f in result.fills), ZERO))
    turnover = (total_traded_quote / avg_eq) if avg_eq > 0 else 0.0

    # Symbol notional concentration (HHI)
    vol_by_sym: dict[str, float] = {}
    for f in result.fills:
        vol_by_sym[f.symbol] = vol_by_sym.get(f.symbol, 0.0) + float(f.quote_qty)
    hhi = (
        sum((v / total_traded_quote) ** 2 for v in vol_by_sym.values())
        if total_traded_quote > 0
        else 0.0
    )

    gross_exps = [float(pt.gross_exposure) for pt in result.equity_curve]
    net_exps = [float(pt.net_exposure) for pt in result.equity_curve]
    long_steps = sum(1 for x in net_exps if x > 0)
    short_steps = sum(1 for x in net_exps if x < 0)
    total_steps = max(1, len(net_exps))

    gross_pnl_before_costs = float(result.net_pnl + result.total_fees - result.total_funding + result.total_interest)
    denom_pnl = abs(gross_pnl_before_costs) if abs(gross_pnl_before_costs) > 1e-9 else 1.0

    report: dict[str, Any] = {
        "lineage": {
            "run_id": result.run_record.run_id,
            "strategy_id": result.run_record.strategy_id,
            "strategy_version_id": result.run_record.strategy_version_id,
            "strategy_code_hash": result.run_record.strategy_code_hash,
            "dataset_version": result.run_record.dataset_version,
            "symbol_spec_version": result.run_record.symbol_spec_version,
            "engine_version": result.run_record.engine_version,
            "rng_seed": result.run_record.rng_seed,
        },
        "summary_metrics": {
            "initial_equity": format_decimal(result.initial_equity),
            "final_equity": format_decimal(result.final_equity),
            "net_pnl": format_decimal(result.net_pnl),
            "cumulative_return": series_metrics["cumulative_return"],
            "annualized_return": series_metrics["annualized_return"],
            "max_drawdown": series_metrics["max_drawdown"],
            "sharpe": series_metrics["sharpe"],
            "sortino": series_metrics["sortino"],
            "calmar": series_metrics["calmar"],
            "turnover_rate": _safe_float(turnover),
        },
        "drawdown_details": series_metrics["drawdown_details"],
        "trade_metrics": {
            "closed_trade_count": n_trades,
            "fill_count": len(result.fills),
            "win_rate": _safe_float(win_rate),
            "payoff_ratio": _safe_float(payoff_ratio),
            "expectancy": _safe_float(expectancy),
            "avg_win": _safe_float(avg_win),
            "avg_loss": _safe_float(avg_loss),
        },
        "exposure_and_concentration": {
            "position_concentration_hhi": _safe_float(hhi),
            "avg_gross_exposure": _safe_float(float(np.mean(gross_exps)) if gross_exps else 0.0),
            "max_gross_exposure": _safe_float(float(np.max(gross_exps)) if gross_exps else 0.0),
            "avg_net_exposure": _safe_float(float(np.mean(net_exps)) if net_exps else 0.0),
            "long_time_ratio": round(long_steps / total_steps, 6),
            "short_time_ratio": round(short_steps / total_steps, 6),
        },
        "cost_breakdown": {
            "total_fees": format_decimal(result.total_fees),
            "total_funding": format_decimal(result.total_funding),
            "total_interest": format_decimal(result.total_interest),
            "fee_to_gross_pnl_ratio": _safe_float(float(result.total_fees) / denom_pnl),
            "funding_to_gross_pnl_ratio": _safe_float(float(result.total_funding) / denom_pnl),
            "fee_to_initial_equity_ratio": _safe_float(float(result.total_fees / result.initial_equity)),
            "funding_to_initial_equity_ratio": _safe_float(float(result.total_funding / result.initial_equity)),
        },
        "holding_duration_distribution": holding_dist,
        "per_trade_attribution": closed_trades,
        "daily_attribution": _compute_daily_attribution(result.equity_curve, result.fills, funding_list),
        "benchmark_comparison": _compute_benchmark_comparison(result.equity_curve, benchmark_bars),
        "monthly_return_matrix": _compute_monthly_matrix(result.equity_curve),
        "equity_curve": [
            {
                "ts_ms": pt.ts_ms,
                "equity": format_decimal(pt.equity),
                "drawdown_pct": format_decimal(pt.drawdown_pct),
                "cumulative_fees": format_decimal(pt.cumulative_fees),
                "cumulative_funding": format_decimal(pt.cumulative_funding),
            }
            for idx_pt, pt in enumerate(result.equity_curve)
            if len(result.equity_curve) <= 1500
            or idx_pt == 0
            or idx_pt == len(result.equity_curve) - 1
            or idx_pt % max(1, len(result.equity_curve) // 1000) == 0
        ],
    }

    updated_run = result.run_record.model_copy(update={"report": report})
    ledger.upsert_run(updated_run)
    return report
