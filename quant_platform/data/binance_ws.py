"""
Binance public WebSocket kline stream — real-time market data source.

Connects to Binance spot/perp combined kline stream and yields CLOSED Bar
objects (is_closed=True) so strategies never see forming-candle lookahead.
Auto-reconnects with exponential backoff on disconnects.

Symbol mapping: "ETH/USDT:USDT" -> "ethusdt" (Binance stream symbol).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal

from quant_platform.core.models import Bar

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


def to_binance_stream_symbol(symbol: str) -> str:
    """Map platform symbol (ETH/USDT:USDT) to Binance stream symbol (ethusdt)."""
    base = symbol.split("/")[0].strip().lower()
    return f"{base}usdt"


def parse_kline_message(raw: str, *, exchange: str, symbol: str, interval: str) -> Bar | None:
    """Parse a Binance kline WebSocket message into a closed Bar (None if forming)."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if msg.get("e") != "kline":
        return None
    k = msg.get("k") or {}
    if not k.get("x"):
        return None  # forming candle — skip to avoid lookahead
    try:
        open_px = Decimal(str(k["o"]))
        high_px = Decimal(str(k["h"]))
        low_px = Decimal(str(k["l"]))
        close_px = Decimal(str(k["c"]))
        volume = Decimal(str(k["v"]))
        quote_volume = Decimal(str(k["q"]))
        open_ts = int(k["t"])
        close_ts = int(k["T"])
    except (KeyError, ValueError, ArithmeticError):
        return None
    return Bar(
        exchange=exchange,
        symbol=symbol,
        interval=interval,
        open_time_ms=open_ts,
        close_time_ms=close_ts,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        quote_volume=quote_volume,
        trade_count=int(k.get("n", 0) or 0),
        is_closed=True,
        exchange_ts_ms=int(msg.get("E", close_ts)),
        local_recv_ts_ms=close_ts,
    )


class BinanceKlineStream:
    """Async iterator of closed Binance klines with auto-reconnect."""

    def __init__(
        self,
        *,
        exchange: str = "binance",
        symbol: str = "ETH/USDT:USDT",
        interval: str = "1h",
        ws_base: str = BINANCE_WS_BASE,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self.ws_base = ws_base
        self.stream_symbol = to_binance_stream_symbol(symbol)
        self._stop = asyncio.Event()

    @property
    def url(self) -> str:
        return f"{self.ws_base}/{self.stream_symbol}@kline_{self.interval}"

    def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[Bar]:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("Binance WS connected: %s", self.url)
                    backoff = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            continue
                        bar = parse_kline_message(
                            raw, exchange=self.exchange, symbol=self.symbol, interval=self.interval
                        )
                        if bar is not None:
                            yield bar
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect loop must survive
                logger.warning("Binance WS disconnect (%s), reconnect in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
