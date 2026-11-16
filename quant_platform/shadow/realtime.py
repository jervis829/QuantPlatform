"""
Real-time Shadow Trading Sessions — 实时模拟盘（类实盘行情、纸面成交）。

Unlike replay shadow runs (which iterate historical bars and finish in seconds),
real-time shadow sessions consume LIVE Binance WebSocket klines as they close,
exactly like a live run — except fills are simulated by ShadowBroker (paper only,
no real orders). Sessions run continuously until explicitly stopped.

Architecture:
    Binance WS (asyncio thread) -> thread-safe queue -> blocking event source
    -> UnifiedExecutionPipeline (sync thread) -> ShadowBroker (paper fills)
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import uuid
from decimal import Decimal
from typing import Any

from quant_platform.core.enums import ApprovalStatus, MarginMode, RunStatus
from quant_platform.core.models import Bar, StrategyConfig
from quant_platform.data.binance_ws import BinanceKlineStream
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.execution.runners import ShadowRunner
from quant_platform.shadow.broker import ShadowBroker, ShadowSimulationParams
from quant_platform.strategy.registry import compile_strategy_class
from quant_platform.strategy.version_store import StrategyVersionStore

logger = logging.getLogger(__name__)


class RealtimeShadowSession:
    """One long-running real-time shadow session."""

    def __init__(
        self,
        *,
        session_factory: Any,
        sandbox: Any,
        strategy_id: str,
        version_id: str,
        exchange: str,
        symbol: str,
        interval: str,
        warmup_bars: list[Bar],
        initial_usdt: Decimal,
        leverage: Decimal = Decimal("2"),
    ) -> None:
        self.session_factory = session_factory
        self.sandbox = sandbox
        self.strategy_id = strategy_id
        self.version_id = version_id
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self.warmup_bars = warmup_bars
        self.initial_usdt = initial_usdt
        self.leverage = leverage

        self.run_id = f"run_shrt_{uuid.uuid4().hex[:14]}"
        self.status = "STARTING"
        self.error_message: str | None = None
        self.started_at_ms: int | None = None
        self.bars_processed = 0
        self.live_bars_received = 0

        self._stop_event = threading.Event()
        self._bar_queue: queue.Queue[Bar | None] = queue.Queue(maxsize=4096)
        self._pipeline_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_stream: BinanceKlineStream | None = None

    # ------------------------------------------------------------------
    def start(self) -> str:
        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline, name=f"shadow-rt-{self.run_id}", daemon=True
        )
        self._pipeline_thread.start()
        return self.run_id

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_stream is not None:
            self._ws_stream.stop()
        # Sentinel unblocks the event source
        try:
            self._bar_queue.put_nowait(None)
        except queue.Full:
            pass

    def is_alive(self) -> bool:
        return self._pipeline_thread is not None and self._pipeline_thread.is_alive()

    # ------------------------------------------------------------------
    def _run_pipeline(self) -> None:
        """Sync pipeline thread: warmup bars then live bars from the queue."""
        try:
            with self.session_factory() as session:
                store = StrategyVersionStore(session=session, sandbox=self.sandbox)
                ver = store.get_version(self.version_id)
                meta = store.get_strategy_meta(self.strategy_id)
                if ver.approval_status != ApprovalStatus.APPROVED.value:
                    self.status = "ERROR"
                    self.error_message = f"APPROVAL_REQUIRED: {ver.approval_status}"
                    return

                cls = compile_strategy_class(ver.code)
                from quant_platform.api.app import _default_perp_spec

                spec = _default_perp_spec(self.symbol, self.exchange)
                cfg = StrategyConfig(
                    strategy_id=self.strategy_id,
                    strategy_version_id=ver.version_id,
                    name=meta.name,
                    exchanges=[self.exchange],
                    symbols=[self.symbol],
                    bar_interval=self.interval,
                    params=ver.params_json,
                    margin_mode=MarginMode.CROSS,
                    default_leverage=self.leverage,
                    initial_balances={"USDT": self.initial_usdt},
                )
                ledger = UnifiedLedgerRepository(session)
                first_ts = self.warmup_bars[0].open_time_ms if self.warmup_bars else 0
                clock = SimulatedClock(first_ts)
                feed = StreamingDataFeed(clock=clock)
                feed.register_symbol_spec(spec)
                broker = ShadowBroker(
                    run_id=self.run_id,
                    strategy_id=self.strategy_id,
                    clock=clock,
                    data_feed=feed,
                    ledger=ledger,
                    sim_params=ShadowSimulationParams(latency_ms=10),
                    initial_balances={"USDT": self.initial_usdt},
                )

                warmup = list(self.warmup_bars)
                bar_queue = self._bar_queue
                stop_event = self._stop_event
                session_ref = self

                def _rt_events():
                    # Warmup: historical bars build indicator state.
                    # Commit after each bar so the long-lived session never
                    # holds a write lock that blocks API threads (SQLite).
                    for b in warmup:
                        if stop_event.is_set():
                            return
                        clock.advance_to(b.close_time_ms)
                        feed.on_bar(b)
                        broker.on_bar(b)
                        session_ref.bars_processed += 1
                        yield ("bar", b)
                        session.commit()
                    # Live: block on queue for real-time closed bars
                    while not stop_event.is_set():
                        try:
                            b = bar_queue.get(timeout=1.0)
                        except queue.Empty:
                            continue
                        if b is None:
                            break
                        clock.advance_to(b.close_time_ms)
                        feed.on_bar(b)
                        broker.on_bar(b)
                        session_ref.bars_processed += 1
                        session_ref.live_bars_received += 1
                        yield ("bar", b)
                        session.commit()

                runner = ShadowRunner(
                    run_id=self.run_id,
                    strategy_cls=cls,
                    strategy_code=ver.code,
                    config=cfg,
                    clock=clock,
                    data_feed=feed,
                    broker=broker,
                    ledger=ledger,
                    event_source=_rt_events,
                )
                self.status = "RUNNING"
                self.started_at_ms = clock.now_ms()
                run_rec = runner.run()
                session.commit()
                self.status = (
                    "COMPLETED" if run_rec.status == RunStatus.COMPLETED else run_rec.status.value
                )
        except Exception as exc:  # noqa: BLE001 - session must report, not crash
            logger.exception("Realtime shadow session %s failed", self.run_id)
            self.status = "ERROR"
            self.error_message = str(exc)

    # ------------------------------------------------------------------
    def _run_ws_loop(self) -> None:
        """Asyncio thread: Binance WS -> queue."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _pump() -> None:
            stream = BinanceKlineStream(
                exchange=self.exchange, symbol=self.symbol, interval=self.interval
            )
            self._ws_stream = stream
            async for bar in stream.stream():
                if self._stop_event.is_set():
                    break
                try:
                    self._bar_queue.put(bar, timeout=5.0)
                except queue.Full:
                    logger.warning("Realtime shadow bar queue full, dropping bar %s", bar.open_time_ms)

        try:
            loop.run_until_complete(_pump())
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    def start_ws(self) -> None:
        self._ws_thread = threading.Thread(
            target=self._run_ws_loop, name=f"shadow-ws-{self.run_id}", daemon=True
        )
        self._ws_thread.start()


