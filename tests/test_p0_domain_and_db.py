"""P0 Tests: Domain models, Decimal strictness, unified 3-env schema, Alembic reversible migrations, and 8 core interfaces."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import random

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import Float, inspect, select, text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from quant_platform.core import (
    Balance,
    Bar,
    BrokerAdapter,
    Clock,
    ContractType,
    DataFeed,
    EquityPoint,
    Fill,
    FundingPayment,
    Instrument,
    LiquidityRole,
    MarginBracket,
    MarginMode,
    NoOpStrategy,
    NullBrokerAdapter,
    NullClock,
    NullDataFeed,
    NullReconciler,
    NullRiskEngine,
    NullRunner,
    NullStrategySandbox,
    Order,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Reconciler,
    RiskEngine,
    RunEnv,
    RunRecord,
    Runner,
    Signal,
    SignalAction,
    Strategy,
    StrategyConfig,
    StrategyContext,
    StrategySandbox,
    SymbolSpec,
    TargetPosition,
    Tick,
    TimeInForce,
    compute_run_id,
)
from quant_platform.db import (
    Base,
    OrderRecord,
    UnifiedLedgerRepository,
    append_audit_log,
    build_engine,
)
from quant_platform.exceptions import AuditImmutabilityError, FloatForbiddenError


def test_all_monetary_and_quantity_fields_reject_float() -> None:
    """Acceptance ②: float is strictly forbidden in domain models."""
    with pytest.raises((ValidationError, FloatForbiddenError)):
        Bar(
            exchange="binance",
            symbol="BTC/USDT",
            interval="1m",
            open_time_ms=1700000000000,
            close_time_ms=1700000059999,
            open=50000.12,  # type: ignore[arg-type]
            high=Decimal("50100"),
            low=Decimal("49900"),
            close=Decimal("50050"),
            volume=Decimal("12.5"),
            exchange_ts_ms=1700000059999,
            local_recv_ts_ms=1700000060005,
        )

    with pytest.raises((ValidationError, FloatForbiddenError)):
        Order(
            env=RunEnv.BACKTEST,
            run_id="run_test",
            strategy_id="s1",
            client_order_id="cid_1",
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000.00"),
            quantity=0.25,  # type: ignore[arg-type]
            remaining_qty=Decimal("0.25"),
            created_ts_ms=1000,
            updated_ts_ms=1000,
            exchange_ts_ms=1000,
            local_recv_ts_ms=1000,
        )


def test_no_float_columns_in_sqlalchemy_schema_and_postgres_ddl() -> None:
    """Acceptance ②: DB schema has zero Float columns; PostgreSQL DDL emits NUMERIC(38, 18)."""
    for table_name, table in Base.metadata.tables.items():
        for col in table.columns:
            assert not isinstance(col.type, Float), f"Forbidden Float column {table_name}.{col.name}"
        pg_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "FLOAT" not in pg_ddl.upper()
        assert "DOUBLE PRECISION" not in pg_ddl.upper()
        if table_name in {"orders", "fills", "positions", "balance_snapshots", "equity_curve", "funding_payments"}:
            assert "NUMERIC(38, 18)" in pg_ddl


def test_db_strict_decimal_rejects_float_and_preserves_18_decimals(db_session: Session) -> None:
    """Acceptance ②: DB layer rejects float at runtime and preserves exact 18-digit fixed-point Decimal."""
    bad_order = OrderRecord(
        env="backtest",
        run_id="run_bad",
        strategy_id="strat_1",
        client_order_id="cid_float",
        exchange="binance",
        symbol="BTC/USDT",
        side="BUY",
        position_side="NET",
        order_type="LIMIT",
        time_in_force="GTC",
        post_only=False,
        reduce_only=False,
        price=50000.123,  # type: ignore[arg-type]
        quantity=Decimal("1"),
        filled_qty=Decimal("0"),
        avg_fill_price=Decimal("0"),
        remaining_qty=Decimal("1"),
        fee_total=Decimal("0"),
        fee_asset="USDT",
        leverage=Decimal("1"),
        margin_mode="CROSS",
        status="NEW",
        symbol_spec_version="v1",
        created_ts_ms=1,
        updated_ts_ms=1,
        exchange_ts_ms=1,
        local_recv_ts_ms=1,
    )
    db_session.add(bad_order)
    with pytest.raises(StatementError) as exc_info:
        db_session.flush()
    assert isinstance(exc_info.value.orig, FloatForbiddenError)
    db_session.rollback()

    exact_price = Decimal("65432.123456789012345678")
    exact_qty = Decimal("0.987654321098765432")
    repo = UnifiedLedgerRepository(db_session)
    repo.upsert_order(
        Order(
            env=RunEnv.BACKTEST,
            run_id="run_exact",
            strategy_id="strat_1",
            client_order_id="cid_exact",
            exchange="binance",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=exact_price,
            quantity=exact_qty,
            remaining_qty=exact_qty,
            created_ts_ms=1000,
            updated_ts_ms=1000,
            exchange_ts_ms=1000,
            local_recv_ts_ms=1000,
        )
    )
    db_session.commit()
    db_session.expire_all()

    loaded = repo.list_orders(RunEnv.BACKTEST, "run_exact")
    assert len(loaded) == 1
    assert isinstance(loaded[0].price, Decimal)
    assert loaded[0].price == exact_price
    assert loaded[0].quantity == exact_qty


def test_unified_three_environment_tables_share_exact_schema(db_session: Session) -> None:
    """Acceptance ① & Red Line 5: orders/fills/positions/balance_snapshots/equity_curve/funding_payments share exact schema across backtest, shadow, live."""
    repo = UnifiedLedgerRepository(db_session)

    for idx, env in enumerate([RunEnv.BACKTEST, RunEnv.SHADOW, RunEnv.LIVE], start=1):
        run_id = f"run_{env.value}_001"
        cid = f"cid_{idx}"
        repo.upsert_order(
            Order(
                env=env,
                run_id=run_id,
                strategy_id="ma_cross",
                client_order_id=cid,
                exchange_order_id=f"ex_{idx}",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("60000.00"),
                quantity=Decimal("0.100"),
                filled_qty=Decimal("0.100"),
                avg_fill_price=Decimal("60000.00"),
                remaining_qty=Decimal("0"),
                fee_total=Decimal("1.20"),
                fee_asset="USDT",
                leverage=Decimal("5"),
                status=OrderStatus.FILLED,
                created_ts_ms=1700000000000 + idx,
                updated_ts_ms=1700000000100 + idx,
                exchange_ts_ms=1700000000050 + idx,
                local_recv_ts_ms=1700000000080 + idx,
            )
        )
        repo.record_fill(
            Fill(
                fill_id=f"fill_{idx}",
                env=env,
                run_id=run_id,
                strategy_id="ma_cross",
                client_order_id=cid,
                exchange_order_id=f"ex_{idx}",
                exchange_trade_id=f"trd_{idx}",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                liquidity=LiquidityRole.MAKER,
                price=Decimal("60000.00"),
                quantity=Decimal("0.100"),
                quote_qty=Decimal("6000.00"),
                reference_price=Decimal("60000.00"),
                slippage_bps=Decimal("0"),
                fee=Decimal("1.20"),
                fee_asset="USDT",
                realized_pnl=Decimal("0"),
                mark_price=Decimal("60001.50"),
                exchange_ts_ms=1700000000050 + idx,
                local_recv_ts_ms=1700000000080 + idx,
            )
        )
        repo.upsert_position(
            Position(
                env=env,
                run_id=run_id,
                strategy_id="ma_cross",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                leverage=Decimal("5"),
                quantity=Decimal("0.100"),
                entry_price=Decimal("60000.00"),
                mark_price=Decimal("60100.00"),
                liquidation_price=Decimal("48240.00"),
                unrealized_pnl=Decimal("10.00"),
                updated_ts_ms=1700000001000 + idx,
            )
        )
        repo.record_balance_snapshot(
            Balance(
                env=env,
                run_id=run_id,
                exchange="binance",
                asset="USDT",
                total=Decimal("10000.00"),
                available=Decimal("8800.00"),
                locked=Decimal("1200.00"),
                ts_ms=1700000001000 + idx,
            )
        )
        repo.record_equity_point(
            EquityPoint(
                env=env,
                run_id=run_id,
                ts_ms=1700000001000 + idx,
                equity=Decimal("10008.80"),
                cash=Decimal("9998.80"),
                position_value=Decimal("6010.00"),
                unrealized_pnl=Decimal("10.00"),
                cumulative_fees=Decimal("1.20"),
            )
        )
        repo.record_funding_payment(
            FundingPayment(
                payment_id=f"fp_{idx}",
                env=env,
                run_id=run_id,
                exchange="binance",
                symbol="BTC/USDT:USDT",
                funding_rate=Decimal("0.0001"),
                mark_price=Decimal("60100.00"),
                position_qty=Decimal("0.100"),
                amount=Decimal("-0.601"),
                asset="USDT",
                settlement_ts_ms=1700028800000,
                local_recv_ts_ms=1700028800010,
            )
        )
    db_session.commit()

    for env in [RunEnv.BACKTEST, RunEnv.SHADOW, RunEnv.LIVE]:
        rid = f"run_{env.value}_001"
        assert len(repo.list_orders(env, rid)) == 1
        assert len(repo.list_fills(env, rid)) == 1
        assert len(repo.list_positions(env, rid)) == 1
        assert len(repo.list_funding_payments(env, rid)) == 1


def test_alembic_reversible_migrations(tmp_path: Path) -> None:
    """Acceptance ③: Alembic migrations cleanly execute upgrade head -> downgrade base -> upgrade head."""
    mig_db = tmp_path / "alembic_test.sqlite3"
    url = f"sqlite:///{mig_db}"

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging_config"] = True

    command.upgrade(cfg, "head")
    engine = build_engine(url)
    tables_up = set(inspect(engine).get_table_names())
    expected_tables = {
        "instruments",
        "symbol_specs",
        "strategies",
        "strategy_versions",
        "runs",
        "orders",
        "fills",
        "positions",
        "balance_snapshots",
        "equity_curve",
        "funding_payments",
        "order_id_mappings",
        "signal_traces",
        "risk_events",
        "reconciliation_reports",
        "api_key_records",
        "audit_logs",
        "alembic_version",
    }
    assert expected_tables.issubset(tables_up)
    engine.dispose()

    command.downgrade(cfg, "base")
    engine = build_engine(url)
    tables_down = set(inspect(engine).get_table_names())
    assert tables_down <= {"alembic_version"}
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = build_engine(url)
    tables_reup = set(inspect(engine).get_table_names())
    assert expected_tables.issubset(tables_reup)
    engine.dispose()


def test_audit_log_append_only_immutability(db_session: Session) -> None:
    """Red Line 8: audit_logs cannot be updated or deleted via ORM or raw SQL."""
    entry1 = append_audit_log(
        db_session,
        actor="alice",
        action="CREATE_STRATEGY",
        resource_type="strategy",
        resource_id="strat_01",
        payload={"version": 1},
        ts_ms=1700000000000,
    )
    entry2 = append_audit_log(
        db_session,
        actor="bob",
        action="APPROVE_STRATEGY",
        resource_type="strategy_version",
        resource_id="strat_01:v1",
        payload={"approved": True},
        ts_ms=1700000005000,
    )
    db_session.commit()
    assert entry1.prev_hash == "GENESIS"
    assert entry2.prev_hash == entry1.entry_hash

    entry1.actor = "mallory"
    with pytest.raises(AuditImmutabilityError):
        db_session.flush()
    db_session.rollback()

    reloaded = db_session.execute(select(type(entry1)).where(type(entry1).id == entry1.id)).scalar_one()
    db_session.delete(reloaded)
    with pytest.raises(AuditImmutabilityError):
        db_session.flush()
    db_session.rollback()

    with pytest.raises(DBAPIError):
        db_session.execute(text("DELETE FROM audit_logs WHERE id = 1"))
    db_session.rollback()

    with pytest.raises(DBAPIError):
        db_session.execute(text("UPDATE audit_logs SET actor = 'mallory' WHERE id = 1"))
    db_session.rollback()


def test_all_14_domain_models_and_8_interfaces_and_deterministic_run_id() -> None:
    """Verify all 14 core domain models, deterministic run_id (Red Line 6), and 8 core interfaces."""
    inst = Instrument(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        base_asset="BTC",
        quote_asset="USDT",
        settle_asset="USDT",
        contract_type=ContractType.PERPETUAL,
    )
    spec = SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        version=1,
        effective_from_ms=1700000000000,
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5.0"),
        max_leverage=Decimal("125"),
        margin_brackets=[
            MarginBracket(
                bracket=1,
                notional_floor=Decimal("0"),
                notional_cap=Decimal("50000"),
                max_leverage=Decimal("125"),
                maint_margin_rate=Decimal("0.004"),
                maint_amount=Decimal("0"),
            )
        ],
    )
    assert inst.instrument_key == "binance:BTC/USDT:USDT"
    assert spec.spec_hash != ""

    ob = OrderBookSnapshot(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(price=Decimal("59999.9"), quantity=Decimal("1.5"))],
        asks=[OrderBookLevel(price=Decimal("60000.1"), quantity=Decimal("2.0"))],
        last_update_id=101,
        exchange_ts_ms=1700000000000,
        local_recv_ts_ms=1700000000005,
    )
    assert ob.mid_price == Decimal("60000.0")

    tick = Tick(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        trade_id="1001",
        price=Decimal("60000.0"),
        quantity=Decimal("0.05"),
        exchange_ts_ms=1700000000000,
        local_recv_ts_ms=1700000000005,
    )
    assert tick.price == Decimal("60000.0")

    cfg = StrategyConfig(
        strategy_id="demo_strat",
        strategy_version_id="demo_strat:v1",
        name="Demo Strategy",
        exchanges=["binance"],
        symbols=["BTC/USDT:USDT"],
        params={"fast": 10, "slow": 30},
        initial_balances={"USDT": Decimal("10000")},
    )
    rid_1 = compute_run_id(
        strategy_code="class MyStrat(Strategy): pass",
        config_snapshot=cfg.model_dump(mode="json"),
        dataset_version="ds_abc123",
        symbol_spec_version=spec.spec_version_id,
    )
    rid_2 = compute_run_id(
        strategy_code="class MyStrat(Strategy): pass",
        config_snapshot=cfg.model_dump(mode="json"),
        dataset_version="ds_abc123",
        symbol_spec_version=spec.spec_version_id,
    )
    assert rid_1 == rid_2
    assert rid_1.startswith("run_")

    clock: Clock = NullClock(1700000000000)
    feed: DataFeed = NullDataFeed(clock)
    broker: BrokerAdapter = NullBrokerAdapter()
    risk: RiskEngine = NullRiskEngine()
    sandbox: StrategySandbox = NullStrategySandbox()
    reconciler: Reconciler = NullReconciler()

    ctx = StrategyContext(
        clock=clock,
        data_feed=feed,
        config=cfg,
        rng=random.Random(cfg.rng_seed),
    )
    assert not hasattr(ctx, "env")
    assert not hasattr(ctx, "broker")

    strat: Strategy = NoOpStrategy(ctx)
    strat.emit_signal(
        Signal(
            strategy_id="demo_strat",
            exchange="binance",
            symbol="BTC/USDT:USDT",
            action=SignalAction.BUY,
            quantity=Decimal("0.01"),
            ts_ms=ctx.now_ms,
        )
    )
    strat.emit_target_position(
        TargetPosition(
            strategy_id="demo_strat",
            exchange="binance",
            symbol="BTC/USDT:USDT",
            target_qty=Decimal("0.05"),
            ts_ms=ctx.now_ms,
        )
    )
    intents = strat.drain_intents()
    assert len(intents) == 2
    assert strat.drain_intents() == []

    run_rec = RunRecord(
        run_id=rid_1,
        env=RunEnv.BACKTEST,
        strategy_id="demo_strat",
        strategy_version_id="demo_strat:v1",
        strategy_code_hash="hash",
        config_snapshot=cfg.model_dump(mode="json"),
        dataset_version="ds_abc123",
        symbol_spec_version=spec.spec_version_id,
        started_at_ms=clock.now_ms(),
    )
    runner: Runner = NullRunner(run_rec)
    assert runner.run().run_id == rid_1
    assert broker.startup_sync()["ready"] is True
    assert sandbox.validate_code("x = 1")["passed"] is True
    assert risk.check_post_trade(
        Fill(
            env=RunEnv.BACKTEST,
            run_id=rid_1,
            strategy_id="demo_strat",
            client_order_id="c1",
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            liquidity=LiquidityRole.TAKER,
            price=Decimal("60000"),
            quantity=Decimal("0.01"),
            quote_qty=Decimal("600"),
            reference_price=Decimal("60000"),
            fee=Decimal("0.24"),
            fee_asset="USDT",
            exchange_ts_ms=1700000000000,
            local_recv_ts_ms=1700000000000,
        )
    ) == []
    assert reconciler.reconcile_exchange_vs_local(rid_1, broker)["discrepancies"] == []
