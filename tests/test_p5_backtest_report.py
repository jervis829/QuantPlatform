"""P5 Tests (Test-First): Backtest report metrics cross-validated with quantstats (< 1e-6), JSON persistence, lineage back-links, walk-forward, parameter heatmap, and IS/OOS split."""

from __future__ import annotations

from decimal import Decimal
import json
import pandas as pd
import quantstats.stats as qs
from sqlalchemy.orm import Session

from quant_platform.backtest import BacktestEngine
from quant_platform.backtest.report import build_and_persist_backtest_report, compute_return_series_metrics
from quant_platform.backtest.research import (
    compute_parameter_sensitivity_heatmap,
    run_walk_forward_analysis,
    split_bars_in_out_sample,
)
from quant_platform.core import (
    Bar,
    ContractType,
    EquityPoint,
    FundingRatePoint,
    MarkIndexPrice,
    OrderType,
    RunEnv,
    Signal,
    SignalAction,
    Strategy,
    StrategyConfig,
    SymbolSpec,
)
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db import UnifiedLedgerRepository
from quant_platform.strategy.indicators import sma


def test_metrics_cross_validated_against_quantstats_within_1e_6() -> None:
    """Acceptance ①: Cumulative/Annualized return, Max Drawdown, Sharpe, Sortino, Calmar match quantstats < 1e-6."""
    daily_rets = [
        0.012, -0.008, 0.025, -0.019, 0.005, 0.018, -0.011, 0.009, 0.014, -0.006,
        0.021, -0.015, 0.007, 0.013, -0.004, 0.016, -0.022, 0.019, 0.008, 0.011,
        -0.009, 0.015, 0.006, -0.003, 0.017, -0.012, 0.010, 0.014, -0.005, 0.009,
    ]
    idx = pd.date_range("2025-01-01", periods=len(daily_rets), freq="D", tz="UTC")
    qs_series = pd.Series(daily_rets, index=idx)

    # Build matching EquityPoint curve starting at 10,000
    t0 = int(idx[0].timestamp() * 1000)
    eq = Decimal("10000")
    points = [
        EquityPoint(env=RunEnv.BACKTEST, run_id="r_qs", ts_ms=t0 - 86_400_000, equity=eq, cash=eq)
    ]
    for i, r in enumerate(daily_rets):
        eq = eq * (Decimal("1") + Decimal(str(r)))
        points.append(
            EquityPoint(
                env=RunEnv.BACKTEST,
                run_id="r_qs",
                ts_ms=int(idx[i].timestamp() * 1000),
                equity=eq,
                cash=eq,
            )
        )

    ours = compute_return_series_metrics(points, periods_per_year=365)

    qs_cum = float(qs.comp(qs_series))
    qs_cagr = float(qs.cagr(qs_series, periods=365))
    qs_mdd = float(qs.max_drawdown(qs_series))
    qs_sharpe = float(qs.sharpe(qs_series, periods=365, smart=False))
    qs_sortino = float(qs.sortino(qs_series, periods=365, smart=False))
    qs_calmar = float(qs.calmar(qs_series, periods=365))

    assert abs(ours["cumulative_return"] - qs_cum) < 1e-6
    assert abs(ours["annualized_return"] - qs_cagr) < 1e-6
    assert abs(ours["max_drawdown"] - qs_mdd) < 1e-6
    assert abs(ours["sharpe"] - qs_sharpe) < 1e-6
    assert abs(ours["sortino"] - qs_sortino) < 1e-6
    assert abs(ours["calmar"] - qs_calmar) < 1e-6


def _build_multi_day_bars(start_ms: int, prices: list[str], interval: str = "1d") -> list[Bar]:
    step = 86_400_000
    bars: list[Bar] = []
    for i, px_str in enumerate(prices):
        px = Decimal(px_str)
        open_ms = start_ms + i * step
        bars.append(
            Bar(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                interval=interval,
                open_time_ms=open_ms,
                close_time_ms=open_ms + step - 1,
                open=px,
                high=px + Decimal("100"),
                low=px - Decimal("100"),
                close=px,
                volume=Decimal("50"),
                is_closed=True,
                exchange_ts_ms=open_ms + step - 1,
                local_recv_ts_ms=open_ms + step,
            )
        )
    return bars


