"""End-to-end Shadow/Backtest/Live trace inspector: signal_ts -> order_ts -> fill_ts -> snapshot_ref (P7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.decimal_utils import ZERO
from quant_platform.core.enums import RunEnv
from quant_platform.db.schema import FillRecord, OrderRecord, SignalTraceRecord


class ShadowTraceInspector:
    """Reconstructs complete provenance chains across signal_traces, orders, and fills."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_run_traces(self, *, env: RunEnv, run_id: str) -> list[dict[str, Any]]:
        traces = list(
            self.session.execute(
                select(SignalTraceRecord)
                .where(SignalTraceRecord.env == env.value, SignalTraceRecord.run_id == run_id)
                .order_by(SignalTraceRecord.signal_ts_ms.asc(), SignalTraceRecord.id.asc())
            )
            .scalars()
            .all()
        )
        orders_by_cid = {
            o.client_order_id: o
            for o in self.session.execute(
                select(OrderRecord).where(OrderRecord.env == env.value, OrderRecord.run_id == run_id)
            )
            .scalars()
            .all()
        }
        fills_by_cid: dict[str, list[FillRecord]] = {}
        for f in (
            self.session.execute(
                select(FillRecord)
                .where(FillRecord.env == env.value, FillRecord.run_id == run_id)
                .order_by(FillRecord.exchange_ts_ms.asc(), FillRecord.id.asc())
            )
            .scalars()
            .all()
        ):
            fills_by_cid.setdefault(f.client_order_id, []).append(f)

        out: list[dict[str, Any]] = []
        for tr in traces:
            cid = tr.client_order_id
            ord_row = orders_by_cid.get(cid) if cid else None
            fills = fills_by_cid.get(cid, []) if cid else []
            first_fill = fills[0] if fills else None
            total_fill_qty = sum((f.quantity for f in fills), ZERO) if fills else None
            out.append(
                {
                    "signal_id": tr.signal_id,
                    "intent_type": tr.intent_type,
                    "exchange": tr.exchange,
                    "symbol": tr.symbol,
                    "client_order_id": cid,
                    "signal_ts_ms": tr.signal_ts_ms,
                    "order_ts_ms": ord_row.created_ts_ms if ord_row is not None else tr.order_ts_ms,
                    "fill_ts_ms": first_fill.exchange_ts_ms if first_fill is not None else None,
                    "snapshot_ref": (
                        first_fill.snapshot_ref
                        if first_fill is not None and first_fill.snapshot_ref
                        else tr.snapshot_ref
                    ),
                    "order_status": ord_row.status if ord_row is not None else None,
                    "fill_id": first_fill.fill_id if first_fill is not None else None,
                    "fill_price": ord_row.avg_fill_price if ord_row is not None and fills else None,
                    "fill_qty": total_fill_qty,
                }
            )
        return out
