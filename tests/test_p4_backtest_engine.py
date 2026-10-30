"""P4 Tests (Test-First): Crypto backtest matching engine — 7 core rules + 5 acceptance criteria with hand calculations."""

from __future__ import annotations

from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from quant_platform.backtest import (
    BacktestBroker,
    BacktestEngine,
    SlippageModelConfig,
    compute_adl_AutoDeleverage_rank,
    estimate_limit_fill_probability,
)
from quant_platform.core import (
    Bar,
    ContractType,
    FeeTier,
    FundingRatePoint,
    LiquidityRole,
    MarginBracket,
    MarginMode,
    MarkIndexPrice,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    RunEnv,
    Signal,
    SignalAction,
    Strategy,
    StrategyConfig,
    SymbolSpec,
    Tick,
    TimeInForce,
)
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db import UnifiedLedgerRepository
from quant_platform.exceptions import ASTSecurityError, LookaheadError
from quant_platform.strategy import ASTSecurityChecker


def _perp_spec(
    symbol: str = "BTC/USDT:USDT",
    contract_type: ContractType = ContractType.PERPETUAL,
    settlement_ts_ms: int | None = None,
    delisted_at_ms: int | None = None,
) -> SymbolSpec:
    return SymbolSpec(
        exchange="binance",
        symbol=symbol,
        version=1,
        effective_from_ms=0,
        contract_type=contract_type,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("1000"),
        min_notional=Decimal("5.0"),
        contract_size=Decimal("1"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        fee_tiers=[
            FeeTier(vip_level=0, maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.0004"), platform_token_discount=Decimal("0.10")),
            FeeTier(vip_level=1, maker_fee=Decimal("0.00016"), taker_fee=Decimal("0.00036"), platform_token_discount=Decimal("0.10")),
        ],
        max_leverage=Decimal("125"),
        margin_brackets=[
            MarginBracket(
                bracket=1,
                notional_floor=Decimal("0"),
                notional_cap=Decimal("500000"),
                max_leverage=Decimal("125"),
                maint_margin_rate=Decimal("0.004"),
                maint_amount=Decimal("0"),
            ),
            MarginBracket(
                bracket=2,
                notional_floor=Decimal("500000"),
                notional_cap=Decimal("5000000"),
                max_leverage=Decimal("50"),
                maint_margin_rate=Decimal("0.005"),
                maint_amount=Decimal("500"),
            ),
        ],
        settlement_ts_ms=settlement_ts_ms,
        delisted_at_ms=delisted_at_ms,
    )


def _spot_spec() -> SymbolSpec:
    return SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT",
        version=1,
        effective_from_ms=0,
        contract_type=ContractType.SPOT,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("1000"),
        min_notional=Decimal("10.0"),
        maker_fee=Decimal("0.0008"),
        taker_fee=Decimal("0.0010"),
        fee_tiers=[
            FeeTier(vip_level=0, maker_fee=Decimal("0.0008"), taker_fee=Decimal("0.0010"), platform_token_discount=Decimal("0.25"))
        ],
        max_leverage=Decimal("5"),
    )


def _ob(ts_ms: int, bids: list[tuple[str, str]], asks: list[tuple[str, str]], symbol: str = "BTC/USDT:USDT") -> OrderBookSnapshot:
    return OrderBookSnapshot(
        exchange="binance",
        symbol=symbol,
        bids=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in bids],
        asks=[OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in asks],
        last_update_id=ts_ms,
        exchange_ts_ms=ts_ms,
        local_recv_ts_ms=ts_ms,
    )


