"""Clock implementations and exchange server time synchronization (P1)."""

from __future__ import annotations

import time
from typing import Any, Callable

from quant_platform.core.interfaces import BrokerAdapter, Clock, FillCallback, OrderUpdateCallback
from quant_platform.core.models import Balance, FundingPayment, Order, OrderRequest, Position
from quant_platform.exceptions import ClockSkewError


class SimulatedClock(Clock):
    """Monotonic controllable UTC millisecond clock for deterministic replay and backtest."""

    def __init__(self, initial_ts_ms: int = 0) -> None:
        self._now_ms = initial_ts_ms
        self._offsets: dict[str, int] = {}

    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, ts_ms: int) -> None:
        if ts_ms < self._now_ms:
            raise ValueError(f"SimulatedClock cannot move backwards: {ts_ms} < {self._now_ms}")
        self._now_ms = ts_ms

    def advance_by(self, delta_ms: int) -> None:
        if delta_ms < 0:
            raise ValueError(f"delta_ms must be >= 0, got {delta_ms}")
        self._now_ms += delta_ms

    def set_exchange_offset(self, exchange: str, offset_ms: int) -> None:
        self._offsets[exchange] = offset_ms

    def exchange_offset_ms(self, exchange: str) -> int:
        return self._offsets.get(exchange, 0)


class ExchangeTimeSynchronizer:
    """
    Periodically measures exchange server time offset (server_ts_ms - local_midpoint_ms),
    emits alerts when skew exceeds threshold, and enforces pre-order clock health.
    """

    def __init__(
        self,
        *,
        max_skew_ms: int = 1000,
        local_time_fn: Callable[[], int] | None = None,
        server_time_fetchers: dict[str, Callable[[], int]] | None = None,
        alert_callbacks: list[Callable[[dict[str, Any]], None]] | None = None,
    ) -> None:
        self.max_skew_ms = max_skew_ms
        self._local_time_fn = local_time_fn or (lambda: int(time.time() * 1000))
        self._fetchers: dict[str, Callable[[], int]] = dict(server_time_fetchers or {})
        self._alerts: list[Callable[[dict[str, Any]], None]] = list(alert_callbacks or [])
        self._offsets_ms: dict[str, int] = {}
        self._last_sync_ms: dict[str, int] = {}

    def register_fetcher(self, exchange: str, fetcher: Callable[[], int]) -> None:
        self._fetchers[exchange] = fetcher

    def register_alert_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._alerts.append(callback)

    def sync(self, exchange: str) -> int:
        if exchange not in self._fetchers:
            self._offsets_ms.setdefault(exchange, 0)
            self._last_sync_ms[exchange] = self._local_time_fn()
            return self._offsets_ms[exchange]

        t0 = self._local_time_fn()
        server_ts = self._fetchers[exchange]()
        t1 = self._local_time_fn()
        midpoint = (t0 + t1) // 2
        offset_ms = server_ts - midpoint
        self._offsets_ms[exchange] = offset_ms
        self._last_sync_ms[exchange] = t1

        if abs(offset_ms) > self.max_skew_ms:
            payload: dict[str, Any] = {
                "code": "CLOCK_SKEW_ALERT",
                "exchange": exchange,
                "offset_ms": offset_ms,
                "max_skew_ms": self.max_skew_ms,
                "local_ts_ms": midpoint,
                "server_ts_ms": server_ts,
            }
            for cb in self._alerts:
                cb(payload)
        return offset_ms

    def get_offset_ms(self, exchange: str) -> int:
        return self._offsets_ms.get(exchange, 0)

    def assert_healthy_for_order(self, exchange: str, auto_sync: bool = True) -> int:
        if exchange not in self._offsets_ms and auto_sync and exchange in self._fetchers:
            self.sync(exchange)
        offset_ms = self._offsets_ms.get(exchange, 0)
        if abs(offset_ms) > self.max_skew_ms:
            raise ClockSkewError(exchange=exchange, offset_ms=offset_ms, threshold_ms=self.max_skew_ms)
        return offset_ms


class SyncedLiveClock(Clock):
    """Real-time UTC millisecond clock paired with ExchangeTimeSynchronizer."""

    def __init__(
        self,
        synchronizer: ExchangeTimeSynchronizer | None = None,
        local_time_fn: Callable[[], int] | None = None,
    ) -> None:
        self._synchronizer = synchronizer or ExchangeTimeSynchronizer()
        self._local_time_fn = local_time_fn or (lambda: int(time.time() * 1000))

    @property
    def synchronizer(self) -> ExchangeTimeSynchronizer:
        return self._synchronizer

    def now_ms(self) -> int:
        return self._local_time_fn()

    def exchange_offset_ms(self, exchange: str) -> int:
        return self._synchronizer.get_offset_ms(exchange)


class TimeSyncGuardedBroker(BrokerAdapter):
    """Broker decorator verifying exchange clock offset before submitting any order."""

    def __init__(self, inner: BrokerAdapter, synchronizer: ExchangeTimeSynchronizer) -> None:
        self._inner = inner
        self._synchronizer = synchronizer

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    def startup_sync(self) -> dict[str, Any]:
        return self._inner.startup_sync()

    def submit_order(self, request: OrderRequest) -> Order:
        offset_ms = self._synchronizer.assert_healthy_for_order(request.exchange)
        if request.created_ts_ms > 0:
            request = request.model_copy(update={"created_ts_ms": request.created_ts_ms + offset_ms})
        return self._inner.submit_order(request)

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        return self._inner.cancel_order(client_order_id, exchange, symbol)

    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        return self._inner.query_order(client_order_id, exchange, symbol)

    def query_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[Order]:
        return self._inner.query_open_orders(exchange, symbol)

    def query_positions(self, exchange: str | None = None, symbol: str | None = None) -> list[Position]:
        return self._inner.query_positions(exchange, symbol)

    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        return self._inner.query_balance(exchange)

    def query_funding_payments(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        since_ms: int | None = None,
    ) -> list[FundingPayment]:
        return self._inner.query_funding_payments(exchange, symbol, since_ms)

    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        self._inner.register_order_callback(callback)

    def register_fill_callback(self, callback: FillCallback) -> None:
        self._inner.register_fill_callback(callback)