class _TrendReportStrategy(Strategy):
    def on_start(self):
        self.closes = []
        return None

    def on_bar(self, bar: Bar):
        self.closes.append(bar.close)
        fast_w = int(self.params.get("fast", 2))
        slow_w = int(self.params.get("slow", 4))
        if len(self.closes) < slow_w:
            return None
        f_val = sma(self.closes, fast_w)
        s_val = sma(self.closes, slow_w)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos else Decimal("0")
        if f_val > s_val and cur_qty <= Decimal("0"):
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    quantity=Decimal("0.200"),
                    order_type=OrderType.MARKET,
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if f_val < s_val and cur_qty > Decimal("0"):
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    quantity=abs(cur_qty),
                    order_type=OrderType.MARKET,
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None


def test_full_backtest_report_json_persistence_and_lineage(db_session: Session) -> None:
    """Acceptance ② & ③: Full report persisted to DB JSON and back-linked to run_id, strategy_version_id, symbol_spec_version."""
    # Span Jan & Feb 2025 (40 days) so monthly return matrix has multiple months
    t0 = 1735689600000  # 2025-01-01 00:00:00 UTC
    prices = [
        "50000", "50500", "51200", "52000", "53000", "52200", "51000", "50200", "49800", "50400",
        "51500", "52800", "54000", "55200", "54500", "53100", "52000", "51500", "52600", "53800",
        "55000", "56200", "57000", "56000", "54800", "53900", "54700", "55900", "57200", "58500",
        "59100", "58000", "56800", "55900", "56700", "57900", "59200", "60500", "61200", "62000",
    ]
    bars = _build_multi_day_bars(t0, prices, interval="1d")
    spec = SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        version=3,
        effective_from_ms=0,
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5.0"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        max_leverage=Decimal("20"),
    )

    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock, source="p5_test")
    feed.register_symbol_spec(spec)
    feed.load_bars("binance", "BTC/USDT:USDT", "1d", bars)
    # Also load 1m synthetic bar alias or orderbook snapshots at each daily close so broker fills @ bar close
    from quant_platform.core import OrderBookLevel, OrderBookSnapshot

    feed.load_orderbooks(
        "binance",
        "BTC/USDT:USDT",
        [
            OrderBookSnapshot(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                bids=[OrderBookLevel(price=b.close, quantity=Decimal("10"))],
                asks=[OrderBookLevel(price=b.close, quantity=Decimal("10"))],
                last_update_id=b.close_time_ms,
                exchange_ts_ms=b.close_time_ms,
                local_recv_ts_ms=b.close_time_ms,
            )
            for b in bars
        ],
    )
    feed.load_mark_prices(
        "binance",
        "BTC/USDT:USDT",
        [
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=b.close,
                index_price=b.close,
                exchange_ts_ms=b.close_time_ms,
                local_recv_ts_ms=b.close_time_ms,
            )
            for b in bars
        ],
    )
    feed.load_funding_rates(
        "binance",
        "BTC/USDT:USDT",
        [
            FundingRatePoint(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                funding_rate=Decimal("0.0001"),
                mark_price=bars[12].close,
                funding_ts_ms=bars[12].close_time_ms,
                local_recv_ts_ms=bars[12].close_time_ms,
            )
        ],
    )

    cfg = StrategyConfig(
        strategy_id="trend_btc",
        strategy_version_id="trend_btc:v2",
        name="Trend BTC",
        exchanges=["binance"],
        symbols=["BTC/USDT:USDT"],
        bar_interval="1d",
        params={"fast": 2, "slow": 4},
        default_leverage=Decimal("3"),
        initial_balances={"USDT": Decimal("10000")},
    )
    bt_res = BacktestEngine(
        db_session=db_session,
        clock=clock,
        data_feed=feed,
        strategy_cls=_TrendReportStrategy,
        strategy_code="class _TrendReportStrategy(Strategy): pass",
        config=cfg,
    ).run()

    report = build_and_persist_backtest_report(
        db_session=db_session,
        result=bt_res,
        benchmark_bars=bars,
    )
    db_session.commit()

    # Verify JSON round-trip and DB storage
    encoded = json.dumps(report)
    decoded = json.loads(encoded)
    stored_run = UnifiedLedgerRepository(db_session).get_run(RunEnv.BACKTEST, bt_res.run_record.run_id)
    assert stored_run is not None and stored_run.report is not None

    # Verify lineage back-links (Acceptance ③)
    lineage = decoded["lineage"]
    assert lineage["run_id"] == bt_res.run_record.run_id
    assert lineage["strategy_version_id"] == "trend_btc:v2"
    assert lineage["symbol_spec_version"] == bt_res.run_record.symbol_spec_version
    assert lineage["dataset_version"] == bt_res.run_record.dataset_version

    # Verify all required metric groups exist and have valid data
    for required_key in [
        "summary_metrics",
        "drawdown_details",
        "trade_metrics",
        "exposure_and_concentration",
        "cost_breakdown",
        "holding_duration_distribution",
        "per_trade_attribution",
        "daily_attribution",
        "benchmark_comparison",
        "monthly_return_matrix",
        "equity_curve",
    ]:
        assert required_key in decoded, f"Missing report section {required_key}"

    assert "2025" in decoded["monthly_return_matrix"]
    assert "01" in decoded["monthly_return_matrix"]["2025"]
    assert "02" in decoded["monthly_return_matrix"]["2025"]
    assert decoded["trade_metrics"]["closed_trade_count"] >= 1


