"""Market data series integrity, gap detection, REST hole-filling, and deduplication (P1)."""

from __future__ import annotations

from typing import Callable

from quant_platform.core.models import Bar, Tick
from quant_platform.exceptions import CrossExchangeIsolationError, DataIntegrityError

INTERVAL_MS_MAP: dict[str, int] = {
    "1s": 1_000,
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

BarBackfiller = Callable[[str, str, str, int, int], list[Bar]]
TickBackfiller = Callable[[str, str, int, int], list[Tick]]


def interval_to_ms(interval: str) -> int:
    if interval not in INTERVAL_MS_MAP:
        raise ValueError(f"Unsupported bar interval '{interval}'")
    return INTERVAL_MS_MAP[interval]


def _deduplicate_bars(bars: list[Bar], exchange: str, symbol: str, interval: str) -> list[Bar]:
    by_open: dict[int, Bar] = {}
    for b in bars:
        if b.exchange != exchange:
            raise CrossExchangeIsolationError(exchange, b.exchange, symbol)
        if b.symbol != symbol or b.interval != interval:
            raise DataIntegrityError(
                f"Bar metadata mismatch: expected {exchange}:{symbol}:{interval}, got {b.exchange}:{b.symbol}:{b.interval}"
            )
        existing = by_open.get(b.open_time_ms)
        if existing is None or (b.is_closed and not existing.is_closed):
            by_open[b.open_time_ms] = b
    return [by_open[k] for k in sorted(by_open.keys())]


def reconcile_bar_series(
    *,
    exchange: str,
    symbol: str,
    interval: str,
    bars: list[Bar],
    rest_backfiller: BarBackfiller | None = None,
) -> list[Bar]:
    """
    Validate bar continuity, optionally backfill missing intervals via REST, and deduplicate.
    Never silently skips a missing interval: raises DataIntegrityError if gaps remain.
    """
    if not bars:
        return []
    step_ms = interval_to_ms(interval)
    merged = _deduplicate_bars(bars, exchange, symbol, interval)

    missing_ranges: list[tuple[int, int]] = []
    for idx in range(1, len(merged)):
        prev_open = merged[idx - 1].open_time_ms
        curr_open = merged[idx].open_time_ms
        if curr_open - prev_open > step_ms:
            missing_ranges.append((prev_open + step_ms, curr_open - step_ms))

    if missing_ranges:
        if rest_backfiller is None:
            raise DataIntegrityError(
                f"Detected {len(missing_ranges)} K-line gap(s) on {exchange}:{symbol}:{interval}",
                details={"missing_ranges": missing_ranges},
            )
        patched: list[Bar] = list(merged)
        for m_start, m_end in missing_ranges:
            patched.extend(rest_backfiller(exchange, symbol, interval, m_start, m_end))
        merged = _deduplicate_bars(patched, exchange, symbol, interval)

        remaining: list[tuple[int, int]] = []
        for idx in range(1, len(merged)):
            prev_open = merged[idx - 1].open_time_ms
            curr_open = merged[idx].open_time_ms
            if curr_open - prev_open > step_ms:
                remaining.append((prev_open + step_ms, curr_open - step_ms))
        if remaining:
            raise DataIntegrityError(
                f"Unresolved K-line gap(s) after REST backfill on {exchange}:{symbol}:{interval}",
                details={"remaining_gaps": remaining},
            )

    return merged


def reconcile_tick_stream(
    *,
    exchange: str,
    symbol: str,
    ticks: list[Tick],
    rest_backfiller: TickBackfiller | None = None,
    allow_reorder: bool = False,
) -> list[Tick]:
    """
    Validate tick ordering and WebSocket sequence continuity.
    - Cross-exchange ticks immediately raise CrossExchangeIsolationError.
    - Out-of-order timestamps/sequences raise DataIntegrityError when allow_reorder=False.
    - Sequence gaps (seq_id jumps) trigger rest_backfiller or raise DataIntegrityError.
    """
    if not ticks:
        return []

    for tk in ticks:
        if tk.exchange != exchange:
            raise CrossExchangeIsolationError(exchange, tk.exchange, symbol)
        if tk.symbol != symbol:
            raise DataIntegrityError(f"Tick symbol mismatch: expected {symbol}, got {tk.symbol}")

    if not allow_reorder:
        for idx in range(1, len(ticks)):
            prev_tk, curr_tk = ticks[idx - 1], ticks[idx]
            if curr_tk.exchange_ts_ms < prev_tk.exchange_ts_ms:
                raise DataIntegrityError(
                    f"Out-of-order tick timestamp on {exchange}:{symbol}: "
                    f"{curr_tk.exchange_ts_ms} < {prev_tk.exchange_ts_ms}",
                    details={"prev_trade_id": prev_tk.trade_id, "curr_trade_id": curr_tk.trade_id},
                )
            if prev_tk.seq_id is not None and curr_tk.seq_id is not None and curr_tk.seq_id < prev_tk.seq_id:
                raise DataIntegrityError(
                    f"Out-of-order tick seq_id on {exchange}:{symbol}: {curr_tk.seq_id} < {prev_tk.seq_id}"
                )

    by_trade_id: dict[str, Tick] = {}
    for tk in ticks:
        by_trade_id[tk.trade_id] = tk

    ordered = sorted(
        by_trade_id.values(),
        key=lambda t: (t.seq_id if t.seq_id is not None else t.exchange_ts_ms, t.exchange_ts_ms, t.trade_id),
    )

    seq_gaps: list[tuple[int, int]] = []
    for idx in range(1, len(ordered)):
        p_seq, c_seq = ordered[idx - 1].seq_id, ordered[idx].seq_id
        if p_seq is not None and c_seq is not None and c_seq - p_seq > 1:
            seq_gaps.append((p_seq + 1, c_seq - 1))

    if seq_gaps:
        if rest_backfiller is None:
            raise DataIntegrityError(
                f"Detected {len(seq_gaps)} tick sequence gap(s) on {exchange}:{symbol}",
                details={"seq_gaps": seq_gaps},
            )
        for g_from, g_to in seq_gaps:
            for recovered in rest_backfiller(exchange, symbol, g_from, g_to):
                if recovered.exchange != exchange:
                    raise CrossExchangeIsolationError(exchange, recovered.exchange, symbol)
                by_trade_id[recovered.trade_id] = recovered

        ordered = sorted(
            by_trade_id.values(),
            key=lambda t: (t.seq_id if t.seq_id is not None else t.exchange_ts_ms, t.exchange_ts_ms, t.trade_id),
        )
        unresolved: list[tuple[int, int]] = []
        for idx in range(1, len(ordered)):
            p_seq, c_seq = ordered[idx - 1].seq_id, ordered[idx].seq_id
            if p_seq is not None and c_seq is not None and c_seq - p_seq > 1:
                unresolved.append((p_seq + 1, c_seq - 1))
        if unresolved:
            raise DataIntegrityError(
                f"Unresolved tick sequence gap(s) after REST backfill on {exchange}:{symbol}",
                details={"unresolved_gaps": unresolved},
            )

    return ordered
