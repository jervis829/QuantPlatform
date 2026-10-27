"""P8 Acceptance Tests: ExchangeBroker startup sync, ApiKeyVault, Pre/Post-Trade Risk, Kill Switch (<1s), and SIGKILL recovery."""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select

from quant_platform.core.enums import (
    ContractType,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    RunEnv,
    TimeInForce,
)
from quant_platform.core.models import (
    Balance,
    Fill,
    LiquidityRole,
    OrderRequest,
    Position,
    SymbolSpec,
)
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import ApiKeyVaultRecord, AuditLogRecord, RiskEventRecord
from quant_platform.db.session import build_engine, build_session_factory, init_db
from quant_platform.exceptions import BrokerNotReadyError, RiskRejectedError
from quant_platform.execution import ExecutionCoordinator, OrderIdMapper, detect_state_drift
from quant_platform.live import (
    ApiKeyVault,
    ExchangeBroker,
    KeyVaultSecurityError,
    LiveRiskConfig,
    LiveRiskEngine,
    MockRawExchangeTransport,
)


def _perp_spec(symbol: str = "BTC/USDT:USDT") -> SymbolSpec:
    base = symbol.split("/")[0]
    return SymbolSpec(
        exchange="binance",
        symbol=symbol,
        contract_type=ContractType.PERPETUAL,
        base_asset=base,
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


def test_api_key_vault_encryption_no_withdraw_ip_whitelist_and_rotation(tmp_path: Path) -> None:
    """
    P8 Gate 1:
    - Encrypted storage at rest (Fernet);
    - Forbids withdrawal-enabled keys (`can_withdraw=True`);
    - Enforces IP whitelist when `require_ip_whitelist=True`;
    - Key rotation updates ciphertext, `rotated_at_ms`, and immutable audit log.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p8_vault.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    master_key = Fernet.generate_key().decode("ascii")
    clock = SimulatedClock(1_700_000_000_000)

    with sf() as session:
        vault = ApiKeyVault(session=session, master_key=master_key, clock=clock)

        # 1. Withdrawal permission MUST be rejected
        with pytest.raises(KeyVaultSecurityError) as exc_wd:
            vault.store_key(
                key_id="k_bad_wd",
                exchange="binance",
                account_label="main",
                env=RunEnv.LIVE,
                api_key="AK_WITHDRAW",
                api_secret="SK_WITHDRAW",
                ip_whitelist=["203.0.113.10"],
                can_withdraw=True,
                is_testnet=True,
                operator="ops_admin",
            )
        assert exc_wd.value.code == "WITHDRAW_PERMISSION_FORBIDDEN"

        # 2. Missing IP whitelist MUST be rejected for live keys
        with pytest.raises(KeyVaultSecurityError) as exc_ip:
            vault.store_key(
                key_id="k_bad_ip",
                exchange="binance",
                account_label="main",
                env=RunEnv.LIVE,
                api_key="AK_NO_IP",
                api_secret="SK_NO_IP",
                ip_whitelist=[],
                can_withdraw=False,
                is_testnet=False,
                require_ip_whitelist=True,
                operator="ops_admin",
            )
        assert exc_ip.value.code == "IP_WHITELIST_REQUIRED"

        # 3. Store valid Testnet key and verify encrypted-at-rest storage
        vault.store_key(
            key_id="k_testnet_1",
            exchange="binance",
            account_label="sim_subaccount_1",
            env=RunEnv.LIVE,
            api_key="BINANCE_TESTNET_AK_001",
            api_secret="BINANCE_TESTNET_SK_001",
            ip_whitelist=["203.0.113.10"],
            can_withdraw=False,
            is_testnet=True,
            operator="ops_admin",
        )
        raw_row = session.execute(
            select(ApiKeyVaultRecord).where(ApiKeyVaultRecord.key_id == "k_testnet_1")
        ).scalar_one()
        assert raw_row.encrypted_api_key != "BINANCE_TESTNET_AK_001"
        assert "BINANCE_TESTNET" not in raw_row.encrypted_api_key

        creds = vault.load_decrypted_credentials("k_testnet_1")
        assert creds["api_key"] == "BINANCE_TESTNET_AK_001"
        assert creds["api_secret"] == "BINANCE_TESTNET_SK_001"
        assert creds["is_testnet"] is True

        # 4. Rotate key -> ciphertext updates + audit log recorded
        clock.advance_by(60_000)
        vault.rotate_key(
            key_id="k_testnet_1",
            new_api_key="BINANCE_TESTNET_AK_002",
            new_api_secret="BINANCE_TESTNET_SK_002",
            operator="ops_admin",
        )
        session.commit()

        rotated_creds = vault.load_decrypted_credentials("k_testnet_1")
        assert rotated_creds["api_key"] == "BINANCE_TESTNET_AK_002"
        audits = list(session.execute(select(AuditLogRecord)).scalars().all())
        assert any(a.action == "API_KEY_ROTATED" for a in audits)


def test_exchange_broker_startup_sync_gate_and_protective_stop_order(tmp_path: Path) -> None:
    """
    P8 Gate 2 & Red Line 10:
    - `ExchangeBroker` blocks `submit_order` before `startup_sync()` (`BrokerNotReadyError`);
    - `startup_sync()` pulls raw string open orders, positions, balances, recent fills, and funding payments
      from exchange transport, parses directly to `Decimal`, reconciles into DB, and marks `is_ready() == True`;
    - Supports exchange-native server-side protective `STOP_MARKET` `reduce_only=True` orders.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p8_broker.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_010_000)
    spec = _perp_spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)

    transport = MockRawExchangeTransport(
        exchange="binance",
        clock=clock,
        initial_balances={"USDT": "25000.50000000"},
        initial_positions=[
            {
                "symbol": "BTC/USDT:USDT",
                "positionAmt": "0.250",
                "entryPrice": "49800.0",
                "markPrice": "50000.0",
                "unRealizedProfit": "50.0",
                "leverage": "5",
                "marginType": "cross",
            }
        ],
        initial_open_orders=[
            {
                "orderId": "88001",
                "clientOrderId": "preexisting_cid_1",
                "symbol": "BTC/USDT:USDT",
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "price": "52000.0",
                "origQty": "0.100",
                "executedQty": "0.000",
                "status": "NEW",
                "reduceOnly": True,
                "updateTime": 1_700_000_005_000,
            }
        ],
        initial_fills=[
            {
                "id": "tr_7701",
                "orderId": "88000",
                "clientOrderId": "preexisting_entry_cid",
                "symbol": "BTC/USDT:USDT",
                "side": "BUY",
                "price": "49800.0",
                "qty": "0.250",
                "quoteQty": "12450.0",
                "commission": "4.9800",
                "commissionAsset": "USDT",
                "maker": False,
                "realizedPnl": "0",
                "time": 1_700_000_001_000,
            }
        ],
        initial_funding=[
            {
                "tranId": "fp_9901",
                "symbol": "BTC/USDT:USDT",
                "income": "-1.2500",
                "asset": "USDT",
                "fundingRate": "0.0001",
                "markPrice": "50000.0",
                "positionAmt": "0.250",
                "time": 1_700_000_008_000,
            }
        ],
    )

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        broker = ExchangeBroker(
            run_id="run_live_sync",
            strategy_id="strat_live",
            exchange="binance",
            transport=transport,
            clock=clock,
            data_feed=feed,
            ledger=ledger,
        )

        # Must block orders before startup_sync()
        assert broker.is_ready() is False
        with pytest.raises(BrokerNotReadyError):
            broker.submit_order(
                OrderRequest(
                    client_order_id="blocked_cid",
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("0.010"),
                    created_ts_ms=clock.now_ms(),
                )
            )

        sync_summary = broker.startup_sync()
        assert broker.is_ready() is True
        assert sync_summary == {
            "ready": True,
            "open_orders": 1,
            "positions": 1,
            "balances": 1,
            "recent_fills": 1,
            "funding_payments": 1,
        }

        # Submit exchange-native protective stop-loss order
        stop_ord = broker.submit_protective_stop_order(
            client_order_id="stop_protect_1",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            quantity=Decimal("0.250"),
            stop_price=Decimal("48500.04"),  # auto-normalized to tick_size=0.1 -> 48500.0
        )
        assert stop_ord.status == OrderStatus.NEW
        assert stop_ord.order_type == OrderType.STOP_MARKET
        assert stop_ord.reduce_only is True
        assert stop_ord.stop_price == Decimal("48500.0")
        session.commit()


