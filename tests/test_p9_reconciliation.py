"""P9 Acceptance Tests: 3-Tier Reconciliation Engine (Exchange vs Local, Shadow vs Live, Backtest vs Shadow)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from quant_platform.core.enums import (
    ContractType,
    DiscrepancySeverity,
    LiquidityRole,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconciliationTier,
    RunEnv,
)
from quant_platform.core.models import (
    Balance,
    Fill,
    FundingPayment,
    Order,
    Position,
    SymbolSpec,
)
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import SignalTraceRecord
from quant_platform.db.session import build_engine, build_session_factory, init_db
from quant_platform.live import ExchangeBroker, MockRawExchangeTransport
from quant_platform.reconciliation import (
    ReconciliationScheduler,
    ThreeTierReconciler,
)


def _spec() -> SymbolSpec:
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
        max_leverage=Decimal("50"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        effective_from_ms=1_700_000_000_000,
    )


def test_tier1_exchange_vs_local_detects_order_fill_position_balance_funding_drifts_and_drills_down(
    tmp_path: Path,
) -> None:
    """
    P9 Tier 1 Gate:
    Inject controlled discrepancies between local DB (`RunEnv.LIVE`) and authoritative `ExchangeBroker`:
    1. Phantom local open order `cid_phantom_local` (CANCELED/absent on exchange);
    2. Missing exchange open order `cid_missing_locally` (orderId=7002);
    3. Missing exchange fill `tr_7009` on `cid_entry`;
    4. Position qty drift (local=0.100 vs exchange=0.250 -> delta +0.150);
    5. Balance total drift (local=9800 vs exchange=10250 -> delta +450);
    6. Missing funding payment `fp_701` (-2.50 USDT).
    Verify alert fires, report persists to DB, drill-down locates exact `order_id`/`fill_id`/`ts_ms`,
    CSV/JSON export succeeds, and `auto_heal=True` converges local DB to 0 discrepancies!
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p9_tier1.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_100_000)
    spec = _spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)

    transport = MockRawExchangeTransport(
        exchange="binance",
        clock=clock,
        initial_balances={"USDT": "10250.00"},
        initial_positions=[
            {
                "symbol": "BTC/USDT:USDT",
                "positionAmt": "0.250",
                "entryPrice": "50000.0",
                "markPrice": "50100.0",
                "unRealizedProfit": "25.0",
                "leverage": "5",
                "marginType": "cross",
            }
        ],
        initial_open_orders=[
            {
                "orderId": "7002",
                "clientOrderId": "cid_missing_locally",
                "symbol": "BTC/USDT:USDT",
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "price": "52000.0",
                "origQty": "0.250",
                "executedQty": "0.000",
                "status": "NEW",
                "reduceOnly": True,
                "updateTime": 1_700_000_090_000,
            }
        ],
        initial_fills=[
            {
                "id": "tr_7009",
                "orderId": "7001",
                "clientOrderId": "cid_entry",
                "symbol": "BTC/USDT:USDT",
                "side": "BUY",
                "price": "50000.0",
                "qty": "0.250",
                "quoteQty": "12500.0",
                "commission": "5.00",
                "commissionAsset": "USDT",
                "maker": False,
                "realizedPnl": "0",
                "time": 1_700_000_080_000,
            }
        ],
        initial_funding=[
            {
                "tranId": "fp_701",
                "symbol": "BTC/USDT:USDT",
                "income": "-2.50",
                "asset": "USDT",
                "fundingRate": "0.0002",
                "markPrice": "50000.0",
                "positionAmt": "0.250",
                "time": 1_700_000_095_000,
            }
        ],
    )

    alerts: list[dict] = []

    with sf() as session:
        # Broker connected to separate unattached ledger during startup so local DB stays drifted for test!
        broker = ExchangeBroker(
            run_id="run_live_t1",
            strategy_id="strat_t1",
            exchange="binance",
            transport=transport,
            clock=clock,
            data_feed=feed,
            ledger=None,
        )
        broker.startup_sync()

        ledger = UnifiedLedgerRepository(session)
        # Seed stale/drifted local records
        ledger.upsert_order(
            Order(
                env=RunEnv.LIVE,
                run_id="run_live_t1",
                strategy_id="strat_t1",
                client_order_id="cid_phantom_local",
                exchange_order_id="6999",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("48000.0"),
                quantity=Decimal("0.100"),
                remaining_qty=Decimal("0.100"),
                status=OrderStatus.NEW,
                created_ts_ms=1_700_000_050_000,
                updated_ts_ms=1_700_000_050_000,
                exchange_ts_ms=1_700_000_050_000,
                local_recv_ts_ms=1_700_000_050_000,
            )
        )
        ledger.upsert_position(
            Position(
                env=RunEnv.LIVE,
                run_id="run_live_t1",
                strategy_id="strat_t1",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                margin_mode=MarginMode.CROSS,
                leverage=Decimal("5"),
                quantity=Decimal("0.100"),
                entry_price=Decimal("50000.0"),
                mark_price=Decimal("50000.0"),
                updated_ts_ms=1_700_000_060_000,
            )
        )
        ledger.record_balance_snapshot(
            Balance(
                env=RunEnv.LIVE,
                run_id="run_live_t1",
                exchange="binance",
                asset="USDT",
                total=Decimal("9800.00"),
                available=Decimal("9800.00"),
                ts_ms=1_700_000_060_000,
            )
        )

        reconciler = ThreeTierReconciler(session=session, clock=clock, alert_callbacks=[alerts.append])
        rep = reconciler.reconcile_exchange_vs_local("run_live_t1", broker)

        assert rep["tier"] == ReconciliationTier.EXCHANGE_VS_LOCAL.value
        assert rep["passed"] is False
        assert rep["discrepancy_count"] == 6
        assert rep["max_severity"] == DiscrepancySeverity.CRITICAL.value
        assert len(alerts) == 1

        # Drill-down verification: every discrepancy carries order_id / fill_id / ts_ms
        items = reconciler.drill_down(rep["report_id"])
        dtypes = {it["type"] for it in items}
        assert dtypes == {
            "PHANTOM_LOCAL_OPEN_ORDER",
            "MISSING_LOCAL_OPEN_ORDER",
            "MISSING_LOCAL_FILL",
            "POSITION_QTY_MISMATCH",
            "BALANCE_TOTAL_MISMATCH",
            "MISSING_LOCAL_FUNDING",
        }
        fill_item = next(it for it in items if it["type"] == "MISSING_LOCAL_FILL")
        assert fill_item["fill_id"] == "exfill_tr_7009"
        assert fill_item["order_id"] == "cid_entry"
        assert fill_item["ts_ms"] == 1_700_000_080_000

        # Export JSON & CSV
        csv_text = reconciler.export_report_csv(rep["report_id"])
        json_text = reconciler.export_report_json(rep["report_id"])
        assert "MISSING_LOCAL_FILL" in csv_text
        assert "exfill_tr_7009" in json_text

        # Auto-heal local DB from authoritative exchange state -> subsequent recon passes cleanly!
        healed_rep = reconciler.reconcile_exchange_vs_local("run_live_t1", broker, auto_heal=True)
        assert healed_rep["healed"] is True
        after_rep = reconciler.reconcile_exchange_vs_local("run_live_t1", broker)
        assert after_rep["passed"] is True
        assert after_rep["discrepancy_count"] == 0
        session.commit()