def test_rule1_fees_vip_discount_and_spot_vs_perp_deduction(db_session: Session) -> None:
    """
    Rule 1 Hand Calculation:
    - Perpetual VIP1 (taker=0.00036) + 10% platform discount => effective rate = 0.00036 * 0.90 = 0.000324.
      Buy 2.0 BTC @ 50,000.0 => notional = 100,000 USDT => fee = 32.40 USDT (fee_asset='USDT').
    - Spot VIP0 without platform discount:
      1) Taker BUY 1.0 BTC @ 50,000.0 (taker=0.0010) => fee = 0.001 BTC (fee_asset='BTC'), net BTC = 0.999 BTC.
      2) Maker Limit SELL 0.5 BTC @ 51,000.0 (maker=0.0008) => quote = 25,500 USDT, fee = 20.40 USDT (fee_asset='USDT'),
         net USDT added = 25,479.60 USDT.
    """
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec())
    feed.register_symbol_spec(_spot_spec())
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [_ob(t0, [("49999.0", "5")], [("50000.0", "5")])])
    feed.load_orderbooks("binance", "BTC/USDT", [_ob(t0, [("49999.00", "5")], [("50000.00", "5")], symbol="BTC/USDT")])

    # Part A: Perpetual VIP1 + 10% discount
    perp_broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_fee_perp",
        strategy_id="s_fee",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("100000")},
        vip_level=1,
        use_platform_token_discount=True,
    )
    perp_broker.startup_sync()
    perp_broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("2.000"),
            leverage=Decimal("5"),
            created_ts_ms=t0,
        )
    )
    perp_fills = UnifiedLedgerRepository(db_session).list_fills(RunEnv.BACKTEST, "run_fee_perp")
    assert len(perp_fills) == 1
    assert perp_fills[0].fee == Decimal("32.40")
    assert perp_fills[0].fee_asset == "USDT"

    # Part B: Spot base-fee on BUY, quote-fee on Maker SELL
    spot_broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_fee_spot",
        strategy_id="s_fee",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("60000")},
        vip_level=0,
        use_platform_token_discount=False,
    )
    spot_broker.startup_sync()
    spot_broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.000"),
            margin_mode=MarginMode.CASH,
            created_ts_ms=t0,
        )
    )
    bals = {b.asset: b for b in spot_broker.query_balance("binance")}
    assert bals["BTC"].total == Decimal("0.999")
    assert bals["USDT"].total == Decimal("10000")

    # Place resting Maker SELL 0.5 BTC @ 51,000.00, then cross it via market tick / orderbook move
    sell_ord = spot_broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=Decimal("51000.00"),
            quantity=Decimal("0.500"),
            margin_mode=MarginMode.CASH,
            created_ts_ms=t0,
        )
    )
    assert sell_ord.status == OrderStatus.NEW
    clock.advance_to(t0 + 1000)
    spot_broker.on_orderbook(_ob(t0 + 1000, [("51005.00", "3")], [("51010.00", "3")], symbol="BTC/USDT"))

    bals_after = {b.asset: b for b in spot_broker.query_balance("binance")}
    # 10,000 + (0.5 * 51,000 - 20.40) = 35,479.60 USDT; BTC = 0.999 - 0.500 = 0.499 BTC
    assert bals_after["USDT"].total == Decimal("35479.60")
    assert bals_after["BTC"].total == Decimal("0.499")


def test_rule2_precision_and_min_notional_rejection(db_session: Session) -> None:
    """Acceptance ④ & Rule 2: Precision/minQty/minNotional violations are rejected (never silently rounded)."""
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec())
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [_ob(t0, [("49999.0", "5")], [("50000.0", "5")])])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_prec",
        strategy_id="s_prec",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("10000")},
    )
    broker.startup_sync()

    # 1. Tick violation (50000.05 vs tick 0.10)
    o1 = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000.05"),
            quantity=Decimal("0.010"),
            created_ts_ms=t0,
        )
    )
    assert o1.status == OrderStatus.REJECTED and o1.reject_reason == "SPEC_PRICE_TICK_VIOLATION"

    # 2. Step violation (0.0015 vs step 0.001)
    o2 = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000.0"),
            quantity=Decimal("0.0015"),
            created_ts_ms=t0,
        )
    )
    assert o2.status == OrderStatus.REJECTED and o2.reject_reason == "SPEC_QTY_STEP_VIOLATION"

    # 3. MinNotional violation (price 4000.0 * qty 0.001 = 4.0 < 5.0)
    o3 = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("4000.0"),
            quantity=Decimal("0.001"),
            created_ts_ms=t0,
        )
    )
    assert o3.status == OrderStatus.REJECTED and o3.reject_reason == "SPEC_MIN_NOTIONAL_VIOLATION"


