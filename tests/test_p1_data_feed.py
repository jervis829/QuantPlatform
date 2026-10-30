"""P1 Tests (Test-First): DataFeed lookahead guard, gap/out-of-order handling, orderbook delta reconstruction, clock skew blocking, dataset hashing, and cross-exchange isolation."""

from __future__ import annotations

from decimal import Decimal
import pytest

from quant_platform.core import (
    Bar,
    ContractType,
    FundingRatePoint,
    MarkIndexPrice,
    OrderBookDelta,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolSpec,
    Tick,
    compute_run_id,
)
from quant_platform.data import (
    ExchangeTimeSynchronizer,
    HistoricalDataFeed,
    OrderBookMaintainer,
    SimulatedClock,
    StreamingDataFeed,
    SyncedLiveClock,
    TimeSyncGuardedBroker,
)
from quant_platform.core.interfaces import NullBrokerAdapter
from quant_platform.exceptions import (
    ClockSkewError,
    CrossExchangeIsolationError,
    DataIntegrityError,
    LookaheadError,
)


def _make_btc_spec() -> SymbolSpec:
    return SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        version=1,
        effective_from_ms=1700000000000,
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("50"),
    )


def _bar(open_ms: int, close_price: str, exchange: str = "binance", is_closed: bool = True) -> Bar:
    return Bar(
        exchange=exchange,
        symbol="BTC/USDT:USDT",
        interval="1m",
        open_time_ms=open_ms,
        close_time_ms=open_ms + 59_999,
        open=Decimal(close_price),
        high=Decimal(close_price) + Decimal("10"),
        low=Decimal(close_price) - Decimal("10"),
        close=Decimal(close_price),
        volume=Decimal("15.5"),
        quote_volume=Decimal(close_price) * Decimal("15.5"),
        trade_count=120,
        is_closed=is_closed,
        exchange_ts_ms=open_ms + 59_999,
        local_recv_ts_ms=open_ms + 60_005,
    )


def test_lookahead_guard_raises_on_future_timestamp_and_hides_unclosed_bars() -> None:
    """Acceptance ①: At time t, requesting data at t+1 raises LookaheadError; unclosed bar is never leaked as closed."""
    t0 = 1700000000000
    clock = SimulatedClock(t0 + 60_000)  # t = t0 + 60_000 (bar 0 closed at +59_999, bar 1 closes at +119_999)
    feed = HistoricalDataFeed(clock=clock, source="unit_test")
    feed.register_symbol_spec(_make_btc_spec())
    feed.load_bars(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        interval="1m",
        bars=[
            _bar(t0, "60000.0"),
            _bar(t0 + 60_000, "60100.0"),
            _bar(t0 + 120_000, "60200.0"),
        ],
    )
    feed.load_ticks(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        ticks=[
            Tick(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                trade_id="t1",
                price=Decimal("60000.0"),
                quantity=Decimal("0.1"),
                seq_id=1,
                exchange_ts_ms=t0 + 10_000,
                local_recv_ts_ms=t0 + 10_004,
            ),
            Tick(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                trade_id="t2",
                price=Decimal("60150.0"),
                quantity=Decimal("0.2"),
                seq_id=2,
                exchange_ts_ms=t0 + 90_000,
                local_recv_ts_ms=t0 + 90_003,
            ),
        ],
    )

    # Requesting t + 1 ms MUST raise LookaheadError
    with pytest.raises(LookaheadError):
        feed.get_bars("binance", "BTC/USDT:USDT", "1m", end_ts_ms=clock.now_ms() + 1)

    with pytest.raises(LookaheadError):
        feed.get_ticks("binance", "BTC/USDT:USDT", end_ts_ms=clock.now_ms() + 1)

    with pytest.raises(LookaheadError):
        feed.get_orderbook("binance", "BTC/USDT:USDT", at_ts_ms=clock.now_ms() + 1)

    with pytest.raises(LookaheadError):
        feed.get_mark_price("binance", "BTC/USDT:USDT", at_ts_ms=clock.now_ms() + 1)

    # At t = t0 + 60_000, only bar 0 (close_time_ms = t0 + 59_999) is closed!
    visible_bars = feed.get_bars("binance", "BTC/USDT:USDT", "1m")
    assert len(visible_bars) == 1
    assert visible_bars[0].open_time_ms == t0
    assert visible_bars[0].exchange_ts_ms == t0 + 59_999
    assert visible_bars[0].local_recv_ts_ms == t0 + 60_005

    # Only tick t1 (at t0 + 10_000 <= t0 + 60_000) is visible
    visible_ticks = feed.get_ticks("binance", "BTC/USDT:USDT")
    assert [tk.trade_id for tk in visible_ticks] == ["t1"]


