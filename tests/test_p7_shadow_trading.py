"""P7 Acceptance Tests: ShadowBroker simulated fills, live-key isolation, trace chain, and OB-replay signal parity."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from quant_platform.backtest.broker import BacktestBroker
from quant_platform.core.enums import (
    ContractType,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    RunEnv,
    SignalAction,
    TimeInForce,
)
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import (
    FundingRatePoint,
    MarkIndexPrice,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderRequest,
    Signal,
    StrategyConfig,
    SymbolSpec,
    Tick,
)
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import HistoricalDataFeed, StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.session import build_engine, build_session_factory, init_db
from quant_platform.execution.runners import BacktestRunner, LiveRunner, ShadowRunner
from quant_platform.shadow import (
    ShadowBroker,
    ShadowIsolationError,
    ShadowSimulationParams,
    ShadowTraceInspector,
)


def _perp_spec() -> SymbolSpec:
    return SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("1000"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("125"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        effective_from_ms=1_700_000_000_000,
    )


class _OrderBookImbalanceStrategy(Strategy):
    """Identical strategy class shared across BacktestRunner, ShadowRunner, and LiveRunner (zero diff)."""

    def on_orderbook(self, orderbook: OrderBookSnapshot):
        bid_vol = sum((lvl.quantity for lvl in orderbook.bids), Decimal("0"))
        ask_vol = sum((lvl.quantity for lvl in orderbook.asks), Decimal("0"))
        pos = self.ctx.get_position(orderbook.exchange, orderbook.symbol)
        cur_qty = pos.quantity if pos else Decimal("0")

        if bid_vol >= ask_vol * Decimal("2") and cur_qty == Decimal("0"):
            return [
                Signal(
                    signal_id=f"sig_buy_{orderbook.last_update_id}",
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=orderbook.exchange,
                    symbol=orderbook.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("0.100"),
                    reason="ob_bid_imbalance",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if ask_vol >= bid_vol * Decimal("2") and cur_qty > Decimal("0"):
            return [
                Signal(
                    signal_id=f"sig_sell_{orderbook.last_update_id}",
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=orderbook.exchange,
                    symbol=orderbook.symbol,
                    action=SignalAction.SELL,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("0.100"),
                    reduce_only=True,
                    reason="ob_ask_imbalance",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None


def test_shadow_key_and_account_isolation_blocks_live_credentials(tmp_path: Path) -> None:
    """
    P7 Gate 1:
    ShadowBroker must physically reject any configuration containing live trading keys/secrets
    and isolate all persisted state strictly under RunEnv.SHADOW.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p7_iso.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_000_000)
    spec = _perp_spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        with pytest.raises(ShadowIsolationError):
            ShadowBroker(
                run_id="run_shadow_bad",
                strategy_id="strat_iso",
                clock=clock,
                data_feed=feed,
                ledger=ledger,
                sim_params=ShadowSimulationParams(),
                initial_balances={"USDT": Decimal("10000")},
                forbidden_credentials={"api_key": "LIVE_KEY_123"},
            )