def test_rule3_walk_the_book_slippage_queue_post_only_ioc_fok(db_session: Session) -> None:
    """
    Rule 3 Hand Calculation:
    - OrderBook asks: [(50000.0, 0.4), (50010.0, 0.6), (50050.0, 1.0)] (total = 2.0 BTC)
    - Market BUY 1.6 BTC walks 3 levels:
      0.4 * 50000 + 0.6 * 50010 + 0.6 * 50050 = 20000 + 30006 + 30030 = 80,036.0 USDT
      avg_fill_price = 80036.0 / 1.6 = 50022.5 USDT
      Level 3 slippage_bps = (50050 - 50000) / 50000 * 10000 = 10.0 bps.
    - Post-Only (GTX) BUY @ 50000.0 when best_ask=50000.0 => EXPIRED_IN_MATCH (0 filled).
    - FOK BUY 2.5 BTC when total ask depth=2.0 BTC => EXPIRED (0 filled).
    - IOC BUY 2.5 BTC when total ask depth=2.0 BTC => fills 2.0 BTC, remaining 0.5 expires (status=EXPIRED, filled_qty=2.0).
    - Resting Limit BUY 1.0 BTC @ 49990.0 behind 2.0 BTC bid with queue_ahead_ratio=0.5 (queue_ahead=1.0 BTC):
      Aggressive sell tick of 1.4 BTC @ 49990.0 clears 1.0 BTC queue + fills 0.4 BTC as MAKER!
    """
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec())
    snap = _ob(
        t0,
        bids=[("49990.0", "2.0")],
        asks=[("50000.0", "0.4"), ("50010.0", "0.6"), ("50050.0", "1.0")],
    )
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [snap])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_slip",
        strategy_id="s_slip",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("500000")},
        slippage_config=SlippageModelConfig(queue_ahead_ratio=Decimal("0.5"), trade_participation_rate=Decimal("1.0")),
    )
    broker.startup_sync()

    # 1. Walk the book Market BUY 1.6 BTC
    mkt = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.600"),
            leverage=Decimal("10"),
            created_ts_ms=t0,
        )
    )
    assert mkt.status == OrderStatus.FILLED
    assert mkt.filled_qty == Decimal("1.600")
    assert mkt.avg_fill_price == Decimal("50022.5")
    fills = UnifiedLedgerRepository(db_session).list_fills(RunEnv.BACKTEST, "run_slip")
    assert len(fills) == 3
    assert [f.slippage_bps for f in fills] == [Decimal("0"), Decimal("2"), Decimal("10")]

    # Reset fresh book for GTX, FOK, IOC, Queue tests
    clock.advance_to(t0 + 100)
    snap2 = _ob(
        t0 + 100,
        bids=[("49990.0", "2.0")],
        asks=[("50000.0", "0.8"), ("50010.0", "1.2")],
    )
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [snap, snap2])
    broker.on_orderbook(snap2)

    # 2. Post-Only crossing best_ask=50000.0 -> rejected/expired in match
    gtx = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000.0"),
            quantity=Decimal("0.100"),
            post_only=True,
            leverage=Decimal("10"),
            created_ts_ms=t0 + 100,
        )
    )
    assert gtx.status == OrderStatus.EXPIRED_IN_MATCH
    assert gtx.filled_qty == Decimal("0")

    # 3. FOK exceeding 2.0 BTC ask depth -> 0 fill, EXPIRED
    fok = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.FOK,
            price=Decimal("50020.0"),
            quantity=Decimal("2.500"),
            leverage=Decimal("10"),
            created_ts_ms=t0 + 100,
        )
    )
    assert fok.status == OrderStatus.EXPIRED
    assert fok.filled_qty == Decimal("0")

    # 4. IOC exceeding 2.0 BTC ask depth -> fills 2.0 BTC, cancels 0.5 BTC -> EXPIRED
    ioc = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.IOC,
            price=Decimal("50020.0"),
            quantity=Decimal("2.500"),
            leverage=Decimal("10"),
            created_ts_ms=t0 + 100,
        )
    )
    assert ioc.status == OrderStatus.EXPIRED
    assert ioc.filled_qty == Decimal("2.000")

    # 5. Resting LIMIT BUY @ 49990.0 with queue position (queue_ahead = 2.0 * 0.5 = 1.0 BTC)
    prob = estimate_limit_fill_probability(
        queue_ahead_qty=Decimal("1.0"),
        order_qty=Decimal("1.0"),
        expected_touch_volume=Decimal("1.4"),
    )
    assert prob == Decimal("0.4")

    resting = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("49990.0"),
            quantity=Decimal("1.000"),
            leverage=Decimal("10"),
            created_ts_ms=t0 + 100,
        )
    )
    assert resting.status == OrderStatus.NEW

    clock.advance_to(t0 + 200)
    broker.on_tick(
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="tk_queue",
            price=Decimal("49990.0"),
            quantity=Decimal("1.400"),
            is_buyer_maker=True,
            exchange_ts_ms=t0 + 200,
            local_recv_ts_ms=t0 + 200,
        )
    )
    updated_resting = broker.query_order(resting.client_order_id, "binance", "BTC/USDT:USDT")
    assert updated_resting is not None
    assert updated_resting.status == OrderStatus.PARTIALLY_FILLED
    assert updated_resting.filled_qty == Decimal("0.400")
    assert updated_resting.remaining_qty == Decimal("0.600")


