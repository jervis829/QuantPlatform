"""Unified ExecutionCoordinator: state machine enforcement, idempotency, timeout recovery, and drift detection (P6)."""

from __future__ import annotations

from typing import Any

from quant_platform.core.decimal_utils import ZERO
from quant_platform.core.enums import OrderStatus, RunEnv
from quant_platform.core.interfaces import BrokerAdapter, Clock, FillCallback, OrderUpdateCallback
from quant_platform.core.models import Fill, Order, OrderRequest, Position
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.execution.id_mapping import OrderIdMapper
from quant_platform.execution.rate_limiter import WeightRateLimiter
from quant_platform.execution.state_machine import (
    STATUS_PROGRESSION_RANK,
    TERMINAL_ORDER_STATUSES,
    OrderStateMachine,
)


class ExecutionCoordinator:
    """
    Environment-agnostic order execution coordinator sitting between UnifiedExecutionPipeline and BrokerAdapter.
    Enforces:
    - OrderStateMachine legal transitions;
    - Global clientOrderId <-> exchangeOrderId bidirectional mapping;
    - Idempotent deduplication of duplicate & out-of-order WS reports;
    - Submit timeout recovery querying authoritative exchange state (Red Line 10);
    - Reconnect state synchronization.
    """

    def __init__(
        self,
        *,
        env: RunEnv,
        run_id: str,
        strategy_id: str,
        broker: BrokerAdapter,
        ledger: UnifiedLedgerRepository,
        id_mapper: OrderIdMapper,
        clock: Clock,
        rate_limiter: WeightRateLimiter | None = None,
    ) -> None:
        self.env = env
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.broker = broker
        self.ledger = ledger
        self.id_mapper = id_mapper
        self.clock = clock
        self.rate_limiter = rate_limiter or WeightRateLimiter()

        self._state_machines: dict[str, OrderStateMachine] = {}
        self._local_orders: dict[str, Order] = {}
        self._local_positions: dict[tuple[str, str], Position] = {}
        self._seen_fill_keys: set[tuple[str, str]] = set()
        self._order_listeners: list[OrderUpdateCallback] = []
        self._fill_listeners: list[FillCallback] = []

        self.broker.register_order_callback(self.on_order_report)
        self.broker.register_fill_callback(self.on_fill_report)

    def register_order_listener(self, cb: OrderUpdateCallback) -> None:
        self._order_listeners.append(cb)

    def register_fill_listener(self, cb: FillCallback) -> None:
        self._fill_listeners.append(cb)

    def get_local_order(self, client_order_id: str) -> Order | None:
        return self._local_orders.get(client_order_id)

    def list_local_open_orders(self) -> list[Order]:
        return [o for o in self._local_orders.values() if o.status not in TERMINAL_ORDER_STATUSES]

    def submit_order(self, request: OrderRequest, max_query_retries: int = 3) -> Order:
        cid = request.client_order_id
        now = self.clock.now_ms()
        self._state_machines.setdefault(cid, OrderStateMachine(cid, OrderStatus.PENDING_SUBMIT))
        self.id_mapper.bind(
            client_order_id=cid,
            exchange=request.exchange,
            symbol=request.symbol,
            exchange_order_id=None,
            env=self.env,
            run_id=self.run_id,
            ts_ms=now,
        )

        self.rate_limiter.acquire(weight=1)
        try:
            order = self.broker.submit_order(request)
            self.on_order_report(order)
            return self._local_orders[cid]
        except TimeoutError:
            # Red Line 10 & P6: Never assume failure locally on timeout — query exchange truth!
            for _ in range(max_query_retries):
                self.rate_limiter.acquire(weight=2)
                remote_ord = self.broker.query_order(cid, request.exchange, request.symbol)
                if remote_ord is not None:
                    # Also sync any fills recorded on exchange during timeout window
                    recorded_fills = getattr(self.broker, "recorded_fills_by_cid", {}).get(cid, [])
                    for rf in recorded_fills:
                        self.on_fill_report(rf)
                    self.on_order_report(remote_ord)
                    self._sync_positions_from_broker(request.exchange, request.symbol)
                    return self._local_orders[cid]
            raise

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        self.rate_limiter.acquire(weight=1)
        canceled = self.broker.cancel_order(client_order_id, exchange, symbol)
        self.on_order_report(canceled)
        return self._local_orders[client_order_id]

    def on_order_report(self, order: Order) -> None:
        cid = order.client_order_id
        sm = self._state_machines.setdefault(cid, OrderStateMachine(cid, OrderStatus.PENDING_SUBMIT))
        existing = self._local_orders.get(cid)

        self.id_mapper.bind(
            client_order_id=cid,
            exchange=order.exchange,
            symbol=order.symbol,
            exchange_order_id=order.exchange_order_id,
            env=self.env,
            run_id=self.run_id,
            ts_ms=order.updated_ts_ms,
        )

        # Idempotent duplicate & out-of-order stale packet protection
        if existing is not None:
            if order.filled_qty < existing.filled_qty:
                return
            if sm.is_terminal:
                return
            if (
                sm.status == order.status
                and order.filled_qty == existing.filled_qty
                and order.exchange_order_id == existing.exchange_order_id
            ):
                return
            if (
                STATUS_PROGRESSION_RANK[order.status] < STATUS_PROGRESSION_RANK[sm.status]
                and order.exchange_ts_ms <= existing.exchange_ts_ms
            ):
                return

        if sm.status != order.status:
            sm.transition_to(order.status)

        self._local_orders[cid] = order
        self.ledger.upsert_order(order)
        for cb in self._order_listeners:
            cb(order)

    def on_fill_report(self, fill: Fill) -> None:
        dedup_id = fill.exchange_trade_id or fill.fill_id
        dedup_key = (fill.exchange, dedup_id)
        if dedup_key in self._seen_fill_keys:
            return
        self._seen_fill_keys.add(dedup_key)

        self.ledger.record_fill(fill)
        self._sync_positions_from_broker(fill.exchange, fill.symbol)
        for cb in self._fill_listeners:
            cb(fill)

    def _sync_positions_from_broker(self, exchange: str | None = None, symbol: str | None = None) -> None:
        for pos in self.broker.query_positions(exchange, symbol):
            self._local_positions[(pos.exchange, pos.symbol)] = pos
            self.ledger.upsert_position(pos)

    def recover_after_disconnect(self) -> dict[str, Any]:
        """Reconcile all non-terminal local orders, open remote orders, fills, and positions after WS reconnect."""
        self.rate_limiter.acquire(weight=5)
        remote_open = {o.client_order_id: o for o in self.broker.query_open_orders()}

        # 1. Check all non-terminal local orders on exchange
        for cid, local_ord in list(self._local_orders.items()):
            if local_ord.status in TERMINAL_ORDER_STATUSES:
                continue
            remote_ord = remote_open.get(cid) or self.broker.query_order(cid, local_ord.exchange, local_ord.symbol)
            if remote_ord is not None:
                recorded_fills = getattr(self.broker, "recorded_fills_by_cid", {}).get(cid, [])
                for rf in recorded_fills:
                    self.on_fill_report(rf)
                self.on_order_report(remote_ord)

        # 2. Import any remote open orders missing locally
        for cid, ro in remote_open.items():
            if cid not in self._local_orders:
                self.on_order_report(ro)

        # 3. Sync authoritative positions
        self._sync_positions_from_broker()
        return {
            "open_orders": len(self.list_local_open_orders()),
            "positions": len(self._local_positions),
        }


