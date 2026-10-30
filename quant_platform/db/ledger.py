"""Unified ledger repository persisting trading records identically across environments (Red Line 5)."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from quant_platform.core.enums import (
    LiquidityRole,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RunEnv,
    RunStatus,
    TimeInForce,
)
from quant_platform.core.models import (
    Balance,
    EquityPoint,
    Fill,
    FundingPayment,
    Order,
    Position,
    RunRecord,
)
from quant_platform.db.schema import (
    BalanceSnapshotRecord,
    EquityCurveRecord,
    FillRecord,
    FundingPaymentRecord,
    OrderRecord,
    PositionRecord,
    RunOrmRecord,
)


class UnifiedLedgerRepository:
    """Single persistence gateway shared by Backtest, Shadow, and Live."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def clear_run_ledger(self, env: RunEnv, run_id: str) -> None:
        """Idempotently clear prior run ledger rows before deterministic backtest replay."""
        for model in (
            EquityCurveRecord,
            BalanceSnapshotRecord,
            FundingPaymentRecord,
            FillRecord,
            OrderRecord,
            PositionRecord,
        ):
            self.session.execute(
                delete(model).where(model.env == env.value, model.run_id == run_id)
            )
        self.session.flush()

    def upsert_run(self, run: RunRecord, created_at_ms: int | None = None) -> RunOrmRecord:
        existing = self.session.execute(
            select(RunOrmRecord).where(
                RunOrmRecord.run_id == run.run_id,
                RunOrmRecord.env == run.env.value,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = RunOrmRecord(
                run_id=run.run_id,
                env=run.env.value,
                strategy_id=run.strategy_id,
                strategy_version_id=run.strategy_version_id,
                strategy_code_hash=run.strategy_code_hash,
                config_snapshot_json=run.config_snapshot,
                dataset_version=run.dataset_version,
                symbol_spec_version=run.symbol_spec_version,
                engine_version=run.engine_version,
                rng_seed=run.rng_seed,
                status=run.status.value,
                started_at_ms=run.started_at_ms,
                ended_at_ms=run.ended_at_ms,
                error_message=run.error_message,
                report_json=run.report,
                metadata_json=run.metadata,
                created_at_ms=created_at_ms if created_at_ms is not None else run.started_at_ms,
            )
            self.session.add(row)
        else:
            row = existing
            row.status = run.status.value
            row.ended_at_ms = run.ended_at_ms
            row.error_message = run.error_message
            row.report_json = run.report
            row.metadata_json = run.metadata
        self.session.flush()
        return row

    def upsert_order(self, order: Order) -> OrderRecord:
        existing = self.session.execute(
            select(OrderRecord).where(
                OrderRecord.env == order.env.value,
                OrderRecord.run_id == order.run_id,
                OrderRecord.client_order_id == order.client_order_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = OrderRecord(
                env=order.env.value,
                run_id=order.run_id,
                strategy_id=order.strategy_id,
                client_order_id=order.client_order_id,
                exchange_order_id=order.exchange_order_id,
                exchange=order.exchange,
                symbol=order.symbol,
                side=order.side.value,
                position_side=order.position_side.value,
                order_type=order.order_type.value,
                time_in_force=order.time_in_force.value,
                post_only=order.post_only,
                reduce_only=order.reduce_only,
                price=order.price,
                stop_price=order.stop_price,
                quantity=order.quantity,
                filled_qty=order.filled_qty,
                avg_fill_price=order.avg_fill_price,
                remaining_qty=order.remaining_qty,
                fee_total=order.fee_total,
                fee_asset=order.fee_asset,
                leverage=order.leverage,
                margin_mode=order.margin_mode.value,
                status=order.status.value,
                reject_reason=order.reject_reason,
                signal_id=order.signal_id,
                symbol_spec_version=order.symbol_spec_version,
                created_ts_ms=order.created_ts_ms,
                updated_ts_ms=order.updated_ts_ms,
                exchange_ts_ms=order.exchange_ts_ms,
                local_recv_ts_ms=order.local_recv_ts_ms,
            )
            self.session.add(row)
        else:
            row = existing
            row.exchange_order_id = order.exchange_order_id
            row.filled_qty = order.filled_qty
            row.avg_fill_price = order.avg_fill_price
            row.remaining_qty = order.remaining_qty
            row.fee_total = order.fee_total
            row.fee_asset = order.fee_asset
            row.status = order.status.value
            row.reject_reason = order.reject_reason
            row.updated_ts_ms = order.updated_ts_ms
            row.exchange_ts_ms = order.exchange_ts_ms
            row.local_recv_ts_ms = order.local_recv_ts_ms
        self.session.flush()
        return row

    def record_fill(self, fill: Fill) -> FillRecord:
        existing = self.session.execute(
            select(FillRecord).where(
                FillRecord.env == fill.env.value,
                FillRecord.run_id == fill.run_id,
                FillRecord.fill_id == fill.fill_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = FillRecord(
            fill_id=fill.fill_id,
            env=fill.env.value,
            run_id=fill.run_id,
            strategy_id=fill.strategy_id,
            client_order_id=fill.client_order_id,
            exchange_order_id=fill.exchange_order_id,
            exchange_trade_id=fill.exchange_trade_id,
            exchange=fill.exchange,
            symbol=fill.symbol,
            side=fill.side.value,
            liquidity=fill.liquidity.value,
            price=fill.price,
            quantity=fill.quantity,
            quote_qty=fill.quote_qty,
            reference_price=fill.reference_price,
            slippage_bps=fill.slippage_bps,
            fee=fill.fee,
            fee_asset=fill.fee_asset,
            realized_pnl=fill.realized_pnl,
            mark_price=fill.mark_price,
            snapshot_ref=fill.snapshot_ref,
            exchange_ts_ms=fill.exchange_ts_ms,
            local_recv_ts_ms=fill.local_recv_ts_ms,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def upsert_position(self, pos: Position) -> PositionRecord:
        existing = self.session.execute(
            select(PositionRecord).where(
                PositionRecord.env == pos.env.value,
                PositionRecord.run_id == pos.run_id,
                PositionRecord.exchange == pos.exchange,
                PositionRecord.symbol == pos.symbol,
                PositionRecord.position_side == pos.position_side.value,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = PositionRecord(
                env=pos.env.value,
                run_id=pos.run_id,
                strategy_id=pos.strategy_id,
                exchange=pos.exchange,
                symbol=pos.symbol,
                position_side=pos.position_side.value,
                margin_mode=pos.margin_mode.value,
                leverage=pos.leverage,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                mark_price=pos.mark_price,
                liquidation_price=pos.liquidation_price,
                unrealized_pnl=pos.unrealized_pnl,
                realized_pnl=pos.realized_pnl,
                isolated_margin=pos.isolated_margin,
                initial_margin=pos.initial_margin,
                maintenance_margin=pos.maintenance_margin,
                cumulative_funding=pos.cumulative_funding,
                updated_ts_ms=pos.updated_ts_ms,
            )
            self.session.add(row)
        else:
            row = existing
            row.margin_mode = pos.margin_mode.value
            row.leverage = pos.leverage
            row.quantity = pos.quantity
            row.entry_price = pos.entry_price
            row.mark_price = pos.mark_price
            row.liquidation_price = pos.liquidation_price
            row.unrealized_pnl = pos.unrealized_pnl
            row.realized_pnl = pos.realized_pnl
            row.isolated_margin = pos.isolated_margin
            row.initial_margin = pos.initial_margin
            row.maintenance_margin = pos.maintenance_margin
            row.cumulative_funding = pos.cumulative_funding
            row.updated_ts_ms = pos.updated_ts_ms
        self.session.flush()
        return row

    def record_balance_snapshot(self, bal: Balance, *, flush: bool = True) -> BalanceSnapshotRecord:
        row = BalanceSnapshotRecord(
            env=bal.env.value,
            run_id=bal.run_id,
            exchange=bal.exchange,
            account_id=bal.account_id,
            asset=bal.asset,
            total=bal.total,
            available=bal.available,
            locked=bal.locked,
            borrowed=bal.borrowed,
            accrued_interest=bal.accrued_interest,
            unrealized_pnl=bal.unrealized_pnl,
            ts_ms=bal.ts_ms,
        )
        self.session.add(row)
        if flush:
            self.session.flush()
        return row

    def record_equity_point(self, pt: EquityPoint, *, flush: bool = True) -> EquityCurveRecord:
        row = EquityCurveRecord(
            env=pt.env.value,
            run_id=pt.run_id,
            ts_ms=pt.ts_ms,
            equity=pt.equity,
            cash=pt.cash,
            position_value=pt.position_value,
            unrealized_pnl=pt.unrealized_pnl,
            realized_pnl=pt.realized_pnl,
            cumulative_fees=pt.cumulative_fees,
            cumulative_funding=pt.cumulative_funding,
            cumulative_interest=pt.cumulative_interest,
            drawdown_pct=pt.drawdown_pct,
            gross_exposure=pt.gross_exposure,
            net_exposure=pt.net_exposure,
        )
        self.session.add(row)
        if flush:
            self.session.flush()
        return row

    def record_funding_payment(self, fp: FundingPayment) -> FundingPaymentRecord:
        existing = self.session.execute(
            select(FundingPaymentRecord).where(
                FundingPaymentRecord.env == fp.env.value,
                FundingPaymentRecord.run_id == fp.run_id,
                FundingPaymentRecord.payment_id == fp.payment_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = FundingPaymentRecord(
            payment_id=fp.payment_id,
            env=fp.env.value,
            run_id=fp.run_id,
            exchange=fp.exchange,
            symbol=fp.symbol,
            funding_rate=fp.funding_rate,
            mark_price=fp.mark_price,
            position_qty=fp.position_qty,
            amount=fp.amount,
            asset=fp.asset,
            settlement_ts_ms=fp.settlement_ts_ms,
            local_recv_ts_ms=fp.local_recv_ts_ms,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_orders(self, env: RunEnv, run_id: str) -> list[Order]:
        rows = self.session.execute(
            select(OrderRecord)
            .where(OrderRecord.env == env.value, OrderRecord.run_id == run_id)
            .order_by(OrderRecord.created_ts_ms.asc(), OrderRecord.id.asc())
        ).scalars().all()
        return [
            Order(
                env=RunEnv(r.env),
                run_id=r.run_id,
                strategy_id=r.strategy_id,
                client_order_id=r.client_order_id,
                exchange_order_id=r.exchange_order_id,
                exchange=r.exchange,
                symbol=r.symbol,
                side=OrderSide(r.side),
                position_side=PositionSide(r.position_side),
                order_type=OrderType(r.order_type),
                time_in_force=TimeInForce(r.time_in_force),
                post_only=r.post_only,
                reduce_only=r.reduce_only,
                price=r.price,
                stop_price=r.stop_price,
                quantity=r.quantity,
                filled_qty=r.filled_qty,
                avg_fill_price=r.avg_fill_price,
                remaining_qty=r.remaining_qty,
                fee_total=r.fee_total,
                fee_asset=r.fee_asset,
                leverage=r.leverage,
                margin_mode=MarginMode(r.margin_mode),
                status=OrderStatus(r.status),
                reject_reason=r.reject_reason,
                signal_id=r.signal_id,
                symbol_spec_version=r.symbol_spec_version,
                created_ts_ms=r.created_ts_ms,
                updated_ts_ms=r.updated_ts_ms,
                exchange_ts_ms=r.exchange_ts_ms,
                local_recv_ts_ms=r.local_recv_ts_ms,
            )
            for r in rows
        ]

    def list_fills(self, env: RunEnv, run_id: str) -> list[Fill]:
        rows = self.session.execute(
            select(FillRecord)
            .where(FillRecord.env == env.value, FillRecord.run_id == run_id)
            .order_by(FillRecord.exchange_ts_ms.asc(), FillRecord.id.asc())
        ).scalars().all()
        return [
            Fill(
                fill_id=r.fill_id,
                env=RunEnv(r.env),
                run_id=r.run_id,
                strategy_id=r.strategy_id,
                client_order_id=r.client_order_id,
                exchange_order_id=r.exchange_order_id,
                exchange_trade_id=r.exchange_trade_id,
                exchange=r.exchange,
                symbol=r.symbol,
                side=OrderSide(r.side),
                liquidity=LiquidityRole(r.liquidity),
                price=r.price,
                quantity=r.quantity,
                quote_qty=r.quote_qty,
                reference_price=r.reference_price,
                slippage_bps=r.slippage_bps,
                fee=r.fee,
                fee_asset=r.fee_asset,
                realized_pnl=r.realized_pnl,
                mark_price=r.mark_price,
                snapshot_ref=r.snapshot_ref,
                exchange_ts_ms=r.exchange_ts_ms,
                local_recv_ts_ms=r.local_recv_ts_ms,
            )
            for r in rows
        ]

    def list_positions(self, env: RunEnv, run_id: str) -> list[Position]:
        rows = self.session.execute(
            select(PositionRecord)
            .where(PositionRecord.env == env.value, PositionRecord.run_id == run_id)
            .order_by(PositionRecord.symbol.asc())
        ).scalars().all()
        return [
            Position(
                env=RunEnv(r.env),
                run_id=r.run_id,
                strategy_id=r.strategy_id,
                exchange=r.exchange,
                symbol=r.symbol,
                position_side=PositionSide(r.position_side),
                margin_mode=MarginMode(r.margin_mode),
                leverage=r.leverage,
                quantity=r.quantity,
                entry_price=r.entry_price,
                mark_price=r.mark_price,
                liquidation_price=r.liquidation_price,
                unrealized_pnl=r.unrealized_pnl,
                realized_pnl=r.realized_pnl,
                isolated_margin=r.isolated_margin,
                initial_margin=r.initial_margin,
                maintenance_margin=r.maintenance_margin,
                cumulative_funding=r.cumulative_funding,
                updated_ts_ms=r.updated_ts_ms,
            )
            for r in rows
        ]

    def list_funding_payments(self, env: RunEnv, run_id: str) -> list[FundingPayment]:
        rows = self.session.execute(
            select(FundingPaymentRecord)
            .where(FundingPaymentRecord.env == env.value, FundingPaymentRecord.run_id == run_id)
            .order_by(FundingPaymentRecord.settlement_ts_ms.asc())
        ).scalars().all()
        return [
            FundingPayment(
                payment_id=r.payment_id,
                env=RunEnv(r.env),
                run_id=r.run_id,
                exchange=r.exchange,
                symbol=r.symbol,
                funding_rate=r.funding_rate,
                mark_price=r.mark_price,
                position_qty=r.position_qty,
                amount=r.amount,
                asset=r.asset,
                settlement_ts_ms=r.settlement_ts_ms,
                local_recv_ts_ms=r.local_recv_ts_ms,
            )
            for r in rows
        ]

    def get_run(self, env: RunEnv, run_id: str) -> RunRecord | None:
        r = self.session.execute(
            select(RunOrmRecord).where(RunOrmRecord.env == env.value, RunOrmRecord.run_id == run_id)
        ).scalar_one_or_none()
        if r is None:
            return None
        return RunRecord(
            run_id=r.run_id,
            env=RunEnv(r.env),
            strategy_id=r.strategy_id,
            strategy_version_id=r.strategy_version_id,
            strategy_code_hash=r.strategy_code_hash,
            config_snapshot=r.config_snapshot_json,
            dataset_version=r.dataset_version,
            symbol_spec_version=r.symbol_spec_version,
            engine_version=r.engine_version,
            rng_seed=r.rng_seed,
            status=RunStatus(r.status),
            started_at_ms=r.started_at_ms,
            ended_at_ms=r.ended_at_ms,
            error_message=r.error_message,
            report=r.report_json,
            metadata=r.metadata_json,
        )