def test_rule4_and_acceptance5_mark_price_liquidation_penalty_adl_and_funding(db_session: Session) -> None:
    """
    Acceptance ⑤ & Rule 4 Hand Calculation:
    - Initial wallet: 10,000.00 USDT
    - Open Isolated Long 1.0 BTC @ 60,000.0 with 10x leverage:
      * Open taker fee (0.0004) = 24.00 USDT
      * Isolated margin allocated = 60,000 / 10 = 6,000.00 USDT
      * Bracket 1: mmr = 0.004, maint_amount = 0
      * Exact Liquidation Mark Price = (60000 - 6000) / (1 - 0.004) = 54,216.867469879518...
    - Step 1 (Last-price wick to 52,000.0 while Mark Price = 58,000.0):
      * MUST NOT liquidate because liquidation uses Mark Price ONLY!
    - Step 2 (Mark Price drops to 54,200.0 <= 54,216.867...):
      * Liquidates immediately at Mark Price = 54,200.0!
      * Price PnL = (54,200 - 60,000) * 1.0 = -5,800.00 USDT
      * Liquidation penalty (rate = 0.002) = 54,200 * 1.0 * 0.002 = 108.40 USDT
      * Residual isolated margin returned = 6,000 - 5,800 - 108.40 = 91.60 USDT
      * Final wallet total = 10,000.00 - 24.00 - 5,800.00 - 108.40 = 4,067.60 USDT!
    """
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec())
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [_ob(t0, [("59999.0", "5")], [("60000.0", "5")])])
    feed.load_mark_prices(
        "binance",
        "BTC/USDT:USDT",
        [
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("60000.0"),
                index_price=Decimal("60000.0"),
                exchange_ts_ms=t0,
                local_recv_ts_ms=t0,
            )
        ],
    )

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_liq",
        strategy_id="s_liq",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("10000")},
        liquidation_penalty_rate=Decimal("0.002"),
    )
    broker.startup_sync()

    broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.000"),
            leverage=Decimal("10"),
            margin_mode=MarginMode.ISOLATED,
            created_ts_ms=t0,
        )
    )
    pos = broker.query_positions("binance", "BTC/USDT:USDT")[0]
    assert pos.quantity == Decimal("1.000")
    assert pos.isolated_margin == Decimal("6000")
    assert pos.liquidation_price is not None
    expected_liq = Decimal("54000") / Decimal("0.996")
    assert abs(pos.liquidation_price - expected_liq) < Decimal("1e-9")

    # Step 1: Last trade tick wicks down to 52,000, Mark Price stays 58,000 -> NO liquidation!
    clock.advance_to(t0 + 1000)
    broker.on_mark_price(
        MarkIndexPrice(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            mark_price=Decimal("58000.0"),
            index_price=Decimal("58000.0"),
            exchange_ts_ms=t0 + 1000,
            local_recv_ts_ms=t0 + 1000,
        )
    )
    broker.on_tick(
        Tick(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            trade_id="wick_last_only",
            price=Decimal("52000.0"),
            quantity=Decimal("0.5"),
            exchange_ts_ms=t0 + 1000,
            local_recv_ts_ms=t0 + 1000,
        )
    )
    assert broker.query_positions("binance", "BTC/USDT:USDT")[0].quantity == Decimal("1.000")

    # Step 2: Mark Price drops to 54,200.0 (<= 54,216.867...) -> LIQUIDATION triggers!
    clock.advance_to(t0 + 2000)
    broker.on_mark_price(
        MarkIndexPrice(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            mark_price=Decimal("54200.0"),
            index_price=Decimal("54200.0"),
            exchange_ts_ms=t0 + 2000,
            local_recv_ts_ms=t0 + 2000,
        )
    )
    pos_after = broker.query_positions("binance", "BTC/USDT:USDT")[0]
    assert pos_after.quantity == Decimal("0")
    bal_after = broker.query_balance("binance")[0]
    assert abs(bal_after.total - Decimal("4067.60")) < Decimal("1e-9")

    # Verify ADL quantile ranking helper
    adl_q = compute_adl_AutoDeleverage_rank(
        unrealized_pnl=Decimal("1200"),
        initial_margin=Decimal("1000"),
        leverage=Decimal("10"),
    )
    assert adl_q == 5