def detect_state_drift(coordinator: ExecutionCoordinator, broker: BrokerAdapter) -> list[dict[str, Any]]:
    """Detect drift between local ExecutionCoordinator state and authoritative BrokerAdapter state."""
    drifts: list[dict[str, Any]] = []
    local_open = {o.client_order_id: o for o in coordinator.list_local_open_orders()}
    remote_open = {o.client_order_id: o for o in broker.query_open_orders()}

    for cid, loc in local_open.items():
        if cid not in remote_open:
            remote_actual = broker.query_order(cid, loc.exchange, loc.symbol)
            drifts.append(
                {
                    "type": "PHANTOM_LOCAL_OPEN_ORDER",
                    "client_order_id": cid,
                    "local_status": loc.status.value,
                    "remote_status": remote_actual.status.value if remote_actual else "MISSING",
                }
            )
        elif remote_open[cid].filled_qty != loc.filled_qty:
            drifts.append(
                {
                    "type": "ORDER_FILLED_QTY_DRIFT",
                    "client_order_id": cid,
                    "local_filled": str(loc.filled_qty),
                    "remote_filled": str(remote_open[cid].filled_qty),
                }
            )

    for cid, rem in remote_open.items():
        if cid not in local_open:
            drifts.append(
                {
                    "type": "MISSING_LOCAL_OPEN_ORDER",
                    "client_order_id": cid,
                    "remote_status": rem.status.value,
                }
            )

    remote_pos_map = {(p.exchange, p.symbol): p for p in broker.query_positions() if p.quantity != ZERO}
    local_pos_map = {k: p for k, p in coordinator._local_positions.items() if p.quantity != ZERO}
    for key in set(remote_pos_map.keys()) | set(local_pos_map.keys()):
        rp = remote_pos_map.get(key)
        lp = local_pos_map.get(key)
        rq = rp.quantity if rp else ZERO
        lq = lp.quantity if lp else ZERO
        if rq != lq:
            drifts.append(
                {
                    "type": "POSITION_QTY_DRIFT",
                    "exchange": key[0],
                    "symbol": key[1],
                    "local_qty": str(lq),
                    "remote_qty": str(rq),
                }
            )

    return drifts
