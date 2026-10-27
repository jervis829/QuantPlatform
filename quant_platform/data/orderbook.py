"""OrderBook snapshot + incremental WebSocket delta sequence reconstruction (P1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from quant_platform.core.decimal_utils import ZERO
from quant_platform.core.models import OrderBookDelta, OrderBookLevel, OrderBookSnapshot
from quant_platform.exceptions import CrossExchangeIsolationError, DataIntegrityError


class OrderBookMaintainer:
    """
    Rebuilds a deterministic Level-2 order book from REST snapshots + WebSocket depth deltas.
    Follows Binance Spot & USDT-M Futures sequence validation rules:
    1) Buffer deltas arriving before initial snapshot;
    2) Discard buffered deltas with final_update_id <= snapshot.last_update_id;
    3) Require first applied delta to bridge snapshot: first_update_id <= last_update_id + 1 <= final_update_id;
    4) Require every subsequent delta to satisfy prev_final_update_id == last_update_id OR first_update_id == last_update_id + 1;
    5) On sequence gap, trigger rest_snapshot_fetcher if configured, else raise DataIntegrityError.
    """

    def __init__(
        self,
        exchange: str,
        symbol: str,
        rest_snapshot_fetcher: Callable[[str, str], OrderBookSnapshot] | None = None,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self._rest_snapshot_fetcher = rest_snapshot_fetcher
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._last_update_id: int | None = None
        self._exchange_ts_ms: int = 0
        self._local_recv_ts_ms: int = 0
        self._pending_deltas: list[OrderBookDelta] = []
        self._resync_count: int = 0

    @property
    def resync_count(self) -> int:
        return self._resync_count

    def _validate_identity(self, item_exchange: str, item_symbol: str) -> None:
        if item_exchange != self.exchange:
            raise CrossExchangeIsolationError(self.exchange, item_exchange, self.symbol)
        if item_symbol != self.symbol:
            raise DataIntegrityError(
                f"Symbol mismatch in OrderBookMaintainer: expected {self.symbol}, got {item_symbol}"
            )

    def apply_snapshot(self, snapshot: OrderBookSnapshot) -> OrderBookSnapshot:
        self._validate_identity(snapshot.exchange, snapshot.symbol)
        self._bids = {lvl.price: lvl.quantity for lvl in snapshot.bids if lvl.quantity > ZERO}
        self._asks = {lvl.price: lvl.quantity for lvl in snapshot.asks if lvl.quantity > ZERO}
        self._last_update_id = snapshot.last_update_id
        self._exchange_ts_ms = snapshot.exchange_ts_ms
        self._local_recv_ts_ms = snapshot.local_recv_ts_ms

        buffered = list(self._pending_deltas)
        self._pending_deltas.clear()
        bridged = False
        for delta in buffered:
            if delta.final_update_id <= self._last_update_id:
                continue
            if not bridged:
                if not (delta.first_update_id <= self._last_update_id + 1 <= delta.final_update_id):
                    raise DataIntegrityError(
                        "Buffered delta fails to bridge orderbook snapshot",
                        details={
                            "snapshot_last_update_id": self._last_update_id,
                            "delta_first_update_id": delta.first_update_id,
                            "delta_final_update_id": delta.final_update_id,
                        },
                    )
                bridged = True
                self._apply_levels(delta)
            else:
                self._apply_contiguous_delta(delta)
        self._assert_not_crossed()
        return self.current_snapshot()

    def push_delta(self, delta: OrderBookDelta) -> OrderBookSnapshot | None:
        self._validate_identity(delta.exchange, delta.symbol)
        if self._last_update_id is None:
            self._pending_deltas.append(delta)
            return None

        if delta.final_update_id <= self._last_update_id:
            return self.current_snapshot()

        self._apply_contiguous_delta(delta)
        self._assert_not_crossed()
        return self.current_snapshot()

    def _apply_contiguous_delta(self, delta: OrderBookDelta) -> None:
        assert self._last_update_id is not None
        expected_next = self._last_update_id + 1
        contiguous_by_pu = (
            delta.prev_final_update_id is not None and delta.prev_final_update_id == self._last_update_id
        )
        contiguous_by_range = delta.first_update_id <= expected_next <= delta.final_update_id

        if not (contiguous_by_pu or contiguous_by_range):
            if self._rest_snapshot_fetcher is not None:
                self._resync_count += 1
                self._last_update_id = None
                self._pending_deltas = [delta]
                fresh = self._rest_snapshot_fetcher(self.exchange, self.symbol)
                self.apply_snapshot(fresh)
                return
            raise DataIntegrityError(
                f"OrderBook delta sequence gap on {self.exchange}:{self.symbol}: "
                f"last_update_id={self._last_update_id}, got [{delta.first_update_id}, {delta.final_update_id}] "
                f"(pu={delta.prev_final_update_id})",
                details={
                    "last_update_id": self._last_update_id,
                    "first_update_id": delta.first_update_id,
                    "final_update_id": delta.final_update_id,
                    "prev_final_update_id": delta.prev_final_update_id,
                },
            )
        self._apply_levels(delta)

    def _apply_levels(self, delta: OrderBookDelta) -> None:
        for lvl in delta.bids:
            if lvl.quantity <= ZERO:
                self._bids.pop(lvl.price, None)
            else:
                self._bids[lvl.price] = lvl.quantity
        for lvl in delta.asks:
            if lvl.quantity <= ZERO:
                self._asks.pop(lvl.price, None)
            else:
                self._asks[lvl.price] = lvl.quantity
        self._last_update_id = delta.final_update_id
        self._exchange_ts_ms = delta.exchange_ts_ms
        self._local_recv_ts_ms = delta.local_recv_ts_ms

    def _assert_not_crossed(self) -> None:
        if self._bids and self._asks:
            best_bid = max(self._bids.keys())
            best_ask = min(self._asks.keys())
            if best_bid >= best_ask:
                raise DataIntegrityError(
                    f"Crossed orderbook detected on {self.exchange}:{self.symbol}: best_bid={best_bid} >= best_ask={best_ask}"
                )

    def current_snapshot(self, depth: int | None = None) -> OrderBookSnapshot:
        if self._last_update_id is None:
            raise DataIntegrityError(f"OrderBook not initialized with snapshot for {self.exchange}:{self.symbol}")
        sorted_bids = [
            OrderBookLevel(price=p, quantity=self._bids[p])
            for p in sorted(self._bids.keys(), reverse=True)
        ]
        sorted_asks = [
            OrderBookLevel(price=p, quantity=self._asks[p])
            for p in sorted(self._asks.keys())
        ]
        if depth is not None:
            sorted_bids = sorted_bids[:depth]
            sorted_asks = sorted_asks[:depth]
        return OrderBookSnapshot(
            exchange=self.exchange,
            symbol=self.symbol,
            bids=sorted_bids,
            asks=sorted_asks,
            last_update_id=self._last_update_id,
            exchange_ts_ms=self._exchange_ts_ms,
            local_recv_ts_ms=self._local_recv_ts_ms,
        )