def test_rule5_spot_margin_hourly_borrow_interest(db_session: Session) -> None:
    """
    Rule 5 Hand Calculation:
    - Initial USDT = 10,000, Spot Margin BUY 0.5 BTC @ 50,000.00 (cost = 25,000 USDT)
    - Borrowed USDT = 15,000.00, hourly_borrow_rate = 0.0001 (0.01%/hr)
    - Advance 5 hours (18,000,000 ms) => accrued interest = 15,000 * 0.0001 * 5 = 7.50 USDT!
    """
    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_spot_spec())
    feed.load_orderbooks("binance", "BTC/USDT", [_ob(t0, [("49999.00", "5")], [("50000.00", "5")], symbol="BTC/USDT")])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_borrow",
        strategy_id="s_borrow",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("10000")},
        allow_spot_borrow=True,
        hourly_borrow_rates={"USDT": Decimal("0.0001")},
    )
    broker.startup_sync()
    broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.500"),
            leverage=Decimal("3"),
            margin_mode=MarginMode.CROSS,
            created_ts_ms=t0,
        )
    )
    usdt_bal_0 = {b.asset: b for b in broker.query_balance("binance")}["USDT"]
    assert usdt_bal_0.borrowed == Decimal("15000")

    clock.advance_to(t0 + 5 * 3_600_000)
    broker.accrue_borrow_interest(clock.now_ms())
    usdt_bal_5h = {b.asset: b for b in broker.query_balance("binance")}["USDT"]
    assert usdt_bal_5h.accrued_interest == Decimal("7.50")


