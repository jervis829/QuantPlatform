"""Scheduled & manual trigger coordinator for ThreeTierReconciler (P9)."""

from __future__ import annotations

from typing import Any

from quant_platform.core.interfaces import BrokerAdapter, Clock
from quant_platform.reconciliation.engine import ThreeTierReconciler


class ReconciliationScheduler:
    """Drives periodic (interval-gated) and manual on-demand runs across all 3 reconciliation tiers."""

    def __init__(
        self,
        *,
        reconciler: ThreeTierReconciler,
        interval_ms: int = 300_000,
        clock: Clock,
    ) -> None:
        self.reconciler = reconciler
        self.interval_ms = interval_ms
        self.clock = clock
        self._last_run_ts_ms: int | None = None

        self._live_jobs: list[tuple[str, BrokerAdapter]] = []
        self._shadow_live_pairs: list[tuple[str, str]] = []
        self._backtest_shadow_pairs: list[tuple[str, str]] = []

    def register_live_broker(self, run_id: str, broker: BrokerAdapter) -> None:
        self._live_jobs.append((run_id, broker))

    def register_shadow_live_pair(self, shadow_run_id: str, live_run_id: str) -> None:
        self._shadow_live_pairs.append((shadow_run_id, live_run_id))

    def register_backtest_shadow_pair(self, backtest_run_id: str, shadow_run_id: str) -> None:
        self._backtest_shadow_pairs.append((backtest_run_id, shadow_run_id))

    def run_all_now(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for run_id, broker in self._live_jobs:
            results.append(self.reconciler.reconcile_exchange_vs_local(run_id, broker))
        for sh_id, lv_id in self._shadow_live_pairs:
            results.append(self.reconciler.reconcile_shadow_vs_live(sh_id, lv_id))
        for bt_id, sh_id in self._backtest_shadow_pairs:
            results.append(self.reconciler.reconcile_backtest_vs_shadow(bt_id, sh_id))
        self._last_run_ts_ms = self.clock.now_ms()
        return results

    def tick(self) -> list[dict[str, Any]]:
        now = self.clock.now_ms()
        if self._last_run_ts_ms is not None and (now - self._last_run_ts_ms) < self.interval_ms:
            return []
        return self.run_all_now()
