"""Real-time WebSocket pub/sub hub pushing run, order/fill, log, and risk events to UI clients (P11)."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from fastapi import WebSocket


class WebSocketEventHub:
    """Manages active WebSocket subscribers and recent event ring-buffer."""

    def __init__(self, max_history: int = 200) -> None:
        self._clients: set[WebSocket] = set()
        self.history: deque[dict[str, Any]] = deque(maxlen=max_history)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        await ws.send_json({"type": "connected", "recent_count": len(self.history)})

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def publish(self, event: dict[str, Any]) -> None:
        self.history.append(event)
        stale: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)

    def publish_sync(self, event: dict[str, Any]) -> None:
        self.history.append(event)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._fanout_only(event))
        except RuntimeError:
            pass

    async def _fanout_only(self, event: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)
