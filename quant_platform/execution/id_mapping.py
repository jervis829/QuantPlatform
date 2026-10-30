"""Global unique clientOrderId generator and bidirectional exchange order ID mapper (P6 / Red Line 10)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.enums import RunEnv
from quant_platform.core.hashing import sha256_hex
from quant_platform.db.schema import OrderIdMappingRecord


def generate_client_order_id(run_id: str, strategy_id: str, seq: int, unique_nonce: str | None = None) -> str:
    """
    Generate a globally unique, exchange-compliant (<= 36 alphanumeric/underscore chars) clientOrderId.
    """
    nonce = unique_nonce or uuid.uuid4().hex[:8]
    tag = sha256_hex(f"{run_id}:{strategy_id}:{seq}:{nonce}")[:16]
    return f"qp_{seq:06d}_{tag}"


class OrderIdMapper:
    """Bidirectional mapping between local clientOrderId and exchangeOrderId."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def bind(
        self,
        *,
        client_order_id: str,
        exchange: str,
        symbol: str,
        exchange_order_id: str | None,
        env: RunEnv,
        run_id: str,
        ts_ms: int,
    ) -> OrderIdMappingRecord:
        existing = self.session.execute(
            select(OrderIdMappingRecord).where(OrderIdMappingRecord.client_order_id == client_order_id)
        ).scalar_one_or_none()
        if existing is None:
            row = OrderIdMappingRecord(
                client_order_id=client_order_id,
                exchange=exchange,
                symbol=symbol,
                exchange_order_id=exchange_order_id,
                env=env.value,
                run_id=run_id,
                created_ts_ms=ts_ms,
                updated_ts_ms=ts_ms,
            )
            self.session.add(row)
        else:
            row = existing
            if exchange_order_id is not None:
                row.exchange_order_id = exchange_order_id
            row.updated_ts_ms = ts_ms
        self.session.flush()
        return row

    def get_exchange_order_id(self, client_order_id: str) -> str | None:
        row = self.session.execute(
            select(OrderIdMappingRecord).where(OrderIdMappingRecord.client_order_id == client_order_id)
        ).scalar_one_or_none()
        return row.exchange_order_id if row else None

    def get_client_order_id(self, exchange: str, exchange_order_id: str) -> str | None:
        row = self.session.execute(
            select(OrderIdMappingRecord).where(
                OrderIdMappingRecord.exchange == exchange,
                OrderIdMappingRecord.exchange_order_id == exchange_order_id,
            )
        ).scalar_one_or_none()
        return row.client_order_id if row else None