class RealtimeShadowManager:
    """Registry of active real-time shadow sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, RealtimeShadowSession] = {}
        self._lock = threading.Lock()

    def start_session(self, session: RealtimeShadowSession) -> str:
        with self._lock:
            self._sessions[session.run_id] = session
        session.start()
        session.start_ws()
        return session.run_id

    def stop_session(self, run_id: str) -> bool:
        with self._lock:
            sess = self._sessions.get(run_id)
        if sess is None:
            return False
        sess.stop()
        return True

    def get_session(self, run_id: str) -> RealtimeShadowSession | None:
        with self._lock:
            return self._sessions.get(run_id)

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        out = []
        for s in sessions:
            if s.is_alive() or s.status in {"RUNNING", "STARTING"}:
                out.append(
                    {
                        "run_id": s.run_id,
                        "strategy_id": s.strategy_id,
                        "version_id": s.version_id,
                        "exchange": s.exchange,
                        "symbol": s.symbol,
                        "interval": s.interval,
                        "status": s.status,
                        "bars_processed": s.bars_processed,
                        "live_bars_received": s.live_bars_received,
                        "started_at_ms": s.started_at_ms,
                        "error_message": s.error_message,
                    }
                )
        return out

    def prune_finished(self) -> None:
        with self._lock:
            finished = [
                rid for rid, s in self._sessions.items()
                if not s.is_alive() and s.status not in {"RUNNING", "STARTING"}
            ]
            for rid in finished:
                del self._sessions[rid]
