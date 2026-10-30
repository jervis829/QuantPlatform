"""Data layer: anti-lookahead DataFeeds, OrderBook delta rebuilder, gap checker, and clock sync (P1)."""

from quant_platform.data.clock import (
    ExchangeTimeSynchronizer,
    SimulatedClock,
    SyncedLiveClock,
    TimeSyncGuardedBroker,
)
from quant_platform.data.feed import CursorGuardedDataFeed, HistoricalDataFeed, StreamingDataFeed
from quant_platform.data.integrity import (
    INTERVAL_MS_MAP,
    interval_to_ms,
    reconcile_bar_series,
    reconcile_tick_stream,
)
from quant_platform.data.orderbook import OrderBookMaintainer
from quant_platform.data.versioning import compute_dataset_version

__all__ = [
    "CursorGuardedDataFeed",
    "ExchangeTimeSynchronizer",
    "HistoricalDataFeed",
    "INTERVAL_MS_MAP",
    "OrderBookMaintainer",
    "SimulatedClock",
    "StreamingDataFeed",
    "SyncedLiveClock",
    "TimeSyncGuardedBroker",
    "compute_dataset_version",
    "interval_to_ms",
    "reconcile_bar_series",
    "reconcile_tick_stream",
]