def test_rule6_extreme_conditions_maintenance_empty_book_and_delisting(db_session: Session) -> None:
    """Rule 6: Exchange maintenance window blocks trading while keeping position; empty book expires safely; delisting force-settles."""
    t0 = 1700000000000
    t_delist = t0 + 300_000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec(delisted_at_ms=t_delist))
    feed.load_orderbooks("binance", "BTC/USDT:USDT", [_ob(t0, [("49990.0", "2")], [("50000.0", "2")])])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_extreme",
        strategy_id="s_extreme",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("50000")},
        maintenance_windows=[(t0 + 60_000, t0 + 120_000)],
    )
    broker.startup_sync()

    # Open 0.5 BTC position before maintenance
    broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.500"),
            leverage=Decimal("5"),
            created_ts_ms=t0,
        )
    )

    # 1. During maintenance window: new order rejected, position still held!
    clock.advance_to(t0 + 90_000)
    maint_ord = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.500"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert maint_ord.status == OrderStatus.REJECTED
    assert maint_ord.reject_reason == "EXCHANGE_IN_MAINTENANCE"
    assert broker.query_positions("binance", "BTC/USDT:USDT")[0].quantity == Decimal("0.500")

    # 2. Instant orderbook vacuum (asks = []): Market BUY safely expires with 0 fill
    clock.advance_to(t0 + 150_000)
    empty_ob = _ob(t0 + 150_000, bids=[("50100.0", "1.0")], asks=[])
    broker.on_orderbook(empty_ob)
    vac_ord = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.100"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert vac_ord.status == OrderStatus.EXPIRED
    assert vac_ord.filled_qty == Decimal("0")

    # 3. Delisting at t_delist: force-settles open 0.5 BTC @ 50,200.0 and blocks new orders
    clock.advance_to(t_delist)
    broker.process_delisting("binance", "BTC/USDT:USDT", settlement_price=Decimal("50200.0"))
    assert broker.query_positions("binance", "BTC/USDT:USDT")[0].quantity == Decimal("0")
    delist_ord = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50200.0"),
            quantity=Decimal("0.100"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert delist_ord.status == OrderStatus.REJECTED
    assert delist_ord.reject_reason == "SPEC_SYMBOL_DELISTED"


def test_rule7_delivery_contract_expiry_settlement_and_rollover(db_session: Session) -> None:
    """
    Rule 7 Hand Calculation:
    - Near-quarter BTC/USDT:USDT-260925 bought 2.0 BTC @ 60,000.0 (taker fee 0.0004 => 48.00 USDT)
    - At settlement_ts_ms, settles @ 61,500.0 with delivery_fee_rate = 0.00015:
      * Gross settlement PnL = (61,500 - 60,000) * 2.0 = +3,000.00 USDT
      * Delivery fee = 61,500 * 2.0 * 0.00015 = 18.45 USDT
    - Simultaneously rolls 2.0 BTC into next-quarter BTC/USDT:USDT-261225 @ 62,000.0 (taker fee = 49.60 USDT).
    """
    t0 = 1700000000000
    t_settle = t0 + 600_000
    near_sym = "BTC/USDT:USDT-260925"
    next_sym = "BTC/USDT:USDT-261225"

    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(_perp_spec(symbol=near_sym, contract_type=ContractType.DELIVERY, settlement_ts_ms=t_settle))
    feed.register_symbol_spec(_perp_spec(symbol=next_sym, contract_type=ContractType.DELIVERY, settlement_ts_ms=t_settle + 7_776_000_000))
    feed.load_orderbooks("binance", near_sym, [_ob(t0, [("59990.0", "5")], [("60000.0", "5")], symbol=near_sym)])
    feed.load_orderbooks("binance", next_sym, [_ob(t0, [("61990.0", "5")], [("62000.0", "5")], symbol=next_sym)])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_delivery",
        strategy_id="s_deliv",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("100000")},
        delivery_fee_rate=Decimal("0.00015"),
    )
    broker.startup_sync()

    broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol=near_sym,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("2.000"),
            leverage=Decimal("5"),
            created_ts_ms=t0,
        )
    )

    clock.advance_to(t_settle)
    broker.on_orderbook(_ob(t_settle, [("61990.0", "5")], [("62000.0", "5")], symbol=next_sym))
    roll_result = broker.settle_and_rollover_delivery(
        exchange="binance",
        expiring_symbol=near_sym,
        settlement_price=Decimal("61500.0"),
        rollover_to_symbol=next_sym,
    )
    assert roll_result["settled_qty"] == Decimal("2.000")
    assert roll_result["realized_pnl"] == Decimal("3000.0")
    assert roll_result["delivery_fee"] == Decimal("18.45")
    assert broker.query_positions("binance", near_sym)[0].quantity == Decimal("0")
    assert broker.query_positions("binance", next_sym)[0].quantity == Decimal("2.000")

    # Initial 100,000 - 48.00 + 3,000.00 - 18.45 - 49.60 = 102,883.95 USDT
    wallet = broker.query_balance("binance")[0]
    assert abs(wallet.total - Decimal("102883.95")) < Decimal("1e-9")


