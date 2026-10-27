"""P6 Tests (Test-First): Exhaustive OrderStateMachine (64 pairs), 4 fault-injection scenarios, rate limiter, drift detector, and coverage proof of 3-Runner isomorphism."""

from __future__ import annotations

import coverage
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from quant_platform.backtest import BacktestBroker
from quant_platform.core import (
    Bar,
    ContractType,
    Fill,
    LiquidityRole,
    Order,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    RunEnv,
    Signal,
    SignalAction,
    Strategy,
    StrategyConfig,
    SymbolSpec,
    ZERO,
)
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db import UnifiedLedgerRepository
from quant_platform.exceptions import OrderStateTransitionError
from quant_platform.execution import (
    BacktestRunner,
    ExecutionCoordinator,
    LiveRunner,
    OrderIdMapper,
    OrderStateMachine,
    ShadowRunner,
    UnifiedExecutionPipeline,
    WeightRateLimiter,
    detect_state_drift,
    generate_client_order_id,
)


def test_exhaustive_order_state_machine_64_transitions() -> None:
    """Acceptance: Exhaustive 8x8 = 64 transition pairs test on OrderStateMachine."""
    all_states = [
        OrderStatus.PENDING_SUBMIT,
        OrderStatus.NEW,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
        OrderStatus.EXPIRED_IN_MATCH,
    ]
    legal_map: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.PENDING_SUBMIT: {
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.EXPIRED_IN_MATCH,
        },
        OrderStatus.NEW: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.EXPIRED_IN_MATCH,
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        },
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.EXPIRED: set(),
        OrderStatus.EXPIRED_IN_MATCH: set(),
    }

    checked_pairs = 0
    for src in all_states:
        for dst in all_states:
            checked_pairs += 1
            sm = OrderStateMachine(client_order_id=f"cid_{src.value}_{dst.value}", initial_status=src)
            if dst in legal_map[src]:
                assert sm.transition_to(dst) == dst
            else:
                with pytest.raises(OrderStateTransitionError):
                    sm.transition_to(dst)
    assert checked_pairs == 64