def test_kline_gap_and_out_of_order_ticks_backfill_or_raise() -> None:
    """Acceptance ②: K-line gaps & out-of-order/gapped ticks must either backfill via REST or raise DataIntegrityError (never silently skip)."""
    t0 = 1700000000000
    clock = SimulatedClock(t0 + 300_000)
    feed = HistoricalDataFeed(clock=clock)

    # 1. Missing bar at t0 + 60_000 without backfiller -> MUST raise DataIntegrityError
    gapped_bars = [_bar(t0, "60000.0"), _bar(t0 + 120_000, "60200.0")]
    with pytest.raises(DataIntegrityError) as exc_info:
        feed.load_bars("binance", "BTC/USDT:USDT", "1m", gapped_bars)
    assert "gap" in exc_info.value.message.lower()

    # 2. Missing bar WITH REST backfiller -> patches missing range & deduplicates cleanly
    def rest_bar_backfiller(exchange: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        return [
            _bar(t0, "60000.0"),  # duplicate boundary to test deduplication
            _bar(t0 + 60_000, "60100.0"),
        ]

    feed.load_bars(
        "binance",
        "BTC/USDT:USDT",
        "1m",
        gapped_bars,
        rest_backfiller=rest_bar_backfiller,
    )
    repaired_bars = feed.get_bars("binance", "BTC/USDT:USDT", "1m")
    assert [b.open_time_ms for b in repaired_bars] == [t0, t0 + 60_000, t0 + 120_000]

    # 3. Out-of-order ticks & sequence gap without repair -> MUST raise DataIntegrityError
    bad_ticks = [
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="10",
            price=Decimal("60000"),
            quantity=Decimal("0.1"),
            seq_id=10,
            exchange_ts_ms=t0 + 2000,
            local_recv_ts_ms=t0 + 2005,
        ),
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="9",
            price=Decimal("59999"),
            quantity=Decimal("0.1"),
            seq_id=9,
            exchange_ts_ms=t0 + 1000,
            local_recv_ts_ms=t0 + 2010,
        ),
    ]
    with pytest.raises(DataIntegrityError):
        feed.load_ticks("binance", "BTC/USDT:USDT", bad_ticks, allow_reorder=False)

    # 4. WebSocket sequence gap (seq 1 -> seq 3) repaired via REST tick backfiller + deduplicated
    gapped_ticks = [
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="1",
            price=Decimal("60000"),
            quantity=Decimal("0.1"),
            seq_id=1,
            exchange_ts_ms=t0 + 1000,
            local_recv_ts_ms=t0 + 1002,
        ),
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="3",
            price=Decimal("60002"),
            quantity=Decimal("0.3"),
            seq_id=3,
            exchange_ts_ms=t0 + 3000,
            local_recv_ts_ms=t0 + 3002,
        ),
    ]
    with pytest.raises(DataIntegrityError):
        feed.load_ticks("binance", "BTC/USDT:USDT", gapped_ticks)

    def rest_tick_backfiller(exchange: str, symbol: str, from_seq: int, to_seq: int) -> list[Tick]:
        assert (from_seq, to_seq) == (2, 2)
        return [
            Tick(
                exchange=exchange,
                symbol=symbol,
                trade_id="2",
                price=Decimal("60001"),
                quantity=Decimal("0.2"),
                seq_id=2,
                exchange_ts_ms=t0 + 2000,
                local_recv_ts_ms=t0 + 3010,
            )
        ]

    feed.load_ticks(
        "binance",
        "BTC/USDT:USDT",
        gapped_ticks,
        rest_backfiller=rest_tick_backfiller,
    )
    repaired_ticks = feed.get_ticks("binance", "BTC/USDT:USDT")
    assert [tk.seq_id for tk in repaired_ticks] == [1, 2, 3]