def test_acceptance2_and_acceptance3_fixed_strategy_exact_hand_calc_and_lookahead_guard(db_session: Session) -> None:
    """
    Acceptance ② Hand Calculation:
    - Initial cash = 10,000.00 USDT
    - Bar 1 (t1 = t0 + 59,999): Strategy emits BUY 1.0 BTC Market -> fills @ ask 50,000.0, taker fee = 20.00 USDT
    - Funding (t2 = t0 + 90,000): funding_rate = +0.0001, mark_price = 51,000.0 -> payment = -5.10 USDT
    - Bar 2 (t3 = t0 + 119,999): Strategy emits SELL 1.0 BTC Market -> fills @ bid 52,000.0, taker fee = 20.80 USDT
      Gross PnL = +2,000.00 USDT
    - Expected Net Return = +1,954.10 USDT, Total Fees = 40.80 USDT, Total Funding = -5.10 USDT, Final Equity = 11,954.10 USDT
      Error < 1e-9!

    Acceptance ③: Lookahead strategy raises LookaheadError / ASTSecurityError!
    """
    class FixedDeterministicStrategy(Strategy):
        def on_start(self):
            self.step = 0
            return None

        def on_bar(self, bar: Bar):
            self.step += 1
            if self.step == 1:
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.BUY,
                        quantity=Decimal("1.000"),
                        order_type=OrderType.MARKET,
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            if self.step == 2:
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.SELL,
                        quantity=Decimal("1.000"),
                        order_type=OrderType.MARKET,
                        reduce_only=True,
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            return None

    t0 = 1700000000000
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    spec = _perp_spec()
    feed.register_symbol_spec(spec)
    feed.load_bars(
        "binance",
        "BTC/USDT:USDT",
        "1m",
        [
            Bar(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                interval="1m",
                open_time_ms=t0,
                close_time_ms=t0 + 59_999,
                open=Decimal("50000"),
                high=Decimal("50050"),
                low=Decimal("49950"),
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
                high=Decimal("52100"),
                low=Decimal("50000"),
                close=Decimal("52000"),
                volume=Decimal("12"),
                is_closed=True,
                exchange_ts_ms=t0 + 119_999,
                local_recv_ts_ms=t0 + 119_999,
            ),
        ],
    )
    feed.load_orderbooks(
        "binance",
        "BTC/USDT:USDT",
        [
            _ob(t0 + 59_999, [("49990.0", "5")], [("50000.0", "5")]),
            _ob(t0 + 119_999, [("52000.0", "5")], [("52010.0", "5")]),
        ],
    )
    feed.load_mark_prices(
        "binance",
        "BTC/USDT:USDT",
        [
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("50000.0"),
                index_price=Decimal("50000.0"),
                exchange_ts_ms=t0 + 59_999,
                local_recv_ts_ms=t0 + 59_999,
            ),
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("51000.0"),
                index_price=Decimal("51000.0"),
                exchange_ts_ms=t0 + 90_000,
                local_recv_ts_ms=t0 + 90_000,
            ),
            MarkIndexPrice(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                mark_price=Decimal("52000.0"),
                index_price=Decimal("52000.0"),
                exchange_ts_ms=t0 + 119_999,
                local_recv_ts_ms=t0 + 119_999,
            ),
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
                mark_price=Decimal("51000.0"),
                funding_ts_ms=t0 + 90_000,
                local_recv_ts_ms=t0 + 90_000,
            )
        ],
    )

    cfg = StrategyConfig(
        strategy_id="fixed_hand_calc",
        strategy_version_id="fixed_hand_calc:v1",
        name="Fixed Hand Calc",
        exchanges=["binance"],
        symbols=["BTC/USDT:USDT"],
        bar_interval="1m",
        default_leverage=Decimal("5"),
        initial_balances={"USDT": Decimal("10000")},
    )
    engine = BacktestEngine(
        db_session=db_session,
        clock=clock,
        data_feed=feed,
        strategy_cls=FixedDeterministicStrategy,
        strategy_code="class FixedDeterministicStrategy(Strategy): pass",
        config=cfg,
    )
    result = engine.run()
    db_session.commit()

    assert abs(result.net_pnl - Decimal("1954.10")) < Decimal("1e-9")
    assert abs(result.total_fees - Decimal("40.80")) < Decimal("1e-9")
    assert abs(result.total_funding - Decimal("-5.10")) < Decimal("1e-9")
    assert abs(result.final_equity - Decimal("11954.10")) < Decimal("1e-9")

    # Acceptance ③: Runtime lookahead attempt via DataFeed MUST raise LookaheadError!
    class CheatingLookaheadStrategy(Strategy):
        def on_bar(self, bar: Bar):
            # Peeks 60s into the future!
            _ = self.ctx.data.get_bars(bar.exchange, bar.symbol, bar.interval, end_ts_ms=self.ctx.now_ms + 60_000)
            return None

    cheat_clock = SimulatedClock(t0)
    cheat_feed = HistoricalDataFeed(cheat_clock)
    cheat_feed.register_symbol_spec(spec)
    cheat_feed.load_bars("binance", "BTC/USDT:USDT", "1m", feed._bars[("binance", "BTC/USDT:USDT", "1m")])
    cheat_engine = BacktestEngine(
        db_session=db_session,
        clock=cheat_clock,
        data_feed=cheat_feed,
        strategy_cls=CheatingLookaheadStrategy,
        strategy_code="class CheatingLookaheadStrategy(Strategy): pass",
        config=cfg,
    )
    with pytest.raises(LookaheadError):
        cheat_engine.run()

    # Static lookahead attempt via .shift(-1) MUST also raise ASTSecurityError!
    with pytest.raises(ASTSecurityError):
        ASTSecurityChecker().assert_safe(
            "from quant_platform.core.interfaces import Strategy\n"
            "class CheatShift(Strategy):\n"
            "    def on_bar(self, bar):\n"
            "        return s.shift(-1)\n"
        )