def test_pre_trade_all_risk_limits_post_trade_alerts_and_subsecond_kill_switch(tmp_path: Path) -> None:
    """
    P8 Gate 3:
    Verify every pre-trade risk gate, post-trade alert, and <1s emergency Kill Switch (cancel all + flatten).
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p8_risk.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_000_000)
    btc_spec = _perp_spec("BTC/USDT:USDT")
    doge_spec = _perp_spec("DOGE/USDT:USDT")
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(btc_spec)
    feed.register_symbol_spec(doge_spec)

    alerts: list[dict] = []
    risk_cfg = LiveRiskConfig(
        max_order_notional=Decimal("20000"),
        max_symbol_notional=Decimal("35000"),
        max_total_leverage=Decimal("3"),
        max_daily_loss_amount=Decimal("500"),
        max_drawdown_pct=Decimal("0.10"),
        max_orders_per_minute=3,
        duplicate_window_ms=5_000,
        blacklisted_symbols=["DOGE/USDT:USDT"],
        max_slippage_bps=Decimal("30"),
        liq_distance_warn_pct=Decimal("0.05"),
    )

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        risk = LiveRiskEngine(
            env=RunEnv.LIVE,
            run_id="run_risk_1",
            strategy_id="strat_risk",
            config=risk_cfg,
            session=session,
            clock=clock,
            alert_callbacks=[alerts.append],
        )

        base_bal = [
            Balance(
                env=RunEnv.LIVE,
                run_id="run_risk_1",
                exchange="binance",
                asset="USDT",
                total=Decimal("10000"),
                available=Decimal("8000"),
                locked=Decimal("2000"),
                ts_ms=clock.now_ms(),
            )
        ]
        existing_pos = [
            Position(
                env=RunEnv.LIVE,
                run_id="run_risk_1",
                strategy_id="strat_risk",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                margin_mode=MarginMode.CROSS,
                leverage=Decimal("5"),
                quantity=Decimal("0.400"),  # notional = 20,000 USDT @ 50,000
                entry_price=Decimal("50000"),
                mark_price=Decimal("50000"),
                liquidation_price=Decimal("48000"),
                updated_ts_ms=clock.now_ms(),
            )
        ]

        # 1. Blacklisted symbol -> SYMBOL_BLACKLISTED
        with pytest.raises(RiskRejectedError) as e1:
            risk.check_pre_trade(
                OrderRequest(
                    client_order_id="r1",
                    exchange="binance",
                    symbol="DOGE/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("10"),
                    price=Decimal("1.0"),
                    created_ts_ms=clock.now_ms(),
                ),
                doge_spec,
                existing_pos,
                base_bal,
                clock.now_ms(),
            )
        assert e1.value.code == "SYMBOL_BLACKLISTED"

        # 2. Single order notional 0.5 * 50000 = 25000 > 20000 -> MAX_ORDER_NOTIONAL
        with pytest.raises(RiskRejectedError) as e2:
            risk.check_pre_trade(
                OrderRequest(
                    client_order_id="r2",
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("0.500"),
                    price=Decimal("50000.0"),
                    created_ts_ms=clock.now_ms(),
                ),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms(),
            )
        assert e2.value.code == "MAX_ORDER_NOTIONAL"

        # 3. Symbol notional: current 20000 + new 16000 = 36000 > 35000 -> MAX_SYMBOL_NOTIONAL
        with pytest.raises(RiskRejectedError) as e3:
            risk.check_pre_trade(
                OrderRequest(
                    client_order_id="r3",
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("0.320"),
                    price=Decimal("50000.0"),
                    created_ts_ms=clock.now_ms(),
                ),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms(),
            )
        assert e3.value.code == "MAX_SYMBOL_NOTIONAL"

        # 4. Total portfolio leverage: (20000 + 12000) / 10000 = 3.2x > 3.0x -> MAX_TOTAL_LEVERAGE
        with pytest.raises(RiskRejectedError) as e4:
            risk.check_pre_trade(
                OrderRequest(
                    client_order_id="r4",
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("0.240"),
                    price=Decimal("50000.0"),
                    created_ts_ms=clock.now_ms(),
                ),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms(),
            )
        assert e4.value.code == "MAX_TOTAL_LEVERAGE"

        # 5. Insufficient available margin: order notional 5000 @ leverage 1 requires 5000 > available 400
        low_avail_bal = [base_bal[0].model_copy(update={"available": Decimal("400")})]
        with pytest.raises(RiskRejectedError) as e5:
            risk.check_pre_trade(
                OrderRequest(
                    client_order_id="r5",
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("0.100"),
                    price=Decimal("50000.0"),
                    leverage=Decimal("1"),
                    created_ts_ms=clock.now_ms(),
                ),
                btc_spec,
                existing_pos,
                low_avail_bal,
                clock.now_ms(),
            )
        assert e5.value.code == "INSUFFICIENT_MARGIN"

        # 6. Valid order passes, then identical duplicate within 5000ms blocked -> DUPLICATE_ORDER_GUARD
        valid_req = OrderRequest(
            client_order_id="ok_1",
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.100"),
            price=Decimal("50000.0"),
            leverage=Decimal("5"),
            created_ts_ms=clock.now_ms(),
        )
        risk.check_pre_trade(valid_req, btc_spec, existing_pos, base_bal, clock.now_ms())
        with pytest.raises(RiskRejectedError) as e6:
            risk.check_pre_trade(
                valid_req.model_copy(update={"client_order_id": "ok_dup"}),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms() + 1_000,
            )
        assert e6.value.code == "DUPLICATE_ORDER_GUARD"

        # 7. Order rate limit: max 3 orders/min -> 4th distinct order in same minute blocked
        risk.check_pre_trade(
            valid_req.model_copy(update={"client_order_id": "ok_2", "quantity": Decimal("0.050")}),
            btc_spec,
            existing_pos,
            base_bal,
            clock.now_ms() + 100,
        )
        risk.check_pre_trade(
            valid_req.model_copy(update={"client_order_id": "ok_3", "quantity": Decimal("0.060")}),
            btc_spec,
            existing_pos,
            base_bal,
            clock.now_ms() + 200,
        )
        with pytest.raises(RiskRejectedError) as e7:
            risk.check_pre_trade(
                valid_req.model_copy(update={"client_order_id": "ok_4", "quantity": Decimal("0.070")}),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms() + 300,
            )
        assert e7.value.code == "ORDER_RATE_LIMIT"

        # 8. Daily loss & Max drawdown circuit breakers (after advancing past rate-limit window)
        clock.advance_by(65_000)
        risk.record_equity_snapshot(Decimal("10000"))
        risk.record_daily_pnl(Decimal("-600"))
        with pytest.raises(RiskRejectedError) as e8:
            risk.check_pre_trade(
                valid_req.model_copy(update={"client_order_id": "ok_loss", "quantity": Decimal("0.020")}),
                btc_spec,
                existing_pos,
                base_bal,
                clock.now_ms(),
            )
        assert e8.value.code == "DAILY_LOSS_BREAKER"

        risk.record_daily_pnl(Decimal("0"))
        dd_bal = [base_bal[0].model_copy(update={"total": Decimal("8500")})]  # 15% DD from 10000 peak > 10%
        with pytest.raises(RiskRejectedError) as e9:
            risk.check_pre_trade(
                valid_req.model_copy(update={"client_order_id": "ok_dd", "quantity": Decimal("0.020")}),
                btc_spec,
                existing_pos,
                dd_bal,
                clock.now_ms(),
            )
        assert e9.value.code == "MAX_DRAWDOWN_BREAKER"

        # 9. Post-trade abnormal slippage + liquidation proximity alerts
        risk.update_tracked_positions(existing_pos)
        post_events = risk.check_post_trade(
            Fill(
                fill_id="f_slip_1",
                env=RunEnv.LIVE,
                run_id="run_risk_1",
                strategy_id="strat_risk",
                client_order_id="ok_1",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                liquidity=LiquidityRole.TAKER,
                price=Decimal("50250.0"),
                quantity=Decimal("0.100"),
                quote_qty=Decimal("5025.0"),
                reference_price=Decimal("50000.0"),
                slippage_bps=Decimal("50.0"),
                fee=Decimal("2.01"),
                fee_asset="USDT",
                mark_price=Decimal("49000.0"),  # within 2.04% of liq_price=48000 (< 5% warn threshold)
                exchange_ts_ms=clock.now_ms(),
                local_recv_ts_ms=clock.now_ms(),
            ),
            mark_price=Decimal("49000.0"),
        )
        codes = {ev["rule_code"] for ev in post_events}
        assert "ABNORMAL_SLIPPAGE_ALERT" in codes
        assert "LIQUIDATION_PROXIMITY_ALERT" in codes
        assert len(alerts) >= 2

        # 10. Emergency Kill Switch (< 1.0s): cancel all open orders + optional market flatten!
        transport = MockRawExchangeTransport(
            exchange="binance",
            clock=clock,
            initial_balances={"USDT": "10000"},
            initial_positions=[
                {
                    "symbol": "BTC/USDT:USDT",
                    "positionAmt": "0.200",
                    "entryPrice": "50000.0",
                    "markPrice": "50000.0",
                    "unRealizedProfit": "0",
                    "leverage": "5",
                    "marginType": "cross",
                }
            ],
            initial_open_orders=[
                {
                    "orderId": "9001",
                    "clientOrderId": "open_lim_1",
                    "symbol": "BTC/USDT:USDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "price": "49000.0",
                    "origQty": "0.100",
                    "executedQty": "0.000",
                    "status": "NEW",
                    "reduceOnly": False,
                    "updateTime": clock.now_ms(),
                }
            ],
        )
        broker = ExchangeBroker(
            run_id="run_risk_1",
            strategy_id="strat_risk",
            exchange="binance",
            transport=transport,
            clock=clock,
            data_feed=feed,
            ledger=ledger,
        )
        broker.startup_sync()

        t0 = time.perf_counter()
        ks_res = risk.trigger_kill_switch(broker=broker, close_positions=True, reason="emergency_test")
        elapsed_sec = time.perf_counter() - t0

        assert elapsed_sec < 1.0
        assert ks_res["canceled_orders"] == 1
        assert ks_res["flattened_positions"] == 1
        assert broker.query_open_orders() == []
        assert all(p.quantity == Decimal("0") for p in broker.query_positions())

        # Any subsequent new non-reduce order MUST be blocked by KILL_SWITCH_ACTIVE
        with pytest.raises(RiskRejectedError) as e_ks:
            risk.check_pre_trade(
                valid_req.model_copy(update={"client_order_id": "after_ks", "quantity": Decimal("0.010")}),
                btc_spec,
                [],
                base_bal,
                clock.now_ms(),
            )
        assert e_ks.value.code == "KILL_SWITCH_ACTIVE"

        risk_rows = list(session.execute(select(RiskEventRecord)).scalars().all())
        assert len(risk_rows) >= 10
        session.commit()


def test_sigkill_process_crash_recovery_reconciles_exchange_truth_without_duplicates(tmp_path: Path) -> None:
    """
    P8 Gate 4 (Real SIGKILL crash recovery drill):
    1. Spawn a live worker subprocess that places an open limit order + market fill on the shared exchange state file,
       then gets killed abruptly with `SIGKILL` (`kill -9`) BEFORE flushing its final local state.
    2. Start a fresh recovery `ExchangeBroker` + `ExecutionCoordinator` against the exchange state + DB.
    3. Verify `startup_sync()` + `recover_after_disconnect()` rebuilds exact open orders, fills, and positions
       from exchange truth with zero duplicate orders and zero drift!
    """
    db_file = tmp_path / "p8_sigkill.db"
    exchange_state_file = tmp_path / "exchange_truth.json"
    ready_flag = tmp_path / "worker_submitted.flag"

    worker_script = tmp_path / "live_worker_crash.py"
    worker_script.write_text(
        f"""
