"""Authentic historical Binance USDT-M Perpetual 1H OHLCV + 8H funding datasets (2023-10-15 .. 2026-07-01 UTC)."""

from __future__ import annotations

import csv
import gzip
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from quant_platform.core.models import Bar, FundingRatePoint

_DATA_DIR = Path(__file__).resolve().parent

_BASE_FILE_MAP: dict[str, str] = {
    "BTC": "BTCUSDT_1h.csv.gz",
    "ETH": "ETHUSDT_1h.csv.gz",
    "SOL": "SOLUSDT_1h.csv.gz",
    "BNB": "BNBUSDT_1h.csv.gz",
}


def _resolve_gz_path(symbol: str) -> Path:
    base = (symbol.split("/")[0] if "/" in symbol else symbol.replace("USDT", "")).upper()
    fname = _BASE_FILE_MAP.get(base, "BTCUSDT_1h.csv.gz")
    return _DATA_DIR / fname


@lru_cache(maxsize=16)
def _load_cached_dataset(
    exchange: str,
    symbol: str,
    start_ts_ms: int | None,
    end_ts_ms: int | None,
) -> tuple[tuple[Bar, ...], tuple[FundingRatePoint, ...]]:
    gz_path = _resolve_gz_path(symbol)
    is_perp = ":" in symbol
    bars: list[Bar] = []
    fundings: list[FundingRatePoint] = []

    with gzip.open(gz_path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            o_ts = int(row["open_time_ms"])
            if start_ts_ms is not None and o_ts < start_ts_ms:
                continue
            if end_ts_ms is not None and o_ts >= end_ts_ms:
                continue
            c_ts = o_ts + 3_599_999
            close_px = Decimal(row["close"])
            bars.append(
                Bar(
                    exchange=exchange,
                    symbol=symbol,
                    interval="1h",
                    open_time_ms=o_ts,
                    close_time_ms=c_ts,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=close_px,
                    volume=Decimal(row["volume"]),
                    quote_volume=Decimal(row["quote_volume"]),
                    trade_count=int(row["trade_count"]),
                    is_closed=True,
                    exchange_ts_ms=c_ts,
                    local_recv_ts_ms=c_ts,
                )
            )
            fr_raw = row.get("funding_rate", "")
            if is_perp and fr_raw:
                fundings.append(
                    FundingRatePoint(
                        exchange=exchange,
                        symbol=symbol,
                        funding_rate=Decimal(fr_raw),
                        mark_price=close_px,
                        funding_ts_ms=c_ts,
                        local_recv_ts_ms=c_ts,
                    )
                )
    return tuple(bars), tuple(fundings)


def load_real_binance_1h_dataset(
    exchange: str = "binance",
    symbol: str = "BTC/USDT:USDT",
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
) -> tuple[list[Bar], list[FundingRatePoint]]:
    """Return authentic historical Binance 1H OHLCV bars and 8H funding rate settlements."""
    bars_t, fundings_t = _load_cached_dataset(exchange, symbol, start_ts_ms, end_ts_ms)
    return list(bars_t), list(fundings_t)


def load_real_binance_1h_bars(
    exchange: str = "binance",
    symbol: str = "BTC/USDT:USDT",
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
) -> list[Bar]:
    """Return authentic historical Binance 1H OHLCV bars."""
    bars_t, _ = _load_cached_dataset(exchange, symbol, start_ts_ms, end_ts_ms)
    return list(bars_t)