def test_orderbook_snapshot_plus_deltas_matches_exchange_snapshot() -> None:
    """Acceptance ③: OrderBook snapshot + WS delta sequence rebuild matches authoritative exchange snapshot."""
    maintainer = OrderBookMaintainer(exchange="binance", symbol="BTC/USDT:USDT")

    # Buffer WS deltas arriving while REST snapshot is in flight:
    # Delta 1: stale (u=100 <= lastUpdateId=100), should be dropped
    maintainer.push_delta(
        OrderBookDelta(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            first_update_id=98,
            final_update_id=100,
            bids=[OrderBookLevel(price=Decimal("59990.0"), quantity=Decimal("9.9"))],
            asks=[],
            exchange_ts_ms=1000,
            local_recv_ts_ms=1002,
        )
    )
    # Delta 2: bridging delta U=100 <= 101 <= u=102
    maintainer.push_delta(
        OrderBookDelta(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            first_update_id=100,
            final_update_id=102,
            bids=[
                OrderBookLevel(price=Decimal("60000.0"), quantity=Decimal("2.5")),  # update bid
                OrderBookLevel(price=Decimal("59999.0"), quantity=Decimal("0")),    # remove level
            ],
            asks=[
                OrderBookLevel(price=Decimal("60001.0"), quantity=Decimal("0")),    # remove best ask
                OrderBookLevel(price=Decimal("60001.5"), quantity=Decimal("1.2")),  # new best ask
            ],
            exchange_ts_ms=1010,
            local_recv_ts_ms=1012,
        )
    )

    initial_snapshot = OrderBookSnapshot(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        bids=[
            OrderBookLevel(price=Decimal("60000.0"), quantity=Decimal("1.0")),
            OrderBookLevel(price=Decimal("59999.0"), quantity=Decimal("3.0")),
            OrderBookLevel(price=Decimal("59998.0"), quantity=Decimal("4.0")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("60001.0"), quantity=Decimal("1.5")),
            OrderBookLevel(price=Decimal("60002.0"), quantity=Decimal("2.0")),
        ],
        last_update_id=100,
        exchange_ts_ms=1005,
        local_recv_ts_ms=1008,
    )
    maintainer.apply_snapshot(initial_snapshot)

    # Delta 3: contiguous u=103 (prev_final_update_id=102)
    maintainer.push_delta(
        OrderBookDelta(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            first_update_id=103,
            final_update_id=103,
            prev_final_update_id=102,
            bids=[OrderBookLevel(price=Decimal("60000.5"), quantity=Decimal("0.8"))],
            asks=[OrderBookLevel(price=Decimal("60002.0"), quantity=Decimal("3.5"))],
            exchange_ts_ms=1020,
            local_recv_ts_ms=1022,
        )
    )

    rebuilt = maintainer.current_snapshot()
    expected_exchange_snapshot = OrderBookSnapshot(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        bids=[
            OrderBookLevel(price=Decimal("60000.5"), quantity=Decimal("0.8")),
            OrderBookLevel(price=Decimal("60000.0"), quantity=Decimal("2.5")),
            OrderBookLevel(price=Decimal("59998.0"), quantity=Decimal("4.0")),
        ],
        asks=[
            OrderBookLevel(price=Decimal("60001.5"), quantity=Decimal("1.2")),
            OrderBookLevel(price=Decimal("60002.0"), quantity=Decimal("3.5")),
        ],
        last_update_id=103,
        exchange_ts_ms=1020,
        local_recv_ts_ms=1022,
    )
    assert rebuilt.last_update_id == expected_exchange_snapshot.last_update_id
    assert rebuilt.bids == expected_exchange_snapshot.bids
    assert rebuilt.asks == expected_exchange_snapshot.asks

    # Gap injection (103 -> 106 without snapshot recovery) MUST raise DataIntegrityError
    with pytest.raises(DataIntegrityError):
        maintainer.push_delta(
            OrderBookDelta(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                first_update_id=106,
                final_update_id=106,
                prev_final_update_id=105,
                bids=[OrderBookLevel(price=Decimal("60000.0"), quantity=Decimal("1.0"))],
                asks=[],
                exchange_ts_ms=1030,
                local_recv_ts_ms=1032,
            )
        )