def test_tier2_shadow_vs_live_daily_symbol_comparison_and_tier3_backtest_vs_shadow_6_factor_attribution(
    tmp_path: Path,
) -> None:
    """
    P9 Tier 2 & Tier 3 Gates + Scheduled Trigger:
    - Seed Backtest (`run_bt_9`), Shadow (`run_sh_9`), and Live (`run_lv_9`) runs across 2 symbols / dates
      with exact hand-calculated slippage, fill rate, latency, fee, funding, and data snapshot differences:
      * Backtest: requested 1.000 BTC, filled 1.000 (fill_rate=1.0), slip=2.0 bps, latency=0ms, fee=4.00, funding=-1.00, snap="ob:100"
      * Shadow:   requested 1.000 BTC, filled 0.800 (fill_rate=0.8), slip=5.0 bps, latency=35ms, fee=3.50, funding=-1.20, snap="ob:101"
      * Live:     requested 1.000 BTC, filled 0.750 (fill_rate=0.75), slip=8.0 bps, latency=90ms, fee=3.60, funding=-1.25, snap="ob:101"
    - Verify Tier 2 (`reconcile_shadow_vs_live`) computes exact day x symbol VWAP bps diff, volume diff, fee/funding diff, and drill-down items!
    - Verify Tier 3 (`reconcile_backtest_vs_shadow`) decomposes all 6 attribution dimensions accurately:
      * slippage_bps_delta = 5.0 - 2.0 = +3.0 bps
      * fill_rate_delta    = 0.8 - 1.0 = -0.2
      * latency_ms_delta   = 35 - 0 = +35 ms
      * fee_delta          = 3.50 - 4.00 = -0.50
      * funding_delta      = -1.20 - (-1.00) = -0.20
      * data_divergence_count = 1 (snapshot_ref "ob:101" != "ob:100")
    - Verify `ReconciliationScheduler` runs periodic batch jobs and persists reports!
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p9_tier23.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    day1_ts = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
    clock = SimulatedClock(day1_ts + 10_000)

    def _seed_env_run(
        session,
        *,
        env: RunEnv,
        run_id: str,
        req_qty: Decimal,
        fill_qty: Decimal,
        fill_px: Decimal,
        slip_bps: Decimal,
        latency_ms: int,
        fee: Decimal,
        funding_amt: Decimal,
        snap_ref: str,
    ) -> None:
        ledger = UnifiedLedgerRepository(session)
        cid = f"cid_{env.value}_1"
        fid = f"fill_{env.value}_1"
        session.add(
            SignalTraceRecord(
                signal_id="sig_shared_1",
                env=env.value,
                run_id=run_id,
                strategy_id="strat_cmp",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                intent_type="Signal",
                payload_json={"action": "BUY"},
                client_order_id=cid,
                snapshot_ref=snap_ref,
                signal_ts_ms=day1_ts + 1_000,
                order_ts_ms=day1_ts + 1_000,
            )
        )
        ledger.upsert_order(
            Order(
                env=env,
                run_id=run_id,
                strategy_id="strat_cmp",
                client_order_id=cid,
                exchange_order_id=f"ex_{cid}",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("50000.0"),
                quantity=req_qty,
                filled_qty=fill_qty,
                avg_fill_price=fill_px,
                remaining_qty=req_qty - fill_qty,
                fee_total=fee,
                fee_asset="USDT",
                status=OrderStatus.FILLED if fill_qty == req_qty else OrderStatus.PARTIALLY_FILLED,
                signal_id="sig_shared_1",
                created_ts_ms=day1_ts + 1_000,
                updated_ts_ms=day1_ts + 1_000 + latency_ms,
                exchange_ts_ms=day1_ts + 1_000 + latency_ms,
                local_recv_ts_ms=day1_ts + 1_000 + latency_ms,
            )
        )
        ledger.record_fill(
            Fill(
                fill_id=fid,
                env=env,
                run_id=run_id,
                strategy_id="strat_cmp",
                client_order_id=cid,
                exchange_order_id=f"ex_{cid}",
                exchange_trade_id=f"tr_{cid}",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                liquidity=LiquidityRole.TAKER,
                price=fill_px,
                quantity=fill_qty,
                quote_qty=fill_px * fill_qty,
                reference_price=Decimal("50000.0"),
                slippage_bps=slip_bps,
                fee=fee,
                fee_asset="USDT",
                snapshot_ref=snap_ref,
                exchange_ts_ms=day1_ts + 1_000 + latency_ms,
                local_recv_ts_ms=day1_ts + 1_000 + latency_ms,
            )
        )
        ledger.record_funding_payment(
            FundingPayment(
                payment_id=f"fp_{env.value}_1",
                env=env,
                run_id=run_id,
                exchange="binance",
                symbol="BTC/USDT:USDT",
                funding_rate=Decimal("0.0001"),
                mark_price=Decimal("50000.0"),
                position_qty=fill_qty,
                amount=funding_amt,
                asset="USDT",
                settlement_ts_ms=day1_ts + 5_000,
                local_recv_ts_ms=day1_ts + 5_000,
            )
        )

    with sf() as session:
        _seed_env_run(
            session,
            env=RunEnv.BACKTEST,
            run_id="run_bt_9",
            req_qty=Decimal("1.000"),
            fill_qty=Decimal("1.000"),
            fill_px=Decimal("50010.0"),
            slip_bps=Decimal("2.0"),
            latency_ms=0,
            fee=Decimal("4.00"),
            funding_amt=Decimal("-1.00"),
            snap_ref="ob:100",
        )
        _seed_env_run(
            session,
            env=RunEnv.SHADOW,
            run_id="run_sh_9",
            req_qty=Decimal("1.000"),
            fill_qty=Decimal("0.800"),
            fill_px=Decimal("50025.0"),
            slip_bps=Decimal("5.0"),
            latency_ms=35,
            fee=Decimal("3.50"),
            funding_amt=Decimal("-1.20"),
            snap_ref="ob:101",
        )
        _seed_env_run(
            session,
            env=RunEnv.LIVE,
            run_id="run_lv_9",
            req_qty=Decimal("1.000"),
            fill_qty=Decimal("0.750"),
            fill_px=Decimal("50040.0"),
            slip_bps=Decimal("8.0"),
            latency_ms=90,
            fee=Decimal("3.60"),
            funding_amt=Decimal("-1.25"),
            snap_ref="ob:101",
        )
        session.flush()

        reconciler = ThreeTierReconciler(session=session, clock=clock)

        # Tier 2: Shadow vs Live (by day x symbol)
        t2 = reconciler.reconcile_shadow_vs_live("run_sh_9", "run_lv_9")
        assert t2["tier"] == ReconciliationTier.SHADOW_VS_LIVE.value
        day_buckets = t2["summary"]["by_day_symbol"]
        assert len(day_buckets) == 1
        bucket = day_buckets[0]
        assert bucket["date"] == "2024-01-01"
        assert bucket["symbol"] == "BTC/USDT:USDT"
        assert Decimal(bucket["shadow_fill_qty"]) == Decimal("0.800")
        assert Decimal(bucket["live_fill_qty"]) == Decimal("0.750")
        assert Decimal(bucket["qty_delta"]) == Decimal("-0.050")
        assert Decimal(bucket["fee_delta"]) == Decimal("0.10")
        assert Decimal(bucket["funding_delta"]) == Decimal("-0.05")
        assert len(t2["items"]) >= 1
        assert t2["items"][0]["order_id"] == "cid_live_1"
        assert t2["items"][0]["fill_id"] == "fill_live_1"

        # Tier 3: Backtest vs Shadow (6-factor attribution)
        t3 = reconciler.reconcile_backtest_vs_shadow("run_bt_9", "run_sh_9")
        assert t3["tier"] == ReconciliationTier.BACKTEST_VS_SHADOW.value
        attr = t3["summary"]["attribution"]
        assert Decimal(attr["slippage_bps_delta"]) == Decimal("3.0")
        assert Decimal(attr["fill_rate_delta"]) == Decimal("-0.2")
        assert Decimal(attr["latency_ms_delta"]) == Decimal("35")
        assert Decimal(attr["fee_delta"]) == Decimal("-0.50")
        assert Decimal(attr["funding_delta"]) == Decimal("-0.20")
        assert attr["data_divergence_count"] == 1

        # Scheduled runner check
        scheduler = ReconciliationScheduler(reconciler=reconciler, interval_ms=60_000, clock=clock)
        scheduler.register_backtest_shadow_pair("run_bt_9", "run_sh_9")
        scheduler.register_shadow_live_pair("run_sh_9", "run_lv_9")
        ran = scheduler.tick()
        assert len(ran) == 2
        # Calling tick() immediately again before interval_ms elapses runs 0 jobs
        assert scheduler.tick() == []
        clock.advance_by(60_001)
        assert len(scheduler.tick()) == 2
        session.commit()
