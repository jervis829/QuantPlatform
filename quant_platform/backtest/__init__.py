"""Event-driven crypto backtest matching engine, fee/margin/liquidation models, and report analytics (P4 & P5)."""

from quant_platform.backtest.broker import BacktestBroker
from quant_platform.backtest.engine import BacktestEngine, BacktestRunResult, convert_intent_to_order_request
from quant_platform.backtest.fees import compute_fill_fee, resolve_fee_tier
from quant_platform.backtest.margin import (
    compute_adl_AutoDeleverage_rank,
    compute_funding_payment_amount,
    compute_liquidation_price,
    compute_maintenance_margin,
    compute_unrealized_pnl,
    should_liquidate_at_mark_price,
)
from quant_platform.backtest.matching import (
    SlippageModelConfig,
    estimate_limit_fill_probability,
    walk_orderbook_levels,
)
from quant_platform.backtest.report import build_and_persist_backtest_report, compute_return_series_metrics
from quant_platform.backtest.research import (
    compute_parameter_sensitivity_heatmap,
    run_walk_forward_analysis,
    split_bars_in_out_sample,
)

__all__ = [
    "BacktestBroker",
    "BacktestEngine",
    "BacktestRunResult",
    "SlippageModelConfig",
    "build_and_persist_backtest_report",
    "compute_adl_AutoDeleverage_rank",
    "compute_fill_fee",
    "compute_funding_payment_amount",
    "compute_liquidation_price",
    "compute_maintenance_margin",
    "compute_parameter_sensitivity_heatmap",
    "compute_return_series_metrics",
    "compute_unrealized_pnl",
    "convert_intent_to_order_request",
    "estimate_limit_fill_probability",
    "resolve_fee_tier",
    "run_walk_forward_analysis",
    "should_liquidate_at_mark_price",
    "split_bars_in_out_sample",
    "walk_orderbook_levels",
]