def test_clock_offset_5_seconds_blocks_order_and_emits_alert() -> None:
    """Acceptance ④: Injecting a 5000ms (5s) server time offset triggers alert and blocks order submission."""
    alerts: list[dict[str, object]] = []
    local_now = 1700000000000

    # Inject 5,000 ms server offset
    synchronizer = ExchangeTimeSynchronizer(
        max_skew_ms=1000,
        local_time_fn=lambda: local_now,
        server_time_fetchers={"binance": lambda: local_now + 5000},
        alert_callbacks=[alerts.append],
    )
    offset = synchronizer.sync("binance")
    assert offset == 5000
    assert len(alerts) == 1
    assert alerts[0]["code"] == "CLOCK_SKEW_ALERT"
    assert alerts[0]["offset_ms"] == 5000

    clock = SyncedLiveClock(synchronizer=synchronizer, local_time_fn=lambda: local_now)
    assert clock.synced_exchange_now_ms("binance") == local_now + 5000

    inner_broker = NullBrokerAdapter()
    inner_broker.startup_sync()
    guarded_broker = TimeSyncGuardedBroker(inner=inner_broker, synchronizer=synchronizer)

    with pytest.raises(ClockSkewError) as exc_info:
        guarded_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("60000"),
                quantity=Decimal("0.01"),
            )
        )
    assert exc_info.value.details["offset_ms"] == 5000


def test_dataset_version_hash_and_cross_exchange_price_isolation() -> None:
    """Multi-exchange price isolation + dataset content hash feeds into run_id."""
    t0 = 1700000000000
    clock = SimulatedClock(t0 + 180_000)
    feed = HistoricalDataFeed(clock=clock, source="archive_v1")

    # Mixing binance and okx bars in one series MUST raise CrossExchangeIsolationError
    with pytest.raises(CrossExchangeIsolationError):
        feed.load_bars(
            "binance",
            "BTC/USDT:USDT",
            "1m",
            [_bar(t0, "60000.0", exchange="binance"), _bar(t0 + 60_000, "60050.0", exchange="okx")],
        )

    feed.load_bars("binance", "BTC/USDT:USDT", "1m", [_bar(t0, "60000.0", exchange="binance")])
    feed.load_bars("okx", "BTC/USDT:USDT", "1m", [_bar(t0, "60088.0", exchange="okx")])
    feed.load_mark_prices(
        "binance",
        "BTC/USDT:USDT",
        [
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("60005.0"),
                index_price=Decimal("60002.0"),
                exchange_ts_ms=t0 + 1000,
                local_recv_ts_ms=t0 + 1003,
            )
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
                mark_price=Decimal("60005.0"),
                funding_ts_ms=t0,
                local_recv_ts_ms=t0 + 5,
            )
        ],
    )

    assert feed.get_bars("binance", "BTC/USDT:USDT", "1m")[0].close == Decimal("60000.0")
    assert feed.get_bars("okx", "BTC/USDT:USDT", "1m")[0].close == Decimal("60088.0")
    assert feed.get_mark_price("binance", "BTC/USDT:USDT") is not None
    assert feed.get_mark_price("okx", "BTC/USDT:USDT") is None
    assert len(feed.get_funding_rates("binance", "BTC/USDT:USDT")) == 1

    ds_ver_1 = feed.dataset_version()
    assert ds_ver_1.startswith("ds_")

    # Streaming unclosed K-line explicit flag test
    s_clock = SimulatedClock(t0 + 30_000)
    s_feed = StreamingDataFeed(clock=s_clock)
    s_feed.on_bar(_bar(t0, "60012.0", is_closed=False))
    assert s_feed.get_bars("binance", "BTC/USDT:USDT", "1m", include_unclosed=False) == []
    unclosed = s_feed.get_bars("binance", "BTC/USDT:USDT", "1m", include_unclosed=True)
    assert len(unclosed) == 1 and unclosed[0].is_closed is False

    run_id = compute_run_id(
        strategy_code="class S(Strategy): pass",
        config_snapshot={"symbol": "BTC/USDT:USDT"},
        dataset_version=ds_ver_1,
        symbol_spec_version="binance:BTC/USDT:USDT:v1",
    )
    assert run_id.startswith("run_")
