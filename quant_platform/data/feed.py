"""DataFeed implementations with structural anti-lookahead guards and multi-exchange isolation (P1)."""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from quant_platform.core.hashing import canonical_json, sha256_hex
from quant_platform.core.interfaces import Clock, DataFeed
from quant_platform.core.models import (
    Bar,
    FundingRatePoint,
    MarkIndexPrice,
    OrderBookDelta,
    OrderBookSnapshot,
    SymbolSpec,
    Tick,
)
from quant_platform.data.integrity import BarBackfiller, TickBackfiller, reconcile_bar_series, reconcile_tick_stream
from quant_platform.data.orderbook import OrderBookMaintainer
from quant_platform.data.versioning import compute_dataset_version
from quant_platform.exceptions import CrossExchangeIsolationError, DataIntegrityError, LookaheadError


class CursorGuardedDataFeed(DataFeed):
    """Shared anti-lookahead DataFeed base class (Red Line 3)."""

    def __init__(self, clock: Clock, source: str = "memory") -> None:
        self._clock = clock
        self._source = source
        self._bars: dict[tuple[str, str, str], list[Bar]] = {}
        self._ticks: dict[tuple[str, str], list[Tick]] = {}
        self._orderbooks: dict[tuple[str, str], list[OrderBookSnapshot]] = {}
        self._ob_ts_index: dict[tuple[str, str], list[int]] = {}
        self._mark_prices: dict[tuple[str, str], list[MarkIndexPrice]] = {}
        self._mark_ts_index: dict[tuple[str, str], list[int]] = {}
        self._funding_rates: dict[tuple[str, str], list[FundingRatePoint]] = {}
        self._symbol_specs: dict[tuple[str, str], list[SymbolSpec]] = {}

    def cursor_ts_ms(self) -> int:
        return self._clock.now_ms()

    def _guard_ts(self, ts_ms: int | None, resource: str) -> int:
        now = self.cursor_ts_ms()
        if ts_ms is None:
            return now
        if ts_ms > now:
            raise LookaheadError(requested_ts_ms=ts_ms, current_ts_ms=now, resource=resource)
        return ts_ms

    def register_symbol_spec(self, spec: SymbolSpec) -> None:
        key = (spec.exchange, spec.symbol)
        bucket = [s for s in self._symbol_specs.get(key, []) if s.version != spec.version]
        bucket.append(spec)
        bucket.sort(key=lambda s: (s.effective_from_ms, s.version))
        self._symbol_specs[key] = bucket

    def get_bars(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
        include_unclosed: bool = False,
    ) -> list[Bar]:
        now = self.cursor_ts_ms()
        if start_ts_ms is not None and start_ts_ms > now:
            raise LookaheadError(requested_ts_ms=start_ts_ms, current_ts_ms=now, resource=f"bars:{exchange}:{symbol}")
        upper = self._guard_ts(end_ts_ms, f"bars:{exchange}:{symbol}:{interval}")

        series = self._bars.get((exchange, symbol, interval), [])
        out: list[Bar] = []
        for b in series:
            if start_ts_ms is not None and b.open_time_ms < start_ts_ms:
                continue
            if b.open_time_ms > upper:
                break
            if b.close_time_ms <= upper and b.is_closed:
                out.append(b)
            elif include_unclosed and b.open_time_ms <= upper:
                if b.is_closed:
                    # Do not leak future high/low/close of a historical bar whose close_time_ms > upper!
                    continue
                out.append(b)
        if limit is not None and limit >= 0:
            out = out[-limit:] if limit > 0 else []
        return out

    def get_ticks(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
    ) -> list[Tick]:
        now = self.cursor_ts_ms()
        if start_ts_ms is not None and start_ts_ms > now:
            raise LookaheadError(requested_ts_ms=start_ts_ms, current_ts_ms=now, resource=f"ticks:{exchange}:{symbol}")
        upper = self._guard_ts(end_ts_ms, f"ticks:{exchange}:{symbol}")

        series = self._ticks.get((exchange, symbol), [])
        out = [
            tk
            for tk in series
            if tk.exchange_ts_ms <= upper and (start_ts_ms is None or tk.exchange_ts_ms >= start_ts_ms)
        ]
        if limit is not None and limit >= 0:
            out = out[-limit:] if limit > 0 else []
        return out

    def get_orderbook(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> OrderBookSnapshot | None:
        upper = self._guard_ts(at_ts_ms, f"orderbook:{exchange}:{symbol}")
        key = (exchange, symbol)
        ts_list = self._ob_ts_index.get(key)
        if not ts_list:
            return None
        pos = bisect_right(ts_list, upper) - 1
        if pos < 0:
            return None
        return self._orderbooks[key][pos]

    def get_mark_price(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> MarkIndexPrice | None:
        upper = self._guard_ts(at_ts_ms, f"mark_price:{exchange}:{symbol}")
        key = (exchange, symbol)
        ts_list = self._mark_ts_index.get(key)
        if not ts_list:
            return None
        pos = bisect_right(ts_list, upper) - 1
        if pos < 0:
            return None
        return self._mark_prices[key][pos]

    def get_funding_rates(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> list[FundingRatePoint]:
        now = self.cursor_ts_ms()
        if start_ts_ms is not None and start_ts_ms > now:
            raise LookaheadError(requested_ts_ms=start_ts_ms, current_ts_ms=now, resource=f"funding:{exchange}:{symbol}")
        upper = self._guard_ts(end_ts_ms, f"funding:{exchange}:{symbol}")
        series = self._funding_rates.get((exchange, symbol), [])
        return [
            fp
            for fp in series
            if fp.funding_ts_ms <= upper and (start_ts_ms is None or fp.funding_ts_ms >= start_ts_ms)
        ]

    def get_symbol_spec(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> SymbolSpec:
        target_ts = self._guard_ts(at_ts_ms, f"symbol_spec:{exchange}:{symbol}")
        specs = self._symbol_specs.get((exchange, symbol), [])
        if not specs:
            raise KeyError(f"No SymbolSpec registered for {exchange}:{symbol}")
        matched: SymbolSpec | None = None
        for spec in specs:
            if spec.effective_from_ms <= target_ts and (
                spec.effective_to_ms is None or target_ts < spec.effective_to_ms
            ):
                matched = spec
        if matched is None:
            # Fallback to earliest if effective_from_ms == 0, else raise
            raise KeyError(f"No active SymbolSpec for {exchange}:{symbol} at ts={target_ts}")
        return matched

    def dataset_version(self) -> str:
        slices: list[dict[str, Any]] = []
        for (ex, sym, interval), bars in self._bars.items():
            if not bars:
                continue
            digest = sha256_hex(
                canonical_json(
                    [
                        (b.open_time_ms, b.close_time_ms, b.open, b.high, b.low, b.close, b.volume, b.is_closed)
                        for b in bars
                    ]
                )
            )
            slices.append(
                {
                    "exchange": ex,
                    "symbol": sym,
                    "kind": "bars",
                    "interval": interval,
                    "start_ts_ms": bars[0].open_time_ms,
                    "end_ts_ms": bars[-1].close_time_ms,
                    "count": len(bars),
                    "digest": digest,
                }
            )
        for (ex, sym), ticks in self._ticks.items():
            if not ticks:
                continue
            digest = sha256_hex(canonical_json([(t.trade_id, t.exchange_ts_ms, t.price, t.quantity) for t in ticks]))
            slices.append(
                {
                    "exchange": ex,
                    "symbol": sym,
                    "kind": "ticks",
                    "interval": "",
                    "start_ts_ms": ticks[0].exchange_ts_ms,
                    "end_ts_ms": ticks[-1].exchange_ts_ms,
                    "count": len(ticks),
                    "digest": digest,
                }
            )
        for (ex, sym), obs in self._orderbooks.items():
            if not obs:
                continue
            digest = sha256_hex(canonical_json([(o.last_update_id, o.exchange_ts_ms) for o in obs]))
            slices.append(
                {
                    "exchange": ex,
                    "symbol": sym,
                    "kind": "orderbooks",
                    "interval": "",
                    "start_ts_ms": obs[0].exchange_ts_ms,
                    "end_ts_ms": obs[-1].exchange_ts_ms,
                    "count": len(obs),
                    "digest": digest,
                }
            )
        for (ex, sym), mps in self._mark_prices.items():
            if not mps:
                continue
            digest = sha256_hex(canonical_json([(m.exchange_ts_ms, m.mark_price, m.index_price) for m in mps]))
            slices.append(
                {
                    "exchange": ex,
                    "symbol": sym,
                    "kind": "mark_prices",
                    "interval": "",
                    "start_ts_ms": mps[0].exchange_ts_ms,
                    "end_ts_ms": mps[-1].exchange_ts_ms,
                    "count": len(mps),
                    "digest": digest,
                }
            )
        for (ex, sym), frs in self._funding_rates.items():
            if not frs:
                continue
            digest = sha256_hex(canonical_json([(f.funding_ts_ms, f.funding_rate, f.mark_price) for f in frs]))
            slices.append(
                {
                    "exchange": ex,
                    "symbol": sym,
                    "kind": "funding_rates",
                    "interval": "",
                    "start_ts_ms": frs[0].funding_ts_ms,
                    "end_ts_ms": frs[-1].funding_ts_ms,
                    "count": len(frs),
                    "digest": digest,
                }
            )
        return compute_dataset_version(source=self._source, slices=slices)


class HistoricalDataFeed(CursorGuardedDataFeed):
    """Deterministic historical replay DataFeed enforcing gapless series & cursor anti-lookahead."""

    def load_bars(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        bars: list[Bar],
        rest_backfiller: BarBackfiller | None = None,
    ) -> None:
        validated = reconcile_bar_series(
            exchange=exchange,
            symbol=symbol,
            interval=interval,
            bars=bars,
            rest_backfiller=rest_backfiller,
        )
        self._bars[(exchange, symbol, interval)] = validated

    def load_ticks(
        self,
        exchange: str,
        symbol: str,
        ticks: list[Tick],
        rest_backfiller: TickBackfiller | None = None,
        allow_reorder: bool = False,
    ) -> None:
        validated = reconcile_tick_stream(
            exchange=exchange,
            symbol=symbol,
            ticks=ticks,
            rest_backfiller=rest_backfiller,
            allow_reorder=allow_reorder,
        )
        self._ticks[(exchange, symbol)] = validated

    def load_orderbooks(self, exchange: str, symbol: str, snapshots: list[OrderBookSnapshot]) -> None:
        for ob in snapshots:
            if ob.exchange != exchange:
                raise CrossExchangeIsolationError(exchange, ob.exchange, symbol)
            if ob.symbol != symbol:
                raise DataIntegrityError(f"OrderBook symbol mismatch: expected {symbol}, got {ob.symbol}")
        ordered = sorted(snapshots, key=lambda s: (s.exchange_ts_ms, s.last_update_id))
        self._orderbooks[(exchange, symbol)] = ordered
        self._ob_ts_index[(exchange, symbol)] = [s.exchange_ts_ms for s in ordered]

    def load_mark_prices(self, exchange: str, symbol: str, prices: list[MarkIndexPrice]) -> None:
        for mp in prices:
            if mp.exchange != exchange:
                raise CrossExchangeIsolationError(exchange, mp.exchange, symbol)
            if mp.symbol != symbol:
                raise DataIntegrityError(f"MarkPrice symbol mismatch: expected {symbol}, got {mp.symbol}")
        ordered = sorted(prices, key=lambda p: p.exchange_ts_ms)
        self._mark_prices[(exchange, symbol)] = ordered
        self._mark_ts_index[(exchange, symbol)] = [p.exchange_ts_ms for p in ordered]

    def load_funding_rates(self, exchange: str, symbol: str, rates: list[FundingRatePoint]) -> None:
        for fp in rates:
            if fp.exchange != exchange:
                raise CrossExchangeIsolationError(exchange, fp.exchange, symbol)
            if fp.symbol != symbol:
                raise DataIntegrityError(f"FundingRate symbol mismatch: expected {symbol}, got {fp.symbol}")
        self._funding_rates[(exchange, symbol)] = sorted(rates, key=lambda r: r.funding_ts_ms)

    def all_timeline_timestamps(self) -> list[int]:
        """Sorted union of event timestamps across loaded historical streams for event-driven simulation."""
        ts_set: set[int] = set()
        for bars in self._bars.values():
            for b in bars:
                if b.is_closed:
                    ts_set.add(b.close_time_ms)
        for ticks in self._ticks.values():
            for t in ticks:
                ts_set.add(t.exchange_ts_ms)
        for obs in self._orderbooks.values():
            for o in obs:
                ts_set.add(o.exchange_ts_ms)
        for mps in self._mark_prices.values():
            for m in mps:
                ts_set.add(m.exchange_ts_ms)
        for frs in self._funding_rates.values():
            for f in frs:
                ts_set.add(f.funding_ts_ms)
        return sorted(ts_set)


class StreamingDataFeed(CursorGuardedDataFeed):
    """Real-time / Shadow / Live streaming DataFeed backed by OrderBookMaintainer and sequence checks."""

    def __init__(self, clock: Clock, source: str = "realtime_ws") -> None:
        super().__init__(clock=clock, source=source)
        self._ob_maintainers: dict[tuple[str, str], OrderBookMaintainer] = {}

    def attach_orderbook_maintainer(self, maintainer: OrderBookMaintainer) -> None:
        self._ob_maintainers[(maintainer.exchange, maintainer.symbol)] = maintainer

    def on_bar(self, bar: Bar) -> None:
        key = (bar.exchange, bar.symbol, bar.interval)
        bucket = self._bars.setdefault(key, [])
        if bucket and bucket[-1].open_time_ms == bar.open_time_ms:
            bucket[-1] = bar
        elif bucket and bar.open_time_ms < bucket[-1].open_time_ms:
            raise DataIntegrityError(
                f"Out-of-order streaming bar on {bar.exchange}:{bar.symbol}: {bar.open_time_ms} < {bucket[-1].open_time_ms}"
            )
        else:
            bucket.append(bar)

    def on_tick(self, tick: Tick, rest_backfiller: TickBackfiller | None = None) -> None:
        key = (tick.exchange, tick.symbol)
        bucket = list(self._ticks.get(key, []))
        bucket.append(tick)
        self._ticks[key] = reconcile_tick_stream(
            exchange=tick.exchange,
            symbol=tick.symbol,
            ticks=bucket,
            rest_backfiller=rest_backfiller,
            allow_reorder=False,
        )

    def on_orderbook_snapshot(self, snapshot: OrderBookSnapshot) -> OrderBookSnapshot:
        key = (snapshot.exchange, snapshot.symbol)
        maintainer = self._ob_maintainers.get(key)
        if maintainer is None:
            maintainer = OrderBookMaintainer(snapshot.exchange, snapshot.symbol)
            self._ob_maintainers[key] = maintainer
        rebuilt = maintainer.apply_snapshot(snapshot)
        self._append_ob_snapshot(rebuilt)
        return rebuilt

    def on_orderbook_delta(self, delta: OrderBookDelta) -> OrderBookSnapshot | None:
        key = (delta.exchange, delta.symbol)
        maintainer = self._ob_maintainers.get(key)
        if maintainer is None:
            maintainer = OrderBookMaintainer(delta.exchange, delta.symbol)
            self._ob_maintainers[key] = maintainer
        rebuilt = maintainer.push_delta(delta)
        if rebuilt is not None:
            self._append_ob_snapshot(rebuilt)
        return rebuilt

    def _append_ob_snapshot(self, snap: OrderBookSnapshot) -> None:
        key = (snap.exchange, snap.symbol)
        obs = self._orderbooks.setdefault(key, [])
        idx = self._ob_ts_index.setdefault(key, [])
        obs.append(snap)
        idx.append(snap.exchange_ts_ms)

    def on_mark_price(self, mp: MarkIndexPrice) -> None:
        key = (mp.exchange, mp.symbol)
        mps = self._mark_prices.setdefault(key, [])
        idx = self._mark_ts_index.setdefault(key, [])
        mps.append(mp)
        idx.append(mp.exchange_ts_ms)

    def on_funding_rate(self, fp: FundingRatePoint) -> None:
        key = (fp.exchange, fp.symbol)
        frs = self._funding_rates.setdefault(key, [])
        frs.append(fp)
        frs.sort(key=lambda r: r.funding_ts_ms)