def test_stop_market_causal_trigger_fill_price(db_session: Session) -> None:
    """
    Verify STOP_MARKET fills causally at stop_price (or gap open), never receiving a lookahead
    favorable bar.close fill after an intrabar stop-hunting wick!
    """
    t0 = 1_700_000_000_000
    spec = _perp_spec()
    clock = SimulatedClock(t0)
    feed = HistoricalDataFeed(clock)
    feed.register_symbol_spec(spec)
    b1 = Bar(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        interval="1h",
        open_time_ms=t0,
        close_time_ms=t0 + 3_599_999,
        open=Decimal("50000.0"),
        high=Decimal("50100.0"),
        low=Decimal("49900.0"),
        close=Decimal("50000.0"),
        volume=Decimal("100"),
        quote_volume=Decimal("5000000"),
        trade_count=200,
        is_closed=True,
        exchange_ts_ms=t0 + 3_599_999,
        local_recv_ts_ms=t0 + 3_599_999,
    )
    # Bar 2 wicks up to 50250 (triggering short stop at 50200), then dumps to close at 49200!
    b2 = Bar(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        interval="1h",
        open_time_ms=t0 + 3_600_000,
        close_time_ms=t0 + 7_199_999,
        open=Decimal("50000.0"),
        high=Decimal("50250.0"),
        low=Decimal("49100.0"),
        close=Decimal("49200.0"),
        volume=Decimal("150"),
        quote_volume=Decimal("7400000"),
        trade_count=300,
        is_closed=True,
        exchange_ts_ms=t0 + 7_199_999,
        local_recv_ts_ms=t0 + 7_199_999,
    )
    feed.load_bars("binance", "BTC/USDT:USDT", "1h", [b1, b2])

    broker = BacktestBroker(
        env=RunEnv.BACKTEST,
        run_id="run_stop_mkt",
        strategy_id="s_stop_mkt",
        clock=clock,
        data_feed=feed,
        ledger=UnifiedLedgerRepository(db_session),
        initial_balances={"USDT": Decimal("100000")},
    )
    broker.startup_sync()

    clock.advance_to(b1.close_time_ms)
    broker.on_bar(b1)
    open_short = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=Decimal("1.000"),
            created_ts_ms=b1.close_time_ms,
        )
    )
    assert open_short.status == OrderStatus.FILLED
    assert open_short.avg_fill_price == Decimal("50000.0")

    # Place resting STOP_MARKET BUY @ 50200.0 -> triggers on b2 upper wick at 50200.0, NOT 49200.0!
    rest_stop = broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.STOP_MARKET,
            stop_price=Decimal("50200.0"),
            quantity=Decimal("1.000"),
            reduce_only=True,
            created_ts_ms=b1.close_time_ms,
        )
    )
    assert rest_stop.status == OrderStatus.NEW
    clock.advance_to(b2.close_time_ms)
    fills = broker.on_bar(b2)
    assert len(fills) == 1
    assert fills[0].price == Decimal("50200.0")