class _FaultInjectableExchangeBroker(BacktestBroker):
    """
    Fault-injectable test double wrapping real matching semantics to simulate:
    1) Submit HTTP timeout AFTER exchange actually matched the order;
    2) Duplicate WS order/fill pushes across redundant connections;
    3) Out-of-order WS order pushes (FILLED arriving before PARTIALLY_FILLED and NEW);
    4) WS flash disconnect dropping callbacks while exchange executes fills.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_next_submit_with_timeout = False
        self.drop_ws_callbacks = False
        self.recorded_fills_by_cid: dict[str, list[Fill]] = {}

    def _emit_order_update(self, order: Order) -> None:
        self._orders[order.client_order_id] = order
        if self.drop_ws_callbacks:
            return
        for cb in self._order_callbacks:
            cb(order)

    def _emit_fill(self, fill: Fill) -> None:
        self.recorded_fills_by_cid.setdefault(fill.client_order_id, []).append(fill)
        if self.drop_ws_callbacks:
            return
        for cb in self._fill_callbacks:
            cb(fill)

    def submit_order(self, request: OrderRequest) -> Order:
        if self.fail_next_submit_with_timeout:
            self.fail_next_submit_with_timeout = False
            prev_drop = self.drop_ws_callbacks
            self.drop_ws_callbacks = True
            try:
                _ = super().submit_order(request)
            finally:
                self.drop_ws_callbacks = prev_drop
            raise TimeoutError("Simulated HTTP 504 Gateway Timeout during submit_order")
        return super().submit_order(request)


def _setup_fault_env(db_session: Session, run_id: str) -> tuple[SimulatedClock, HistoricalDataFeed, _FaultInjectableExchangeBroker]:
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
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
        max_leverage=Decimal("50"),
    )
    feed.register_symbol_spec(spec)
    feed.load_orderbooks(
        "binance",
        "BTC/USDT:USDT",
        [
            OrderBookSnapshot(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                bids=[OrderBookLevel(price=Decimal("49990.0"), quantity=Decimal("5"))],
                asks=[OrderBookLevel(price=Decimal("50000.0"), quantity=Decimal("5"))],
                last_update_id=1,
                exchange_ts_ms=t0,
                local_recv_ts_ms=t0,
            )
        ],
    )
    broker = _FaultInjectableExchangeBroker(
        env=RunEnv.LIVE,
        run_id=run_id,
        strategy_id="fault_strat",
        clock=clock,
        data_feed=feed,
        ledger=None,  # Coordinator manages local ledger so we can verify local vs exchange convergence!
        initial_balances={"USDT": Decimal("100000")},
    )
    broker.startup_sync()
    return clock, feed, broker


def test_fault_injection_4_scenarios_converge_to_exchange_truth(db_session: Session) -> None:
    """
    Acceptance: Fault injection covering all 4 scenarios:
    1) Submit timeout when order actually FILLED on exchange;
    2) Duplicate order & fill callbacks across redundant connections;
    3) Out-of-order reports (FILLED arrives before PARTIALLY_FILLED and NEW);
    4) Connection flash-disconnect dropping WS pushes, recovered via reconnect sync.
    """
    clock, _, broker = _setup_fault_env(db_session, "run_fault_4")
    ledger = UnifiedLedgerRepository(db_session)
    mapper = OrderIdMapper(db_session)
    coord = ExecutionCoordinator(
        env=RunEnv.LIVE,
        run_id="run_fault_4",
        strategy_id="fault_strat",
        broker=broker,
        ledger=ledger,
        id_mapper=mapper,
        clock=clock,
    )

    # --- Scenario 1: Submit timeout while exchange actually FILLED ---
    cid1 = generate_client_order_id("run_fault_4", "fault_strat", 1)
    broker.fail_next_submit_with_timeout = True
    recovered_ord = coord.submit_order(
        OrderRequest(
            client_order_id=cid1,
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.200"),
            leverage=Decimal("5"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert recovered_ord.status == OrderStatus.FILLED
    assert recovered_ord.filled_qty == Decimal("0.200")
    assert mapper.get_exchange_order_id(cid1) == recovered_ord.exchange_order_id
    assert mapper.get_client_order_id("binance", recovered_ord.exchange_order_id or "") == cid1

    # --- Scenario 2: Duplicate fill & order updates pushed 3 times across redundant WS channels ---
    dup_fill = Fill(
        fill_id="fill_dup_001",
        env=RunEnv.LIVE,
        run_id="run_fault_4",
        strategy_id="fault_strat",
        client_order_id=cid1,
        exchange_order_id=recovered_ord.exchange_order_id,
        exchange_trade_id="trd_dup_999",
        exchange="binance",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        liquidity=LiquidityRole.TAKER,
        price=Decimal("50000.0"),
        quantity=Decimal("0.200"),
        quote_qty=Decimal("10000.0"),
        reference_price=Decimal("50000.0"),
        fee=Decimal("4.0"),
        fee_asset="USDT",
        exchange_ts_ms=clock.now_ms(),
        local_recv_ts_ms=clock.now_ms(),
    )
    for _ in range(3):
        coord.on_fill_report(dup_fill)
        coord.on_order_report(recovered_ord)

    stored_fills_1 = [f for f in ledger.list_fills(RunEnv.LIVE, "run_fault_4") if f.exchange_trade_id == "trd_dup_999"]
    assert len(stored_fills_1) == 1

    # --- Scenario 3: Out-of-order reports (FILLED arrives BEFORE PARTIALLY_FILLED and NEW) ---
    cid3 = generate_client_order_id("run_fault_4", "fault_strat", 3)
    t_now = clock.now_ms()
    ord_filled_msg = Order(
        env=RunEnv.LIVE,
        run_id="run_fault_4",
        strategy_id="fault_strat",
        client_order_id=cid3,
        exchange_order_id="ex_ooo_3",
        exchange="binance",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("49900.0"),
        quantity=Decimal("1.000"),
        filled_qty=Decimal("1.000"),
        avg_fill_price=Decimal("49900.0"),
        remaining_qty=ZERO,
        status=OrderStatus.FILLED,
        created_ts_ms=t_now,
        updated_ts_ms=t_now + 30,
        exchange_ts_ms=t_now + 30,
        local_recv_ts_ms=t_now + 35,
    )
    ord_partial_msg = ord_filled_msg.model_copy(
        update={
            "filled_qty": Decimal("0.400"),
            "remaining_qty": Decimal("0.600"),
            "status": OrderStatus.PARTIALLY_FILLED,
            "updated_ts_ms": t_now + 20,
            "exchange_ts_ms": t_now + 20,
        }
    )
    ord_new_msg = ord_filled_msg.model_copy(
        update={
            "filled_qty": ZERO,
            "avg_fill_price": ZERO,
            "remaining_qty": Decimal("1.000"),
            "status": OrderStatus.NEW,
            "updated_ts_ms": t_now + 10,
            "exchange_ts_ms": t_now + 10,
        }
    )
    # Deliver FILLED first, then delayed PARTIALLY_FILLED, then delayed NEW!
    coord.on_order_report(ord_filled_msg)
    coord.on_order_report(ord_partial_msg)
    coord.on_order_report(ord_new_msg)

    local_ord3 = coord.get_local_order(cid3)
    assert local_ord3 is not None
    assert local_ord3.status == OrderStatus.FILLED
    assert local_ord3.filled_qty == Decimal("1.000")

    # --- Scenario 4: WS connection flash-disconnect while resting order fills on exchange ---
    cid4 = generate_client_order_id("run_fault_4", "fault_strat", 4)
    resting_ord = coord.submit_order(
        OrderRequest(
            client_order_id=cid4,
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("49950.0"),
            quantity=Decimal("0.300"),
            leverage=Decimal("5"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert resting_ord.status == OrderStatus.NEW

    # Simulate WS disconnect: exchange matches resting_ord @ 49950.0 while local misses push!
    broker.drop_ws_callbacks = True
    clock.advance_to(t_now + 1000)
    broker.on_orderbook(
        OrderBookSnapshot(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[OrderBookLevel(price=Decimal("49940.0"), quantity=Decimal("5"))],
            asks=[OrderBookLevel(price=Decimal("49945.0"), quantity=Decimal("5"))],
            last_update_id=2,
            exchange_ts_ms=clock.now_ms(),
            local_recv_ts_ms=clock.now_ms(),
        )
    )
    broker.drop_ws_callbacks = False

    # Drift detector spots the mismatch BEFORE reconnect recovery!
    drifts_before = detect_state_drift(coord, broker)
    assert len(drifts_before) >= 1

    # Reconnect recovery brings local state into 100% agreement with exchange!
    coord.recover_after_disconnect()
    db_session.commit()

    drifts_after = detect_state_drift(coord, broker)
    assert drifts_after == []
    recovered_4 = coord.get_local_order(cid4)
    assert recovered_4 is not None and recovered_4.status == OrderStatus.FILLED


def test_rate_limiter_weight_scheduling_and_429_backoff() -> None:
    """Rate limiter tracks request weight budget and backs off on HTTP 429."""
    fake_now = [1000.0]
    slept: list[float] = []

    def _sleep(sec: float) -> None:
        slept.append(sec)
        fake_now[0] += sec

    limiter = WeightRateLimiter(
        max_weight_per_window=10,
        window_seconds=1.0,
        time_fn=lambda: fake_now[0],
        sleep_fn=_sleep,
    )
    limiter.acquire(weight=6)
    limiter.acquire(weight=4)
    assert slept == []
    # Next weight=5 exceeds 10 within window => sleeps ~1.0s to clear window
    limiter.acquire(weight=5)
    assert len(slept) == 1 and slept[0] >= 1.0

    backoff = limiter.record_rate_limit_hit(attempt=2, base_backoff_sec=0.25)
    assert backoff == 0.5
    assert limiter.throttle_count >= 2


class _IsomorphicTestStrategy(Strategy):
    def on_start(self):
        self.count = 0
        return None

    def on_bar(self, bar: Bar):
        self.count += 1
        if self.count == 1:
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    quantity=Decimal("0.100"),
                    order_type=OrderType.MARKET,
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if self.count == 2:
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    quantity=Decimal("0.100"),
                    order_type=OrderType.MARKET,
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None


def test_coverage_proof_three_runners_execute_identical_strategy_and_pipeline_lines(db_session: Session) -> None:
    """
    Acceptance & Red Line 2:
    Use coverage.Coverage to prove BacktestRunner, ShadowRunner, and LiveRunner execute
    the EXACT SAME lines in Strategy and UnifiedExecutionPipeline (only adapters diverge).
    """
    import inspect
    import quant_platform.execution.runners as runners_mod

    pipeline_src = inspect.getsource(UnifiedExecutionPipeline)
    assert "BACKTEST" not in pipeline_src
    assert '"backtest"' not in pipeline_src
    assert "'backtest'" not in pipeline_src

    t0 = 1700000000000
    bars = [
        Bar(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            interval="1m",
            open_time_ms=t0,
            close_time_ms=t0 + 59_999,
            open=Decimal("50000"),
            high=Decimal("50100"),
            low=Decimal("49900"),
            close=Decimal("50000"),
            volume=Decimal("10"),
            is_closed=True,
            exchange_ts_ms=t0 + 59_999,
            local_recv_ts_ms=t0 + 59_999,
        ),
        Bar(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            interval="1m",
            open_time_ms=t0 + 60_000,
            close_time_ms=t0 + 119_999,
            open=Decimal("50000"),
            high=Decimal("51100"),
            low=Decimal("50000"),
            close=Decimal("51000"),
            volume=Decimal("10"),
            is_closed=True,
            exchange_ts_ms=t0 + 119_999,
            local_recv_ts_ms=t0 + 119_999,
        ),
    ]
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

    executed_lines_by_runner: dict[str, tuple[set[int], set[int]]] = {}
    this_file = inspect.getsourcefile(_IsomorphicTestStrategy)
    runners_file = inspect.getsourcefile(runners_mod)
    assert this_file is not None and runners_file is not None

    strat_lines_range = inspect.getsourcelines(_IsomorphicTestStrategy)
    strat_start, strat_end = strat_lines_range[1], strat_lines_range[1] + len(strat_lines_range[0])
    pipe_lines_range = inspect.getsourcelines(UnifiedExecutionPipeline)
    pipe_start, pipe_end = pipe_lines_range[1], pipe_lines_range[1] + len(pipe_lines_range[0])

    for runner_cls, env in [
        (BacktestRunner, RunEnv.BACKTEST),
        (ShadowRunner, RunEnv.SHADOW),
        (LiveRunner, RunEnv.LIVE),
    ]:
        clock = SimulatedClock(t0)
        feed = HistoricalDataFeed(clock, source=f"iso_{env.value}")
        feed.register_symbol_spec(spec)
        feed.load_bars("binance", "BTC/USDT:USDT", "1m", bars)

        cfg = StrategyConfig(
            strategy_id="iso_strat",
            strategy_version_id="iso_strat:v1",
            name="Iso Strat",
            exchanges=["binance"],
            symbols=["BTC/USDT:USDT"],
            bar_interval="1m",
            initial_balances={"USDT": Decimal("10000")},
        )
        broker = BacktestBroker(
            env=env,
            run_id=f"run_iso_{env.value}",
            strategy_id="iso_strat",
            clock=clock,
            data_feed=feed,
            ledger=UnifiedLedgerRepository(db_session),
            initial_balances={"USDT": Decimal("10000")},
        )
        runner = runner_cls(
            run_id=f"run_iso_{env.value}",
            strategy_cls=_IsomorphicTestStrategy,
            strategy_code="class _IsomorphicTestStrategy(Strategy): pass",
            config=cfg,
            clock=clock,
            data_feed=feed,
            broker=broker,
            ledger=UnifiedLedgerRepository(db_session),
            event_source=lambda b=bars, brk=broker, clk=clock: _yield_bar_events(b, brk, clk),
        )

        cov = coverage.Coverage(include=[this_file, runners_file])
        cov.start()
        rec = runner.run()
        cov.stop()
        assert rec.status.value == "COMPLETED"

        cov_data = cov.get_data()
        s_lines = {ln for ln in (cov_data.lines(this_file) or []) if strat_start <= ln <= strat_end}
        p_lines = {ln for ln in (cov_data.lines(runners_file) or []) if pipe_start <= ln <= pipe_end}
        executed_lines_by_runner[env.value] = (s_lines, p_lines)

    assert len(executed_lines_by_runner["backtest"][0]) > 0
    assert executed_lines_by_runner["backtest"][0] == executed_lines_by_runner["shadow"][0] == executed_lines_by_runner["live"][0]
    assert executed_lines_by_runner["backtest"][1] == executed_lines_by_runner["shadow"][1] == executed_lines_by_runner["live"][1]


def _yield_bar_events(bars: list[Bar], broker: BacktestBroker, clock: SimulatedClock):
    for b in bars:
        clock.advance_to(b.close_time_ms)
        broker.on_bar(b)
        yield ("bar", b)
