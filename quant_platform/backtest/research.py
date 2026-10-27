"""Advanced backtest validation: IS/OOS split, Walk-Forward rolling optimization, and Parameter Sensitivity Heatmap (P5)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from quant_platform.backtest.engine import BacktestEngine, BacktestRunResult
from quant_platform.backtest.report import compute_return_series_metrics
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, StrategyConfig, SymbolSpec
from quant_platform.data import HistoricalDataFeed, SimulatedClock


def split_bars_in_out_sample(bars: list[Bar], in_sample_ratio: float = 0.7) -> tuple[list[Bar], list[Bar]]:
    """Chronological non-overlapping In-Sample / Out-Of-Sample split."""
    if not (0.1 <= in_sample_ratio <= 0.95):
        raise ValueError("in_sample_ratio must be in [0.1, 0.95]")
    ordered = sorted(bars, key=lambda b: b.open_time_ms)
    pivot = max(1, min(len(ordered) - 1, int(len(ordered) * in_sample_ratio)))
    return ordered[:pivot], ordered[pivot:]


def _run_slice_backtest(
    *,
    db_session: Session,
    bars: list[Bar],
    spec: SymbolSpec,
    strategy_cls: type[Strategy],
    strategy_code: str,
    config: StrategyConfig,
    slice_tag: str,
) -> BacktestRunResult:
    clock = SimulatedClock(bars[0].open_time_ms)
    feed = HistoricalDataFeed(clock, source=slice_tag)
    feed.register_symbol_spec(spec)
    feed.load_bars(bars[0].exchange, bars[0].symbol, bars[0].interval, bars)
    return BacktestEngine(
        db_session=db_session,
        clock=clock,
        data_feed=feed,
        strategy_cls=strategy_cls,
        strategy_code=strategy_code,
        config=config,
    ).run()


def run_walk_forward_analysis(
    *,
    db_session: Session,
    bars: list[Bar],
    spec: SymbolSpec,
    strategy_cls: type[Strategy],
    strategy_code: str,
    base_config: StrategyConfig,
    param_candidates: list[dict[str, Any]],
    train_size: int,
    test_size: int,
) -> dict[str, Any]:
    """
    Rolling Walk-Forward validation:
    On each window k, selects best candidate parameters on train slice [start : start + train_size]
    by Sharpe/return, then evaluates strictly out-of-sample on [start + train_size : start + train_size + test_size].
    """
    ordered = sorted(bars, key=lambda b: b.open_time_ms)
    windows: list[dict[str, Any]] = []
    cursor = 0
    win_idx = 0

    while cursor + train_size + test_size <= len(ordered):
        train_bars = ordered[cursor : cursor + train_size]
        test_bars = ordered[cursor + train_size : cursor + train_size + test_size]

        best_params = param_candidates[0]
        best_score = float("-inf")
        for c_idx, cand in enumerate(param_candidates):
            cfg_train = base_config.model_copy(
                update={"params": cand, "extra": {"wf_window": win_idx, "role": "train", "cand": c_idx}}
            )
            res_train = _run_slice_backtest(
                db_session=db_session,
                bars=train_bars,
                spec=spec,
                strategy_cls=strategy_cls,
                strategy_code=strategy_code,
                config=cfg_train,
                slice_tag=f"wf_train_{win_idx}_{c_idx}",
            )
            m = compute_return_series_metrics(res_train.equity_curve)
            score = m["sharpe"] if m["sharpe"] != 0.0 else m["cumulative_return"]
            if score > best_score:
                best_score = score
                best_params = cand

        cfg_oos = base_config.model_copy(
            update={"params": best_params, "extra": {"wf_window": win_idx, "role": "oos"}}
        )
        res_oos = _run_slice_backtest(
            db_session=db_session,
            bars=test_bars,
            spec=spec,
            strategy_cls=strategy_cls,
            strategy_code=strategy_code,
            config=cfg_oos,
            slice_tag=f"wf_oos_{win_idx}",
        )
        oos_metrics = compute_return_series_metrics(res_oos.equity_curve)
        windows.append(
            {
                "window_index": win_idx,
                "train_start_ms": train_bars[0].open_time_ms,
                "train_end_ms": train_bars[-1].close_time_ms,
                "test_start_ms": test_bars[0].open_time_ms,
                "test_end_ms": test_bars[-1].close_time_ms,
                "best_params": best_params,
                "in_sample_score": best_score,
                "oos_return": oos_metrics["cumulative_return"],
                "oos_sharpe": oos_metrics["sharpe"],
                "oos_max_drawdown": oos_metrics["max_drawdown"],
                "oos_run_id": res_oos.run_record.run_id,
            }
        )
        cursor += test_size
        win_idx += 1

    return {"window_count": len(windows), "windows": windows}


def compute_parameter_sensitivity_heatmap(
    *,
    db_session: Session,
    bars: list[Bar],
    spec: SymbolSpec,
    strategy_cls: type[Strategy],
    strategy_code: str,
    base_config: StrategyConfig,
    param_x_name: str,
    param_x_values: list[Any],
    param_y_name: str,
    param_y_values: list[Any],
) -> dict[str, Any]:
    """Evaluate 2D parameter grid returning Sharpe, Cumulative Return, and Max Drawdown cells."""
    cells: list[dict[str, Any]] = []
    for x_val in param_x_values:
        for y_val in param_y_values:
            merged_params = {**base_config.params, param_x_name: x_val, param_y_name: y_val}
            cfg = base_config.model_copy(
                update={"params": merged_params, "extra": {"grid": f"{x_val}_{y_val}"}}
            )
            res = _run_slice_backtest(
                db_session=db_session,
                bars=bars,
                spec=spec,
                strategy_cls=strategy_cls,
                strategy_code=strategy_code,
                config=cfg,
                slice_tag=f"hm_{x_val}_{y_val}",
            )
            m = compute_return_series_metrics(res.equity_curve)
            cells.append(
                {
                    "x": x_val,
                    "y": y_val,
                    "cumulative_return": m["cumulative_return"],
                    "sharpe": m["sharpe"],
                    "max_drawdown": m["max_drawdown"],
                    "run_id": res.run_record.run_id,
                }
            )
    return {
        "param_x_name": param_x_name,
        "param_y_name": param_y_name,
        "x_values": param_x_values,
        "y_values": param_y_values,
        "cells": cells,
    }
