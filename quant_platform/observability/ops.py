"""WebSocket Watchdog, Startup Mandatory Reconciliation Supervisor & DB Fault Retry (P12)."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any, TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from quant_platform.core.interfaces import Clock
from quant_platform.live.broker import ExchangeBroker
from quant_platform.observability.metrics import PlatformMetrics
from quant_platform.reconciliation.engine import ThreeTierReconciler

T = TypeVar("T")


class WebSocketHealthMonitor:
    """
    Monitors per-(exchange, channel) WebSocket heartbeats:
    - Updates `quant_ws_connection_healthy` gauge (1 = healthy, 0 = stale);
    - On stale detection (`now_ms - last_heartbeat_ms > stale_timeout_ms`), sets gauge to `0`
      and triggers `on_reconnect(exchange, channel)` callback so missed fills/positions reconcile cleanly.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        metrics: PlatformMetrics,
        stale_timeout_ms: int = 30_000,
    ) -> None:
        self._clock = clock
        self._metrics = metrics
        self._stale_timeout_ms = stale_timeout_ms
        self._last_heartbeat_ms: dict[tuple[str, str], int] = {}

    def record_heartbeat(self, exchange: str, channel: str) -> None:
        key = (exchange, channel)
        self._last_heartbeat_ms[key] = self._clock.now_ms()
        self._metrics.ws_connection_healthy.labels(exchange=exchange, channel=channel).set(1)

    def check_and_heal(
        self,
        *,
        on_reconnect: Callable[[str, str], None] | None = None,
    ) -> list[tuple[str, str]]:
        now_ms = self._clock.now_ms()
        stale_streams: list[tuple[str, str]] = []
        for (ex, ch), last_ts in list(self._last_heartbeat_ms.items()):
            if now_ms - last_ts > self._stale_timeout_ms:
                self._metrics.ws_connection_healthy.labels(exchange=ex, channel=ch).set(0)
                stale_streams.append((ex, ch))
                if on_reconnect is not None:
                    on_reconnect(ex, ch)
        return stale_streams


class PlatformSupervisor:
    """
    Production process supervisor responsibilities:
    1. `startup_reconcile(...)`: enforces Red Line 10 on process boot — runs `broker.startup_sync()`
       followed by Tier-1 `reconcile_exchange_vs_local(..., auto_heal=True)` before enabling strategy ticks;
    2. `execute_with_db_retry(...)`: wraps transient database `OperationalError` blips with bounded exponential backoff.
    """

    def __init__(self, *, clock: Clock, metrics: PlatformMetrics) -> None:
        self._clock = clock
        self._metrics = metrics

    def startup_reconcile(
        self,
        *,
        session: Session,
        run_id: str,
        broker: ExchangeBroker,
    ) -> dict[str, Any]:
        sync_summary = broker.startup_sync()
        reconciler = ThreeTierReconciler(session=session, clock=self._clock)
        tier1 = reconciler.reconcile_exchange_vs_local(
            run_id=run_id,
            broker=broker,
            auto_heal=True,
        )
        for item in tier1.get("items", []):
            self._metrics.reconciliation_discrepancies_total.labels(
                tier="exchange_vs_local",
                severity=str(item.get("severity", "LOW")),
            ).inc()
        return {
            "broker_sync": sync_summary,
            "tier1_reconciliation_id": tier1["report_id"],
            "tier1_passed": tier1["passed"],
            "discrepancies_healed": int(tier1["discrepancy_count"]),
        }

    @staticmethod
    def execute_with_db_retry(
        fn: Callable[[], T],
        *,
        max_attempts: int = 3,
        base_delay_sec: float = 0.05,
    ) -> T:
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except OperationalError as exc:
                last_err = exc
                if attempt == max_attempts:
                    raise
                time.sleep(base_delay_sec * (2 ** (attempt - 1)))
        assert last_err is not None
        raise last_err