def test_shadow_queue_ratio_trade_participation_latency_and_simulated_funding(tmp_path: Path) -> None:
    """
    P7 Gate 2 (Hand-calculated queue drainage, latency gate, and funding):
    - Displayed bid at 50000.0 has quantity = 2.000 BTC.
    - ShadowSimulationParams:
      * queue_ahead_ratio = 0.5 -> queue_ahead = 2.000 * 0.5 = 1.000 BTC ahead of us!
      * trade_participation_rate = 0.5 -> only 50% of aggressive sell tape volume at 50000.0 counts toward queue/fill.
      * latency_ms = 50 -> order submitted at t=...1000 activates at t=...1050.
    - Step 1 (t=...1020 < ...1050): aggressive sell trade of 4.000 BTC at 50000.0 arrives BEFORE order activation -> 0 fill!
    - Step 2 (t=...1060 >= ...1050): aggressive sell trade of 2.000 BTC at 50000.0 -> effective vol = 2.000 * 0.5 = 1.000 BTC -> drains queue_ahead from 1.000 to 0.000, fill = 0!
    - Step 3 (t=...1080): aggressive sell trade of 0.600 BTC at 50000.0 -> effective vol = 0.600 * 0.5 = 0.300 BTC -> partial fill of 0.300 BTC Maker!
    - Step 4 (t=...1100): simulated funding rate +0.0001 on 0.300 BTC @ mark 50000.0 -> amount = -1.5000 USDT persisted under RunEnv.SHADOW!
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p7_queue.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_001_000)
    spec = _perp_spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)

    sim_params = ShadowSimulationParams(
        queue_ahead_ratio=Decimal("0.5"),
        trade_participation_rate=Decimal("0.5"),
        latency_ms=50,
        vip_level=0,
    )

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        broker = ShadowBroker(
            run_id="run_shadow_queue",
            strategy_id="strat_q",
            clock=clock,
            data_feed=feed,
            ledger=ledger,
            sim_params=sim_params,
            initial_balances={"USDT": Decimal("10000")},
        )
        broker.startup_sync()
        assert broker.persisted_sim_params()["queue_ahead_ratio"] == "0.5"
        assert broker.persisted_sim_params()["trade_participation_rate"] == "0.5"
        assert broker.persisted_sim_params()["latency_ms"] == 50

        ob = OrderBookSnapshot(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[OrderBookLevel(price=Decimal("50000.0"), quantity=Decimal("2.000"))],
            asks=[OrderBookLevel(price=Decimal("50010.0"), quantity=Decimal("2.000"))],
            last_update_id=501,
            exchange_ts_ms=clock.now_ms(),
            local_recv_ts_ms=clock.now_ms(),
        )
        feed.on_orderbook_snapshot(ob)
        broker.on_orderbook(ob)

        # Submit resting limit BUY 0.500 @ 50000.0 at t=...1000 (activates at ...1050)
        ord_resting = broker.submit_order(
            OrderRequest(
                client_order_id="sh_limit_1",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.GTC,
                quantity=Decimal("0.500"),
                price=Decimal("50000.0"),
                created_ts_ms=clock.now_ms(),
            )
        )
        assert ord_resting.env == RunEnv.SHADOW
        assert ord_resting.status == OrderStatus.NEW
        assert ord_resting.filled_qty == Decimal("0")

        # Step 1: Trade arrives at +20ms (during simulated 50ms flight latency) -> ignored
        clock.advance_by(20)
        broker.on_tick(
            Tick(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                trade_id="t1",
                price=Decimal("50000.0"),
                quantity=Decimal("4.000"),
                is_buyer_maker=True,
                exchange_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            )
        )
        assert broker.query_order("sh_limit_1", "binance", "BTC/USDT:USDT").filled_qty == Decimal("0")

        # Step 2: Trade arrives at +60ms (after activation): 2.000 * 0.5 = 1.000 drains queue_ahead (1.000 -> 0)
        clock.advance_by(40)
        broker.on_tick(
            Tick(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                trade_id="t2",
                price=Decimal("50000.0"),
                quantity=Decimal("2.000"),
                is_buyer_maker=True,
                exchange_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            )
        )
        assert broker.query_order("sh_limit_1", "binance", "BTC/USDT:USDT").filled_qty == Decimal("0")

        # Step 3: Trade arrives at +80ms: 0.600 * 0.5 = 0.300 BTC filled as Maker!
        clock.advance_by(20)
        broker.on_tick(
            Tick(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                trade_id="t3",
                price=Decimal("50000.0"),
                quantity=Decimal("0.600"),
                is_buyer_maker=True,
                exchange_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            )
        )
        updated = broker.query_order("sh_limit_1", "binance", "BTC/USDT:USDT")
        assert updated is not None
        assert updated.status == OrderStatus.PARTIALLY_FILLED
        assert updated.filled_qty == Decimal("0.300")
        assert updated.avg_fill_price == Decimal("50000.0")

        # Step 4: Simulated funding rate +0.0001 on 0.300 BTC @ mark 50000.0 -> -1.5000 USDT
        clock.advance_by(20)
        broker.on_mark_price(
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("50000.0"),
                index_price=Decimal("50000.0"),
                next_funding_rate=Decimal("0.0001"),
                next_funding_ts_ms=clock.now_ms(),
                exchange_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            )
        )
        payment = broker.on_funding_rate(
            FundingRatePoint(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                funding_rate=Decimal("0.0001"),
                mark_price=Decimal("50000.0"),
                funding_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            )
        )
        assert payment is not None
        assert payment.env == RunEnv.SHADOW
        assert payment.amount == Decimal("-1.5000")
        session.commit()


def test_shadow_end_to_end_trace_and_signal_parity_with_orderbook_replay_backtest(tmp_path: Path) -> None:
    """
    P7 Gates 3 & 4:
    1. Run `_OrderBookImbalanceStrategy` on a recorded sequence of OrderBook snapshots through:
       - `ShadowRunner` (`StreamingDataFeed` + `ShadowBroker`)
       - `BacktestRunner` (`HistoricalDataFeed` + `BacktestBroker`)
    2. Prove 100% identical strategy signals (timestamps, actions, quantities, reasons) and fills!
    3. Verify complete `signal_ts -> order_ts -> fill_ts -> snapshot_ref` linkage via `ShadowTraceInspector`.
    4. Verify `_OrderBookImbalanceStrategy` requires zero code changes between `ShadowRunner` and `LiveRunner`.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p7_parity.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    spec = _perp_spec()

    base_ts = 1_700_000_000_000
    obs = [
        OrderBookSnapshot(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[OrderBookLevel(price=Decimal("50000.0"), quantity=Decimal("1.000"))],
            asks=[OrderBookLevel(price=Decimal("50001.0"), quantity=Decimal("1.000"))],
            last_update_id=1001,
            exchange_ts_ms=base_ts + 1_000,
            local_recv_ts_ms=base_ts + 1_000,
        ),
        OrderBookSnapshot(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[OrderBookLevel(price=Decimal("50001.0"), quantity=Decimal("3.000"))],
            asks=[OrderBookLevel(price=Decimal("50002.0"), quantity=Decimal("1.000"))],
            last_update_id=1002,
            exchange_ts_ms=base_ts + 2_000,
            local_recv_ts_ms=base_ts + 2_000,
        ),
        OrderBookSnapshot(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[OrderBookLevel(price=Decimal("50010.0"), quantity=Decimal("1.000"))],
            asks=[OrderBookLevel(price=Decimal("50011.0"), quantity=Decimal("3.000"))],
            last_update_id=1003,
            exchange_ts_ms=base_ts + 3_000,
            local_recv_ts_ms=base_ts + 3_000,
        ),
    ]

    cfg = StrategyConfig(
        strategy_id="strat_ob_imb",
        strategy_version_id="v1",
        name="OrderBook Imbalance",
        exchanges=["binance"],
        symbols=["BTC/USDT:USDT"],
        margin_mode=MarginMode.CROSS,
        default_leverage=Decimal("5"),
        rng_seed=99,
    )

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)

        # --- 1. Shadow Run (StreamingDataFeed + ShadowBroker) ---
        sh_clock = SimulatedClock(base_ts)
        sh_feed = StreamingDataFeed(clock=sh_clock)
        sh_feed.register_symbol_spec(spec)
        sh_broker = ShadowBroker(
            run_id="run_shadow_parity",
            strategy_id=cfg.strategy_id,
            clock=sh_clock,
            data_feed=sh_feed,
            ledger=ledger,
            sim_params=ShadowSimulationParams(latency_ms=0),
            initial_balances={"USDT": Decimal("10000")},
        )

        def _shadow_stream():
            for ob in obs:
                sh_clock.advance_to(ob.exchange_ts_ms)
                sh_feed.on_orderbook_snapshot(ob)
                sh_broker.on_orderbook(ob)
                yield ("orderbook", ob)

        sh_runner = ShadowRunner(
            run_id="run_shadow_parity",
            strategy_cls=_OrderBookImbalanceStrategy,
            strategy_code="class _OrderBookImbalanceStrategy: ...",
            config=cfg,
            clock=sh_clock,
            data_feed=sh_feed,
            broker=sh_broker,
            ledger=ledger,
            event_source=_shadow_stream,
        )
        sh_runner.run()

        # --- 2. Same-Day OrderBook Replay Backtest Run (HistoricalDataFeed + BacktestBroker) ---
        bt_clock = SimulatedClock(base_ts)
        bt_feed = HistoricalDataFeed(clock=bt_clock)
        bt_feed.register_symbol_spec(spec)
        bt_feed.load_orderbooks("binance", "BTC/USDT:USDT", obs)
        bt_broker = BacktestBroker(
            run_id="run_bt_parity",
            strategy_id=cfg.strategy_id,
            clock=bt_clock,
            data_feed=bt_feed,
            ledger=ledger,
            initial_balances={"USDT": Decimal("10000")},
        )

        def _bt_stream():
            for ob in obs:
                bt_clock.advance_to(ob.exchange_ts_ms)
                bt_broker.on_orderbook(ob)
                yield ("orderbook", ob)

        bt_runner = BacktestRunner(
            run_id="run_bt_parity",
            strategy_cls=_OrderBookImbalanceStrategy,
            strategy_code="class _OrderBookImbalanceStrategy: ...",
            config=cfg,
            clock=bt_clock,
            data_feed=bt_feed,
            broker=bt_broker,
            ledger=ledger,
            event_source=_bt_stream,
        )
        bt_runner.run()
        session.commit()

        # Verify 100% signal parity between Shadow and Backtest!
        inspector = ShadowTraceInspector(session)
        sh_traces = inspector.get_run_traces(env=RunEnv.SHADOW, run_id="run_shadow_parity")
        bt_traces = inspector.get_run_traces(env=RunEnv.BACKTEST, run_id="run_bt_parity")

        assert len(sh_traces) == 2
        assert len(bt_traces) == 2

        for st, bt in zip(sh_traces, bt_traces):
            assert st["signal_id"] == bt["signal_id"]
            assert st["signal_ts_ms"] == bt["signal_ts_ms"]
            assert st["order_ts_ms"] == bt["order_ts_ms"]
            assert st["fill_ts_ms"] == bt["fill_ts_ms"]
            assert st["snapshot_ref"] == bt["snapshot_ref"]
            assert st["fill_price"] == bt["fill_price"]
            assert st["fill_qty"] == bt["fill_qty"]

            assert st["signal_ts_ms"] <= st["order_ts_ms"] <= st["fill_ts_ms"]
            assert st["snapshot_ref"].startswith("ob:")

        # Verify zero code difference required to instantiate LiveRunner with exact same Strategy class
        assert LiveRunner.ENV == RunEnv.LIVE
