"""SQLAlchemy 2.0 ORM schema shared across Backtest, Shadow, and Live environments."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, Integer, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from quant_platform.db.types import StrictDecimalColumn
from quant_platform.exceptions import AuditImmutabilityError


class Base(DeclarativeBase):
    pass


class InstrumentRecord(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    settle_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SPOT")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="TRADING")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SymbolSpecRecord(Base):
    __tablename__ = "symbol_specs"
    __table_args__ = (
        UniqueConstraint("spec_version_id", name="uq_symbol_specs_version_id"),
        UniqueConstraint("exchange", "symbol", "version", name="uq_symbol_specs_ex_sym_ver"),
        Index("ix_symbol_specs_lookup", "exchange", "symbol", "effective_from_ms"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    spec_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_to_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contract_type: Mapped[str] = mapped_column(String(16), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    margin_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    step_size: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    min_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    max_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    contract_size: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    maker_fee: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    taker_fee: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    fee_tiers_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    max_leverage: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    margin_brackets_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    funding_interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    settlement_ts_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delisted_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StrategyMetaRecord(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("strategy_id", name="uq_strategies_strategy_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StrategyVersionRecord(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_strategy_versions_version_id"),
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strat_ver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    param_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    generation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    ast_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    backtest_gate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RunOrmRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("run_id", "env", name="uq_runs_run_id_env"),
        Index("ix_runs_strategy_env", "strategy_id", "env"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol_spec_version: Mapped[str] = mapped_column(String(256), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rng_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    started_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# Unified Three-Environment Trading Record Tables (Red Line 5)
# Shared across env in {'backtest', 'shadow', 'live'}, keyed by (env, run_id)
# ---------------------------------------------------------------------------


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("env", "run_id", "client_order_id", name="uq_orders_env_run_cid"),
        Index("ix_orders_env_run", "env", "run_id"),
        Index("ix_orders_symbol_status", "exchange", "symbol", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False, default="NET")
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="GTC")
    post_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price: Mapped[Decimal | None] = mapped_column(StrictDecimalColumn(), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(StrictDecimalColumn(), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    filled_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    avg_fill_price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    remaining_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    fee_total: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    fee_asset: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    leverage: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    margin_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="CROSS")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol_spec_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exchange_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    local_recv_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class FillRecord(Base):
    __tablename__ = "fills"
    __table_args__ = (
        UniqueConstraint("env", "run_id", "fill_id", name="uq_fills_env_run_fill_id"),
        Index("ix_fills_env_run", "env", "run_id"),
        Index("ix_fills_cid", "client_order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    liquidity: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    quote_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    slippage_bps: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    fee: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    fee_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(StrictDecimalColumn(), nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exchange_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    local_recv_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "env",
            "run_id",
            "exchange",
            "symbol",
            "position_side",
            name="uq_positions_env_run_ex_sym_side",
        ),
        Index("ix_positions_env_run", "env", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False, default="NET")
    margin_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="CROSS")
    leverage: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    liquidation_price: Mapped[Decimal | None] = mapped_column(StrictDecimalColumn(), nullable=True)
    unrealized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    isolated_margin: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    initial_margin: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    maintenance_margin: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    cumulative_funding: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    updated_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BalanceSnapshotRecord(Base):
    __tablename__ = "balance_snapshots"
    __table_args__ = (Index("ix_balance_snapshots_env_run_ts", "env", "run_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    total: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    available: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    locked: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    borrowed: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    accrued_interest: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EquityCurveRecord(Base):
    __tablename__ = "equity_curve"
    __table_args__ = (Index("ix_equity_curve_env_run_ts", "env", "run_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    equity: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    cash: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    position_value: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    cumulative_fees: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    cumulative_funding: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    cumulative_interest: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    gross_exposure: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    net_exposure: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)


class FundingPaymentRecord(Base):
    __tablename__ = "funding_payments"
    __table_args__ = (
        UniqueConstraint("env", "run_id", "payment_id", name="uq_funding_payments_env_run_pid"),
        Index("ix_funding_payments_env_run_ts", "env", "run_id", "settlement_ts_ms"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    funding_rate: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    position_qty: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(StrictDecimalColumn(), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    local_recv_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# Supporting Execution, Risk, Reconciliation, KeyVault & Audit Tables
# ---------------------------------------------------------------------------


class OrderIdMappingRecord(Base):
    __tablename__ = "order_id_mappings"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_order_id_mappings_cid"),
        Index("ix_order_id_mappings_ex_oid", "exchange", "exchange_order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SignalTraceRecord(Base):
    __tablename__ = "signal_traces"
    __table_args__ = (Index("ix_signal_traces_env_run", "env", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_ts_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class RiskEventRecord(Base):
    __tablename__ = "risk_events"
    __table_args__ = (Index("ix_risk_events_env_run_ts", "env", "run_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReconciliationReportRecord(Base):
    __tablename__ = "reconciliation_reports"
    __table_args__ = (Index("ix_recon_reports_tier_ts", "tier", "created_ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    discrepancy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_severity: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ApiKeyVaultRecord(Base):
    __tablename__ = "api_key_records"
    __table_args__ = (UniqueConstraint("key_id", name="uq_api_key_records_key_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    account_label: Mapped[str] = mapped_column(String(64), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_passphrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_whitelist_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    can_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    can_withdraw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_testnet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rotated_at_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AuditLogRecord(Base):
    """Append-only tamper-evident audit trail (Red Line 8)."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_res_ts", "resource_type", "resource_id", "ts_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


@event.listens_for(AuditLogRecord, "before_update")
def _forbid_audit_update(mapper: Any, connection: Any, target: Any) -> None:
    raise AuditImmutabilityError()


@event.listens_for(AuditLogRecord, "before_delete")
def _forbid_audit_delete(mapper: Any, connection: Any, target: Any) -> None:
    raise AuditImmutabilityError()