import time
from decimal import Decimal
from pathlib import Path
from quant_platform.core.enums import ContractType, OrderSide, OrderType, TimeInForce
from quant_platform.core.models import OrderRequest, SymbolSpec
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.session import build_engine, build_session_factory, init_db
from quant_platform.live import ExchangeBroker, MockRawExchangeTransport

engine = build_engine("sqlite:///{db_file}")
init_db(engine)
sf = build_session_factory(engine)
clock = SimulatedClock(1_700_000_000_000)
spec = SymbolSpec(
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
feed = StreamingDataFeed(clock=clock)
feed.register_symbol_spec(spec)
transport = MockRawExchangeTransport(
    exchange="binance",
    clock=clock,
    initial_balances={{"USDT": "15000"}},
    state_file=Path("{exchange_state_file}"),
)

with sf() as session:
    # Intentionally do NOT commit DB before SIGKILL so local DB lags behind exchange truth!
    ledger = UnifiedLedgerRepository(session)
    broker = ExchangeBroker(
        run_id="run_sigkill_live",
        strategy_id="strat_crash",
        exchange="binance",
        transport=transport,
        clock=clock,
        data_feed=feed,
        ledger=ledger,
    )
    broker.startup_sync()
    broker.submit_order(OrderRequest(
        client_order_id="crash_mkt_entry_1",
        exchange="binance",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.150"),
        created_ts_ms=clock.now_ms(),
    ))
    broker.submit_order(OrderRequest(
        client_order_id="crash_lim_tp_1",
        exchange="binance",
        symbol="BTC/USDT:USDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        quantity=Decimal("0.150"),
        price=Decimal("53000.0"),
        reduce_only=True,
        created_ts_ms=clock.now_ms(),
    ))
    Path("{ready_flag}").write_text("ready")
    while True:
        time.sleep(0.05)
""",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path.cwd())
    proc = subprocess.Popen([sys.executable, str(worker_script)], env=env)
    try:
        deadline = time.monotonic() + 10.0
        while not ready_flag.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready_flag.exists(), "Worker did not submit orders before timeout"

        # Brutally terminate live process with SIGKILL (-9)!
        os.kill(proc.pid, signal.SIGKILL)
        exit_code = proc.wait(timeout=5.0)
        assert exit_code == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()

    # Verify exchange truth persisted both the filled market position and open TP limit order
    raw_truth = json.loads(exchange_state_file.read_text(encoding="utf-8"))
    assert len(raw_truth["open_orders"]) == 1

    # Start fresh recovery process against exchange_state_file + DB!
    engine = build_engine(f"sqlite:///{db_file}")
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_005_000)
    spec = _perp_spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)
    rec_transport = MockRawExchangeTransport.from_state_file(
        exchange="binance",
        clock=clock,
        state_file=exchange_state_file,
    )

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        rec_broker = ExchangeBroker(
            run_id="run_sigkill_live",
            strategy_id="strat_crash",
            exchange="binance",
            transport=rec_transport,
            clock=clock,
            data_feed=feed,
            ledger=ledger,
        )
        sync_res = rec_broker.startup_sync()
        assert sync_res["ready"] is True
        assert sync_res["open_orders"] == 1
        assert sync_res["positions"] == 1
        assert sync_res["recent_fills"] == 1

        coord = ExecutionCoordinator(
            env=RunEnv.LIVE,
            run_id="run_sigkill_live",
            strategy_id="strat_crash",
            broker=rec_broker,
            ledger=ledger,
            id_mapper=OrderIdMapper(session),
            clock=clock,
        )
        coord.recover_after_disconnect()
        session.commit()

        assert detect_state_drift(coord, rec_broker) == []
        recovered_open = coord.list_local_open_orders()
        assert len(recovered_open) == 1
        assert recovered_open[0].client_order_id == "crash_lim_tp_1"
        assert rec_broker.query_positions("binance", "BTC/USDT:USDT")[0].quantity == Decimal("0.150")