def test_in_out_sample_split_walk_forward_and_sensitivity_heatmap(db_session: Session) -> None:
    """Advanced P5: IS/OOS split, rolling Walk-Forward validation, and 2D Parameter Sensitivity Heatmap."""
    t0 = 1735689600000
    prices = [
        "50000", "50500", "51200", "52000", "53000", "52200", "51000", "50200", "49800", "50400",
        "51500", "52800", "54000", "55200", "54500", "53100", "52000", "51500", "52600", "53800",
        "55000", "56200", "57000", "56000", "54800", "53900", "54700", "55900", "57200", "58500",
    ]
    bars = _build_multi_day_bars(t0, prices, interval="1d")
    spec = SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        version=1,
        effective_from_ms=0,
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5.0"),
        max_leverage=Decimal("20"),
    )

    is_bars, oos_bars = split_bars_in_out_sample(bars, in_sample_ratio=0.7)
    assert len(is_bars) == 21 and len(oos_bars) == 9
    assert is_bars[-1].close_time_ms < oos_bars[0].open_time_ms

    wf = run_walk_forward_analysis(
        db_session=db_session,
        bars=bars,
        spec=spec,
        strategy_cls=_TrendReportStrategy,
        strategy_code="class _TrendReportStrategy(Strategy): pass",
        base_config=StrategyConfig(
            strategy_id="wf_strat",
            strategy_version_id="wf_strat:v1",
            name="WF Strat",
            exchanges=["binance"],
            symbols=["BTC/USDT:USDT"],
            bar_interval="1d",
            initial_balances={"USDT": Decimal("10000")},
        ),
        param_candidates=[{"fast": 2, "slow": 4}, {"fast": 3, "slow": 5}],
        train_size=12,
        test_size=6,
    )
    assert len(wf["windows"]) == 3
    assert all("best_params" in w and "oos_return" in w for w in wf["windows"])

    heatmap = compute_parameter_sensitivity_heatmap(
        db_session=db_session,
        bars=bars,
        spec=spec,
        strategy_cls=_TrendReportStrategy,
        strategy_code="class _TrendReportStrategy(Strategy): pass",
        base_config=StrategyConfig(
            strategy_id="hm_strat",
            strategy_version_id="hm_strat:v1",
            name="HM Strat",
            exchanges=["binance"],
            symbols=["BTC/USDT:USDT"],
            bar_interval="1d",
            initial_balances={"USDT": Decimal("10000")},
        ),
        param_x_name="fast",
        param_x_values=[2, 3],
        param_y_name="slow",
        param_y_values=[4, 6],
    )
    assert len(heatmap["cells"]) == 4
    assert heatmap["x_values"] == [2, 3]
    assert heatmap["y_values"] == [4, 6]
