"""Initial QuantPlatform unified schema

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-10-27 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from quant_platform.db.types import StrictDecimalColumn

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dec = StrictDecimalColumn()

    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(32), nullable=False),
        sa.Column("settle_asset", sa.String(32), nullable=False),
        sa.Column("contract_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    )
    op.create_index("ix_instruments_exchange", "instruments", ["exchange"])
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"])

    op.create_table(
        "symbol_specs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("spec_version_id", sa.String(128), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from_ms", sa.BigInteger(), nullable=False),
        sa.Column("effective_to_ms", sa.BigInteger(), nullable=True),
        sa.Column("contract_type", sa.String(16), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(32), nullable=False),
        sa.Column("margin_asset", sa.String(32), nullable=False),
        sa.Column("tick_size", dec, nullable=False),
        sa.Column("step_size", dec, nullable=False),
        sa.Column("min_qty", dec, nullable=False),
        sa.Column("max_qty", dec, nullable=False),
        sa.Column("min_notional", dec, nullable=False),
        sa.Column("contract_size", dec, nullable=False),
        sa.Column("maker_fee", dec, nullable=False),
        sa.Column("taker_fee", dec, nullable=False),
        sa.Column("fee_tiers_json", sa.JSON(), nullable=False),
        sa.Column("max_leverage", dec, nullable=False),
        sa.Column("margin_brackets_json", sa.JSON(), nullable=False),
        sa.Column("funding_interval_hours", sa.Integer(), nullable=False),
        sa.Column("settlement_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("delisted_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("spec_version_id", name="uq_symbol_specs_version_id"),
        sa.UniqueConstraint("exchange", "symbol", "version", name="uq_symbol_specs_ex_sym_ver"),
    )
    op.create_index("ix_symbol_specs_lookup", "symbol_specs", ["exchange", "symbol", "effective_from_ms"])

    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("strategy_id", name="uq_strategies_strategy_id"),
    )
    op.create_index("ix_strategies_strategy_id", "strategies", ["strategy_id"])

    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.String(128), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("param_schema_json", sa.JSON(), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("diff_text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("generation_prompt", sa.Text(), nullable=True),
        sa.Column("ast_report_json", sa.JSON(), nullable=False),
        sa.Column("backtest_gate_json", sa.JSON(), nullable=True),
        sa.Column("approval_status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("version_id", name="uq_strategy_versions_version_id"),
        sa.UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strat_ver"),
    )
    op.create_index("ix_strategy_versions_version_id", "strategy_versions", ["version_id"])
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("strategy_version_id", sa.String(128), nullable=False),
        sa.Column("strategy_code_hash", sa.String(64), nullable=False),
        sa.Column("config_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("symbol_spec_version", sa.String(256), nullable=False),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("rng_seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("ended_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("run_id", "env", name="uq_runs_run_id_env"),
    )
    op.create_index("ix_runs_run_id", "runs", ["run_id"])
    op.create_index("ix_runs_env", "runs", ["env"])
    op.create_index("ix_runs_strategy_id", "runs", ["strategy_id"])
    op.create_index("ix_runs_strategy_env", "runs", ["strategy_id", "env"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("time_in_force", sa.String(8), nullable=False),
        sa.Column("post_only", sa.Boolean(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("price", dec, nullable=True),
        sa.Column("stop_price", dec, nullable=True),
        sa.Column("quantity", dec, nullable=False),
        sa.Column("filled_qty", dec, nullable=False),
        sa.Column("avg_fill_price", dec, nullable=False),
        sa.Column("remaining_qty", dec, nullable=False),
        sa.Column("fee_total", dec, nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=False),
        sa.Column("leverage", dec, nullable=False),
        sa.Column("margin_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("signal_id", sa.String(64), nullable=True),
        sa.Column("symbol_spec_version", sa.String(128), nullable=False),
        sa.Column("created_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("exchange_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("local_recv_ts_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("env", "run_id", "client_order_id", name="uq_orders_env_run_cid"),
    )
    op.create_index("ix_orders_client_order_id", "orders", ["client_order_id"])
    op.create_index("ix_orders_exchange_order_id", "orders", ["exchange_order_id"])
    op.create_index("ix_orders_env_run", "orders", ["env", "run_id"])
    op.create_index("ix_orders_symbol_status", "orders", ["exchange", "symbol", "status"])

    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fill_id", sa.String(64), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("exchange_trade_id", sa.String(64), nullable=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("liquidity", sa.String(8), nullable=False),
        sa.Column("price", dec, nullable=False),
        sa.Column("quantity", dec, nullable=False),
        sa.Column("quote_qty", dec, nullable=False),
        sa.Column("reference_price", dec, nullable=False),
        sa.Column("slippage_bps", dec, nullable=False),
        sa.Column("fee", dec, nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=False),
        sa.Column("realized_pnl", dec, nullable=False),
        sa.Column("mark_price", dec, nullable=True),
        sa.Column("snapshot_ref", sa.String(128), nullable=True),
        sa.Column("exchange_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("local_recv_ts_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("env", "run_id", "fill_id", name="uq_fills_env_run_fill_id"),
    )
    op.create_index("ix_fills_env_run", "fills", ["env", "run_id"])
    op.create_index("ix_fills_cid", "fills", ["client_order_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("margin_mode", sa.String(16), nullable=False),
        sa.Column("leverage", dec, nullable=False),
        sa.Column("quantity", dec, nullable=False),
        sa.Column("entry_price", dec, nullable=False),
        sa.Column("mark_price", dec, nullable=False),
        sa.Column("liquidation_price", dec, nullable=True),
        sa.Column("unrealized_pnl", dec, nullable=False),
        sa.Column("realized_pnl", dec, nullable=False),
        sa.Column("isolated_margin", dec, nullable=False),
        sa.Column("initial_margin", dec, nullable=False),
        sa.Column("maintenance_margin", dec, nullable=False),
        sa.Column("cumulative_funding", dec, nullable=False),
        sa.Column("updated_ts_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("env", "run_id", "exchange", "symbol", "position_side", name="uq_positions_env_run_ex_sym_side"),
    )
    op.create_index("ix_positions_env_run", "positions", ["env", "run_id"])

    op.create_table(
        "balance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("total", dec, nullable=False),
        sa.Column("available", dec, nullable=False),
        sa.Column("locked", dec, nullable=False),
        sa.Column("borrowed", dec, nullable=False),
        sa.Column("accrued_interest", dec, nullable=False),
        sa.Column("unrealized_pnl", dec, nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_balance_snapshots_env_run_ts", "balance_snapshots", ["env", "run_id", "ts_ms"])

    op.create_table(
        "equity_curve",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("equity", dec, nullable=False),
        sa.Column("cash", dec, nullable=False),
        sa.Column("position_value", dec, nullable=False),
        sa.Column("unrealized_pnl", dec, nullable=False),
        sa.Column("realized_pnl", dec, nullable=False),
        sa.Column("cumulative_fees", dec, nullable=False),
        sa.Column("cumulative_funding", dec, nullable=False),
        sa.Column("cumulative_interest", dec, nullable=False),
        sa.Column("drawdown_pct", dec, nullable=False),
        sa.Column("gross_exposure", dec, nullable=False),
        sa.Column("net_exposure", dec, nullable=False),
    )
    op.create_index("ix_equity_curve_env_run_ts", "equity_curve", ["env", "run_id", "ts_ms"])

    op.create_table(
        "funding_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("payment_id", sa.String(64), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("funding_rate", dec, nullable=False),
        sa.Column("mark_price", dec, nullable=False),
        sa.Column("position_qty", dec, nullable=False),
        sa.Column("amount", dec, nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("settlement_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("local_recv_ts_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("env", "run_id", "payment_id", name="uq_funding_payments_env_run_pid"),
    )
    op.create_index("ix_funding_payments_env_run_ts", "funding_payments", ["env", "run_id", "settlement_ts_ms"])

    op.create_table(
        "order_id_mappings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("created_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_ts_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("client_order_id", name="uq_order_id_mappings_cid"),
    )
    op.create_index("ix_order_id_mappings_ex_oid", "order_id_mappings", ["exchange", "exchange_order_id"])

    op.create_table(
        "signal_traces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(64), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("intent_type", sa.String(24), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=True),
        sa.Column("snapshot_ref", sa.String(128), nullable=True),
        sa.Column("signal_ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("order_ts_ms", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_signal_traces_signal_id", "signal_traces", ["signal_id"])
    op.create_index("ix_signal_traces_env_run", "signal_traces", ["env", "run_id"])

    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_risk_events_env_run_ts", "risk_events", ["env", "run_id", "ts_ms"])

    op.create_table(
        "reconciliation_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("reference_run_id", sa.String(64), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("discrepancy_count", sa.Integer(), nullable=False),
        sa.Column("max_severity", sa.String(16), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("created_ts_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_recon_reports_tier_ts", "reconciliation_reports", ["tier", "created_ts_ms"])

    op.create_table(
        "api_key_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("account_label", sa.String(64), nullable=False),
        sa.Column("env", sa.String(16), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("encrypted_api_secret", sa.Text(), nullable=False),
        sa.Column("encrypted_passphrase", sa.Text(), nullable=True),
        sa.Column("ip_whitelist_json", sa.JSON(), nullable=False),
        sa.Column("can_trade", sa.Boolean(), nullable=False),
        sa.Column("can_withdraw", sa.Boolean(), nullable=False),
        sa.Column("is_testnet", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("rotated_at_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("key_id", name="uq_api_key_records_key_id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_audit_logs_res_ts", "audit_logs", ["resource_type", "resource_id", "ts_ms"])

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE: audit_logs cannot be updated');
            END;
            """
        )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE: audit_logs cannot be deleted');
            END;
            """
        )
    elif bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION forbid_audit_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'AUDIT_IMMUTABLE: audit_logs is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_audit_logs_immutable
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update;")
        op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_delete;")
    elif bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs;")
        op.execute("DROP FUNCTION IF EXISTS forbid_audit_mutation();")

    for table in [
        "audit_logs",
        "api_key_records",
        "reconciliation_reports",
        "risk_events",
        "signal_traces",
        "order_id_mappings",
        "funding_payments",
        "equity_curve",
        "balance_snapshots",
        "positions",
        "fills",
        "orders",
        "runs",
        "strategy_versions",
        "strategies",
        "symbol_specs",
        "instruments",
    ]:
        op.drop_table(table)
