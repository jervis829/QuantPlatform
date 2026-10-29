"""BacktestBroker implementing crypto exchange matching, margin, Mark-Price liquidation, fees, funding, borrow interest, and delivery settlement (P4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import uuid

from quant_platform.backtest.fees import compute_fill_fee
from quant_platform.backtest.margin import (
    compute_funding_payment_amount,
    compute_liquidation_price,
    compute_maintenance_margin,
    compute_unrealized_pnl,
    should_liquidate_at_mark_price,
)
from quant_platform.backtest.matching import SlippageModelConfig, walk_orderbook_levels
from quant_platform.core.decimal_utils import ONE, ZERO
from quant_platform.core.enums import (
    ContractType,
    LiquidityRole,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RunEnv,
    TimeInForce,
)
from quant_platform.core.interfaces import BrokerAdapter, Clock, DataFeed, FillCallback, OrderUpdateCallback
from quant_platform.core.models import (
    Balance,
    Bar,
    EquityPoint,
    Fill,
    FundingPayment,
    FundingRatePoint,
    MarkIndexPrice,
    Order,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderRequest,
    Position,
    SymbolSpec,
    Tick,
)
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.exceptions import BrokerNotReadyError, SymbolSpecViolationError
from quant_platform.specs.normalizer import normalize_and_validate_order


@dataclass
class _RestingOrderEntry:
    order: Order
    queue_ahead_qty: Decimal


class BacktestBroker(BrokerAdapter):
    """
    Event-driven simulated exchange & margin ledger implementing BrokerAdapter (Red Line 4).
    Supports Spot, USDT-M Perpetual, and Delivery contracts with exact Decimal arithmetic.
    """

    def __init__(
        self,
        *,
        env: RunEnv = RunEnv.BACKTEST,
        run_id: str,
        strategy_id: str,
        clock: Clock,
        data_feed: DataFeed,
        ledger: UnifiedLedgerRepository | None = None,
        initial_balances: dict[str, Decimal] | None = None,
        default_exchange: str = "binance",
        vip_level: int = 0,
        use_platform_token_discount: bool = False,
        slippage_config: SlippageModelConfig | None = None,
        liquidation_penalty_rate: Decimal = Decimal("0.005"),
        delivery_fee_rate: Decimal = Decimal("0.00015"),
        allow_spot_borrow: bool = False,
        hourly_borrow_rates: dict[str, Decimal] | None = None,
        maintenance_windows: list[tuple[int, int]] | None = None,
    ) -> None:
        self.env = env
        self.run_id = run_id
        self.strategy_id = strategy_id
        self._clock = clock
        self._data_feed = data_feed
        self._ledger = ledger
        self.vip_level = vip_level
        self.use_platform_token_discount = use_platform_token_discount
        self.slippage_config = slippage_config or SlippageModelConfig()
        self.liquidation_penalty_rate = liquidation_penalty_rate
        self.delivery_fee_rate = delivery_fee_rate
        self.allow_spot_borrow = allow_spot_borrow
        self.hourly_borrow_rates: dict[str, Decimal] = dict(hourly_borrow_rates or {})
        self.maintenance_windows: list[tuple[int, int]] = list(maintenance_windows or [])

        self._ready = False
        self._seq = 0
        self._cash: dict[tuple[str, str], Decimal] = {}
        self._borrowed: dict[tuple[str, str], Decimal] = {}
        self._accrued_interest: dict[tuple[str, str], Decimal] = {}
        self._last_interest_ts_ms: int | None = None

        for asset, amt in (initial_balances or {"USDT": Decimal("10000")}).items():
            self._cash[(default_exchange, asset)] = amt

        self._orders: dict[str, Order] = {}
        self._resting: dict[str, _RestingOrderEntry] = {}
        self._positions: dict[tuple[str, str], Position] = {}
        self._funding_payments: list[FundingPayment] = []
        self._settled_funding_keys: set[tuple[str, str, int]] = set()
        self._live_books: dict[tuple[str, str], OrderBookSnapshot] = {}
        self._mark_prices: dict[tuple[str, str], Decimal] = {}
        self._last_prices: dict[tuple[str, str], Decimal] = {}

        self._cumulative_fees = ZERO
        self._cumulative_funding = ZERO
        self._cumulative_interest = ZERO
        self._cumulative_realized_pnl = ZERO
        self._peak_equity = ZERO

        self._order_callbacks: list[OrderUpdateCallback] = []
        self._fill_callbacks: list[FillCallback] = []

    def is_ready(self) -> bool:
        return self._ready

    def startup_sync(self) -> dict[str, Any]:
        self._ready = True
        self._last_interest_ts_ms = self._clock.now_ms()
        return {"ready": True, "synced_orders": 0, "synced_positions": len(self._positions)}

    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        self._order_callbacks.append(callback)

    def register_fill_callback(self, callback: FillCallback) -> None:
        self._fill_callbacks.append(callback)

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self.run_id[-8:]}_{self._seq:06d}"

    def _in_maintenance(self, ts_ms: int) -> bool:
        return any(start <= ts_ms <= end for start, end in self.maintenance_windows)

    def _resolve_book(self, exchange: str, symbol: str) -> OrderBookSnapshot | None:
        key = (exchange, symbol)
        now = self._clock.now_ms()
        feed_ob = self._data_feed.get_orderbook(exchange, symbol, at_ts_ms=now)
        cached = self._live_books.get(key)
        if feed_ob is not None and (cached is None or feed_ob.exchange_ts_ms > cached.exchange_ts_ms):
            self._live_books[key] = feed_ob
            return feed_ob
        if cached is not None:
            return cached
        last_px = self._last_prices.get(key)
        if last_px is not None:
            return OrderBookSnapshot(
                exchange=exchange,
                symbol=symbol,
                bids=[OrderBookLevel(price=last_px, quantity=Decimal("1000000"))],
                asks=[OrderBookLevel(price=last_px, quantity=Decimal("1000000"))],
                last_update_id=now,
                exchange_ts_ms=now,
                local_recv_ts_ms=now,
            )
        # Synthesize minimal top-of-book from latest closed bar if only bar data was loaded
        bars = self._data_feed.get_bars(exchange, symbol, "1m", end_ts_ms=now, limit=1)
        if bars:
            px = bars[-1].close
            synth = OrderBookSnapshot(
                exchange=exchange,
                symbol=symbol,
                bids=[OrderBookLevel(price=px, quantity=Decimal("1000000"))],
                asks=[OrderBookLevel(price=px, quantity=Decimal("1000000"))],
                last_update_id=now,
                exchange_ts_ms=now,
                local_recv_ts_ms=now,
            )
            return synth
        return None

    def _reference_price(self, exchange: str, symbol: str) -> Decimal | None:
        ob = self._resolve_book(exchange, symbol)
        if ob is not None:
            if ob.mid_price is not None:
                return ob.mid_price
            if ob.best_ask is not None:
                return ob.best_ask
            if ob.best_bid is not None:
                return ob.best_bid
        return self._mark_prices.get((exchange, symbol)) or self._last_prices.get((exchange, symbol))

    def _emit_order_update(self, order: Order) -> None:
        self._orders[order.client_order_id] = order
        if self._ledger is not None:
            self._ledger.upsert_order(order)
        for cb in self._order_callbacks:
            cb(order)

    def _emit_fill(self, fill: Fill) -> None:
        if self._ledger is not None:
            self._ledger.record_fill(fill)
        for cb in self._fill_callbacks:
            cb(fill)

    def submit_order(self, request: OrderRequest) -> Order:
        if not self._ready:
            raise BrokerNotReadyError(request.exchange)

        now = self._clock.now_ms()
        spec = self._data_feed.get_symbol_spec(request.exchange, request.symbol, at_ts_ms=now)

        # 1. Maintenance window check
        if self._in_maintenance(now):
            rejected = self._build_order_from_request(
                request,
                spec=spec,
                status=OrderStatus.REJECTED,
                reject_reason="EXCHANGE_IN_MAINTENANCE",
                ts_ms=now,
            )
            self._emit_order_update(rejected)
            return rejected

        # 2. Strict SymbolSpec validation (exchange rejects precision/minQty/minNotional violations)
        ref_px = self._reference_price(request.exchange, request.symbol)
        try:
            validated = normalize_and_validate_order(
                request,
                spec,
                reference_price=ref_px,
                auto_round=False,
                now_ms=now,
            )
        except SymbolSpecViolationError as exc:
            rejected = self._build_order_from_request(
                request,
                spec=spec,
                status=OrderStatus.REJECTED,
                reject_reason=exc.code,
                ts_ms=now,
            )
            self._emit_order_update(rejected)
            return rejected

        order = self._build_order_from_request(
            validated,
            spec=spec,
            status=OrderStatus.NEW,
            reject_reason=None,
            ts_ms=now,
        )

        ob = self._resolve_book(validated.exchange, validated.symbol)
        opposite_levels = (ob.asks if validated.side == OrderSide.BUY else ob.bids) if ob else []
        same_side_levels = (ob.bids if validated.side == OrderSide.BUY else ob.asks) if ob else []

        # 3. Post-Only (GTX) check: reject/expire if order would immediately cross opposite book
        if validated.post_only or validated.time_in_force == TimeInForce.GTX:
            assert validated.price is not None
            would_take = False
            if validated.side == OrderSide.BUY and opposite_levels and validated.price >= opposite_levels[0].price:
                would_take = True
            elif validated.side == OrderSide.SELL and opposite_levels and validated.price <= opposite_levels[0].price:
                would_take = True
            if would_take:
                expired_gtx = order.model_copy(
                    update={
                        "status": OrderStatus.EXPIRED_IN_MATCH,
                        "reject_reason": "POST_ONLY_WOULD_TAKE",
                        "remaining_qty": ZERO,
                    }
                )
                self._emit_order_update(expired_gtx)
                return expired_gtx

        # 4. FOK pre-check: require full quantity available across crossable levels
        if validated.time_in_force == TimeInForce.FOK:
            slices_preview, _ = walk_orderbook_levels(
                side=validated.side,
                quantity=validated.quantity,
                levels=opposite_levels,
                limit_price=validated.price if validated.order_type == OrderType.LIMIT else None,
                extra_impact_bps=self.slippage_config.extra_impact_bps,
            )
            avail = sum((s[1] for s in slices_preview), ZERO)
            if avail < validated.quantity:
                expired_fok = order.model_copy(
                    update={
                        "status": OrderStatus.EXPIRED,
                        "reject_reason": "FOK_INSUFFICIENT_LIQUIDITY",
                        "remaining_qty": ZERO,
                    }
                )
                self._emit_order_update(expired_fok)
                return expired_fok

        # 5. Immediate matching against opposite orderbook levels (walk the book)
        can_cross = validated.order_type == OrderType.MARKET or (
            validated.order_type == OrderType.LIMIT
            and validated.price is not None
            and bool(opposite_levels)
            and (
                (validated.side == OrderSide.BUY and validated.price >= opposite_levels[0].price)
                or (validated.side == OrderSide.SELL and validated.price <= opposite_levels[0].price)
            )
        )

        if can_cross:
            slices, remaining_opp = walk_orderbook_levels(
                side=validated.side,
                quantity=validated.quantity,
                levels=opposite_levels,
                limit_price=validated.price if validated.order_type == OrderType.LIMIT else None,
                extra_impact_bps=self.slippage_config.extra_impact_bps,
            )
            if ob is not None:
                if validated.side == OrderSide.BUY:
                    self._live_books[(validated.exchange, validated.symbol)] = ob.model_copy(
                        update={"asks": remaining_opp, "exchange_ts_ms": now}
                    )
                else:
                    self._live_books[(validated.exchange, validated.symbol)] = ob.model_copy(
                        update={"bids": remaining_opp, "exchange_ts_ms": now}
                    )

            for fill_px, fill_qty, ref_level_px, slip_bps in slices:
                order = self._apply_fill_to_order_and_portfolio(
                    order=order,
                    spec=spec,
                    fill_price=fill_px,
                    fill_qty=fill_qty,
                    reference_price=ref_level_px,
                    slippage_bps=slip_bps,
                    liquidity=LiquidityRole.TAKER,
                    snapshot_ref=f"ob:{ob.last_update_id}" if ob else None,
                    ts_ms=now,
                )

        # 6. Post-match handling of any remaining_qty
        if order.remaining_qty > ZERO:
            if validated.order_type == OrderType.MARKET or validated.time_in_force == TimeInForce.IOC:
                reason = "EMPTY_ORDERBOOK" if order.filled_qty == ZERO else "IOC_OR_MARKET_UNFILLED_EXPIRED"
                order = order.model_copy(
                    update={
                        "status": OrderStatus.EXPIRED,
                        "reject_reason": reason,
                        "remaining_qty": ZERO,
                        "updated_ts_ms": now,
                    }
                )
                self._emit_order_update(order)
                return order

            # Resting GTC / GTX Limit order: initialize queue position at limit price
            assert order.price is not None
            level_qty = ZERO
            for lvl in same_side_levels:
                if lvl.price == order.price:
                    level_qty = lvl.quantity
                    break
            queue_ahead = level_qty * self.slippage_config.queue_ahead_ratio
            self._resting[order.client_order_id] = _RestingOrderEntry(order=order, queue_ahead_qty=queue_ahead)
            self._emit_order_update(order)
            return order

        self._emit_order_update(order)
        return order

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        now = self._clock.now_ms()
        if self._in_maintenance(now):
            existing = self._orders[client_order_id]
            return existing
        entry = self._resting.pop(client_order_id, None)
        if entry is None:
            existing = self._orders[client_order_id]
            return existing
        canceled = entry.order.model_copy(
            update={
                "status": OrderStatus.CANCELED,
                "remaining_qty": ZERO,
                "updated_ts_ms": now,
                "exchange_ts_ms": now,
                "local_recv_ts_ms": now,
            }
        )
        self._emit_order_update(canceled)
        return canceled

    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        return self._orders.get(client_order_id)

    def query_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[Order]:
        return [
            e.order
            for e in self._resting.values()
            if (exchange is None or e.order.exchange == exchange)
            and (symbol is None or e.order.symbol == symbol)
        ]

    def query_positions(self, exchange: str | None = None, symbol: str | None = None) -> list[Position]:
        return [
            p
            for (ex, sym), p in sorted(self._positions.items())
            if (exchange is None or ex == exchange) and (symbol is None or sym == symbol)
        ]

    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        now = self._clock.now_ms()
        assets = set(self._cash.keys()) | set(self._borrowed.keys())
        out: list[Balance] = []
        for ex, asset in sorted(assets):
            if exchange is not None and ex != exchange:
                continue
            cash_val = self._cash.get((ex, asset), ZERO)
            borrowed_val = self._borrowed.get((ex, asset), ZERO)
            interest_val = self._accrued_interest.get((ex, asset), ZERO)
            locked_margin = sum(
                (
                    p.isolated_margin if p.margin_mode == MarginMode.ISOLATED else p.initial_margin
                    for (pex, _), p in self._positions.items()
                    if pex == ex and p.quantity != ZERO and asset == "USDT"
                ),
                ZERO,
            )
            upnl_sum = sum(
                (
                    p.unrealized_pnl
                    for (pex, _), p in self._positions.items()
                    if pex == ex and p.quantity != ZERO and asset == "USDT"
                ),
                ZERO,
            )
            avail = max(ZERO, cash_val - locked_margin)
            out.append(
                Balance(
                    env=self.env,
                    run_id=self.run_id,
                    exchange=ex,
                    asset=asset,
                    total=cash_val - interest_val,
                    available=avail,
                    locked=locked_margin,
                    borrowed=borrowed_val,
                    accrued_interest=interest_val,
                    unrealized_pnl=upnl_sum,
                    ts_ms=now,
                )
            )
        return out

    def query_funding_payments(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        since_ms: int | None = None,
    ) -> list[FundingPayment]:
        return [
            fp
            for fp in self._funding_payments
            if (exchange is None or fp.exchange == exchange)
            and (symbol is None or fp.symbol == symbol)
            and (since_ms is None or fp.settlement_ts_ms >= since_ms)
        ]

    # ------------------------------------------------------------------
    # Market Event Processors (OrderBook, Tick, Bar, MarkPrice, Funding)
    # ------------------------------------------------------------------

    def on_orderbook(self, snapshot: OrderBookSnapshot) -> list[Fill]:
        self._live_books[(snapshot.exchange, snapshot.symbol)] = snapshot
        if snapshot.mid_price is not None:
            self._last_prices[(snapshot.exchange, snapshot.symbol)] = snapshot.mid_price
        fills: list[Fill] = []
        now = snapshot.exchange_ts_ms
        spec = self._data_feed.get_symbol_spec(snapshot.exchange, snapshot.symbol, at_ts_ms=self._clock.now_ms())

        for cid in list(self._resting.keys()):
            entry = self._resting.get(cid)
            if entry is None:
                continue
            ord_obj = entry.order
            if ord_obj.exchange != snapshot.exchange or ord_obj.symbol != snapshot.symbol:
                continue
            assert ord_obj.price is not None
            crossed = (
                ord_obj.side == OrderSide.BUY
                and snapshot.best_ask is not None
                and snapshot.best_ask <= ord_obj.price
            ) or (
                ord_obj.side == OrderSide.SELL
                and snapshot.best_bid is not None
                and snapshot.best_bid >= ord_obj.price
            )
            if crossed:
                fill_qty = ord_obj.remaining_qty
                ord_updated, fill = self._execute_maker_fill(
                    entry=entry,
                    spec=spec,
                    fill_qty=fill_qty,
                    snapshot_ref=f"ob:{snapshot.last_update_id}",
                    ts_ms=now,
                )
                fills.append(fill)
                if ord_updated.remaining_qty <= ZERO:
                    self._resting.pop(cid, None)
        return fills

    def on_tick(self, tick: Tick) -> list[Fill]:
        """
        Process trade tick:
        - Updates last trade price (NEVER triggers contract liquidation — liquidation uses Mark Price only!)
        - Advances resting limit order queue positions & generates MAKER fills.
        """
        self._last_prices[(tick.exchange, tick.symbol)] = tick.price
        fills: list[Fill] = []
        spec = self._data_feed.get_symbol_spec(tick.exchange, tick.symbol, at_ts_ms=self._clock.now_ms())

        for cid in list(self._resting.keys()):
            entry = self._resting.get(cid)
            if entry is None:
                continue
            ord_obj = entry.order
            if ord_obj.exchange != tick.exchange or ord_obj.symbol != tick.symbol:
                continue
            assert ord_obj.price is not None

            crossed_through = (ord_obj.side == OrderSide.BUY and tick.price < ord_obj.price) or (
                ord_obj.side == OrderSide.SELL and tick.price > ord_obj.price
            )
            at_limit = tick.price == ord_obj.price

            fill_qty = ZERO
            if crossed_through:
                fill_qty = ord_obj.remaining_qty
                entry.queue_ahead_qty = ZERO
            elif at_limit:
                participating_vol = tick.quantity * self.slippage_config.trade_participation_rate
                if participating_vol <= entry.queue_ahead_qty:
                    entry.queue_ahead_qty -= participating_vol
                else:
                    avail_for_order = participating_vol - entry.queue_ahead_qty
                    entry.queue_ahead_qty = ZERO
                    fill_qty = min(ord_obj.remaining_qty, avail_for_order)

            if fill_qty > ZERO:
                ord_updated, fill = self._execute_maker_fill(
                    entry=entry,
                    spec=spec,
                    fill_qty=fill_qty,
                    snapshot_ref=f"tick:{tick.trade_id}",
                    ts_ms=tick.exchange_ts_ms,
                )
                fills.append(fill)
                if ord_updated.remaining_qty <= ZERO:
                    self._resting.pop(cid, None)
        return fills

    def on_bar(self, bar: Bar) -> list[Fill]:
        key = (bar.exchange, bar.symbol)
        self._last_prices[key] = bar.close
        cached_ob = self._live_books.get(key)
        if cached_ob is not None and cached_ob.exchange_ts_ms < bar.close_time_ms:
            feed_ob = self._data_feed.get_orderbook(bar.exchange, bar.symbol, at_ts_ms=bar.close_time_ms)
            if feed_ob is None or feed_ob.exchange_ts_ms < bar.close_time_ms:
                self._live_books.pop(key, None)
        fills: list[Fill] = []
        spec = self._data_feed.get_symbol_spec(bar.exchange, bar.symbol, at_ts_ms=self._clock.now_ms())
        for cid in list(self._resting.keys()):
            entry = self._resting.get(cid)
            if entry is None:
                continue
            ord_obj = entry.order
            if ord_obj.exchange != bar.exchange or ord_obj.symbol != bar.symbol:
                continue
            assert ord_obj.price is not None
            crossed = (ord_obj.side == OrderSide.BUY and bar.low < ord_obj.price) or (
                ord_obj.side == OrderSide.SELL and bar.high > ord_obj.price
            )
            if crossed:
                ord_updated, fill = self._execute_maker_fill(
                    entry=entry,
                    spec=spec,
                    fill_qty=ord_obj.remaining_qty,
                    snapshot_ref=f"bar:{bar.open_time_ms}",
                    ts_ms=bar.close_time_ms,
                )
                fills.append(fill)
                if ord_updated.remaining_qty <= ZERO:
                    self._resting.pop(cid, None)
        return fills

    def on_mark_price(self, mp: MarkIndexPrice) -> list[Fill]:
        """Update position Mark Price & Unrealized PnL, and evaluate Mark-Price-only liquidation."""
        key = (mp.exchange, mp.symbol)
        self._mark_prices[key] = mp.mark_price
        pos = self._positions.get(key)
        if pos is None or pos.quantity == ZERO:
            return []

        spec = self._data_feed.get_symbol_spec(mp.exchange, mp.symbol, at_ts_ms=self._clock.now_ms())
        if spec.contract_type == ContractType.SPOT:
            updated_pos = pos.model_copy(
                update={
                    "mark_price": mp.mark_price,
                    "unrealized_pnl": pos.quantity * (mp.mark_price - pos.entry_price),
                    "updated_ts_ms": mp.exchange_ts_ms,
                }
            )
            self._positions[key] = updated_pos
            if self._ledger is not None:
                self._ledger.upsert_position(updated_pos)
            return []

        notional = abs(pos.quantity) * mp.mark_price * spec.contract_size
        mm, _, _ = compute_maintenance_margin(spec, notional)
        upnl = compute_unrealized_pnl(
            position_qty=pos.quantity,
            entry_price=pos.entry_price,
            mark_price=mp.mark_price,
            contract_size=spec.contract_size,
        )
        backing = (
            pos.isolated_margin
            if pos.margin_mode == MarginMode.ISOLATED
            else self._cash.get((mp.exchange, spec.margin_asset), ZERO)
        )
        liq_px = compute_liquidation_price(
            spec=spec,
            position_qty=pos.quantity,
            entry_price=pos.entry_price,
            margin_backing=backing,
        )

        if should_liquidate_at_mark_price(
            spec=spec,
            position_qty=pos.quantity,
            entry_price=pos.entry_price,
            mark_price=mp.mark_price,
            margin_backing=backing,
        ):
            liq_fill = self._execute_liquidation(pos=pos, spec=spec, mark_price=mp.mark_price, ts_ms=mp.exchange_ts_ms)
            return [liq_fill]

        updated = pos.model_copy(
            update={
                "mark_price": mp.mark_price,
                "unrealized_pnl": upnl,
                "maintenance_margin": mm,
                "liquidation_price": liq_px,
                "updated_ts_ms": mp.exchange_ts_ms,
            }
        )
        self._positions[key] = updated
        if self._ledger is not None:
            self._ledger.upsert_position(updated)
        return []

    def on_funding_rate(self, fp_point: FundingRatePoint) -> FundingPayment | None:
        key = (fp_point.exchange, fp_point.symbol)
        dedup_key = (fp_point.exchange, fp_point.symbol, fp_point.funding_ts_ms)
        if dedup_key in self._settled_funding_keys:
            return None
        pos = self._positions.get(key)
        if pos is None or pos.quantity == ZERO:
            return None
        spec = self._data_feed.get_symbol_spec(fp_point.exchange, fp_point.symbol, at_ts_ms=self._clock.now_ms())
        if spec.contract_type != ContractType.PERPETUAL:
            return None

        self._settled_funding_keys.add(dedup_key)
        amount = compute_funding_payment_amount(
            position_qty=pos.quantity,
            mark_price=fp_point.mark_price,
            contract_size=spec.contract_size,
            funding_rate=fp_point.funding_rate,
        )
        cash_key = (fp_point.exchange, spec.margin_asset)
        self._cash[cash_key] = self._cash.get(cash_key, ZERO) + amount
        self._cumulative_funding += amount

        updated_pos = pos.model_copy(
            update={
                "cumulative_funding": pos.cumulative_funding + amount,
                "mark_price": fp_point.mark_price,
                "updated_ts_ms": fp_point.funding_ts_ms,
            }
        )
        self._positions[key] = updated_pos
        if self._ledger is not None:
            self._ledger.upsert_position(updated_pos)

        payment = FundingPayment(
            payment_id=self._next_id("fund"),
            env=self.env,
            run_id=self.run_id,
            exchange=fp_point.exchange,
            symbol=fp_point.symbol,
            funding_rate=fp_point.funding_rate,
            mark_price=fp_point.mark_price,
            position_qty=pos.quantity,
            amount=amount,
            asset=spec.margin_asset,
            settlement_ts_ms=fp_point.funding_ts_ms,
            local_recv_ts_ms=fp_point.local_recv_ts_ms,
        )
        self._funding_payments.append(payment)
        if self._ledger is not None:
            self._ledger.record_funding_payment(payment)
        return payment

    def accrue_borrow_interest(self, now_ms: int) -> Decimal:
        """Accrue hourly borrow interest across Spot Margin / Unified Account liabilities."""
        if self._last_interest_ts_ms is None:
            self._last_interest_ts_ms = now_ms
            return ZERO
        elapsed_ms = now_ms - self._last_interest_ts_ms
        hours = elapsed_ms // 3_600_000
        if hours <= 0:
            return ZERO
        self._last_interest_ts_ms += hours * 3_600_000
        added_total = ZERO
        for (ex, asset), borrowed_amt in list(self._borrowed.items()):
            if borrowed_amt <= ZERO:
                continue
            rate = self.hourly_borrow_rates.get(asset, Decimal("0.00005"))
            interest = borrowed_amt * rate * Decimal(hours)
            self._accrued_interest[(ex, asset)] = self._accrued_interest.get((ex, asset), ZERO) + interest
            added_total += interest
        self._cumulative_interest += added_total
        return added_total

    def process_delisting(self, exchange: str, symbol: str, settlement_price: Decimal) -> dict[str, Any]:
        """Cancel resting orders and force-settle open position upon symbol delisting."""
        now = self._clock.now_ms()
        for orig_ord in list(self.query_open_orders(exchange, symbol)):
            self.cancel_order(orig_ord.client_order_id, exchange, symbol)

        pos = self._positions.get((exchange, symbol))
        if pos is None or pos.quantity == ZERO:
            return {"settled_qty": ZERO, "realized_pnl": ZERO}

        spec = self._data_feed.get_symbol_spec(exchange, symbol, at_ts_ms=now)
        close_side = OrderSide.SELL if pos.quantity > ZERO else OrderSide.BUY
        abs_qty = abs(pos.quantity)
        pnl = pos.quantity * (settlement_price - pos.entry_price) * spec.contract_size
        cash_key = (exchange, spec.margin_asset)
        self._cash[cash_key] = self._cash.get(cash_key, ZERO) + pnl
        self._cumulative_realized_pnl += pnl

        closed_pos = pos.model_copy(
            update={
                "quantity": ZERO,
                "mark_price": settlement_price,
                "liquidation_price": None,
                "unrealized_pnl": ZERO,
                "realized_pnl": pos.realized_pnl + pnl,
                "isolated_margin": ZERO,
                "initial_margin": ZERO,
                "maintenance_margin": ZERO,
                "updated_ts_ms": now,
            }
        )
        self._positions[(exchange, symbol)] = closed_pos
        if self._ledger is not None:
            self._ledger.upsert_position(closed_pos)

        fill = Fill(
            fill_id=self._next_id("fill"),
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=f"delist_{uuid.uuid4().hex[:12]}",
            exchange=exchange,
            symbol=symbol,
            side=close_side,
            liquidity=LiquidityRole.TAKER,
            price=settlement_price,
            quantity=abs_qty,
            quote_qty=settlement_price * abs_qty * spec.contract_size,
            reference_price=settlement_price,
            slippage_bps=ZERO,
            fee=ZERO,
            fee_asset=spec.margin_asset,
            realized_pnl=pnl,
            mark_price=settlement_price,
            snapshot_ref="DELISTING_SETTLEMENT",
            exchange_ts_ms=now,
            local_recv_ts_ms=now,
        )
        self._emit_fill(fill)
        return {"settled_qty": abs_qty, "realized_pnl": pnl}

    def settle_and_rollover_delivery(
        self,
        *,
        exchange: str,
        expiring_symbol: str,
        settlement_price: Decimal,
        rollover_to_symbol: str | None = None,
    ) -> dict[str, Any]:
        """Settle an expiring delivery contract at settlement_price and optionally roll position to next contract."""
        now = self._clock.now_ms()
        for open_o in list(self.query_open_orders(exchange, expiring_symbol)):
            self.cancel_order(open_o.client_order_id, exchange, expiring_symbol)

        pos = self._positions.get((exchange, expiring_symbol))
        if pos is None or pos.quantity == ZERO:
            return {"settled_qty": ZERO, "realized_pnl": ZERO, "delivery_fee": ZERO}

        spec = self._data_feed.get_symbol_spec(exchange, expiring_symbol, at_ts_ms=now)
        orig_qty = pos.quantity
        abs_qty = abs(orig_qty)
        notional = abs_qty * settlement_price * spec.contract_size
        delivery_fee = notional * self.delivery_fee_rate
        pnl = orig_qty * (settlement_price - pos.entry_price) * spec.contract_size

        cash_key = (exchange, spec.margin_asset)
        self._cash[cash_key] = self._cash.get(cash_key, ZERO) + pnl - delivery_fee
        self._cumulative_realized_pnl += pnl
        self._cumulative_fees += delivery_fee

        settled_pos = pos.model_copy(
            update={
                "quantity": ZERO,
                "mark_price": settlement_price,
                "liquidation_price": None,
                "unrealized_pnl": ZERO,
                "realized_pnl": pos.realized_pnl + pnl,
                "isolated_margin": ZERO,
                "initial_margin": ZERO,
                "maintenance_margin": ZERO,
                "updated_ts_ms": now,
            }
        )
        self._positions[(exchange, expiring_symbol)] = settled_pos
        if self._ledger is not None:
            self._ledger.upsert_position(settled_pos)

        fill = Fill(
            fill_id=self._next_id("fill"),
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=f"settle_{uuid.uuid4().hex[:12]}",
            exchange=exchange,
            symbol=expiring_symbol,
            side=OrderSide.SELL if orig_qty > ZERO else OrderSide.BUY,
            liquidity=LiquidityRole.TAKER,
            price=settlement_price,
            quantity=abs_qty,
            quote_qty=notional,
            reference_price=settlement_price,
            slippage_bps=ZERO,
            fee=delivery_fee,
            fee_asset=spec.margin_asset,
            realized_pnl=pnl,
            mark_price=settlement_price,
            snapshot_ref="DELIVERY_SETTLEMENT",
            exchange_ts_ms=now,
            local_recv_ts_ms=now,
        )
        self._emit_fill(fill)

        roll_order: Order | None = None
        if rollover_to_symbol is not None:
            roll_order = self.submit_order(
                OrderRequest(
                    exchange=exchange,
                    symbol=rollover_to_symbol,
                    side=OrderSide.BUY if orig_qty > ZERO else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=abs_qty,
                    leverage=pos.leverage,
                    margin_mode=pos.margin_mode,
                    created_ts_ms=now,
                )
            )
        return {
            "settled_qty": abs_qty,
            "realized_pnl": pnl,
            "delivery_fee": delivery_fee,
            "rollover_order": roll_order,
        }

    def snapshot_equity(self, exchange: str = "binance") -> EquityPoint:
        now = self._clock.now_ms()
        cash_usdt = self._cash.get((exchange, "USDT"), ZERO)
        borrowed_usdt = self._borrowed.get((exchange, "USDT"), ZERO)
        interest_usdt = self._accrued_interest.get((exchange, "USDT"), ZERO)

        pos_val = ZERO
        upnl_total = ZERO
        gross_exp = ZERO
        net_exp = ZERO

        for (ex, sym), pos in self._positions.items():
            if ex != exchange or pos.quantity == ZERO:
                continue
            spec = self._data_feed.get_symbol_spec(ex, sym, at_ts_ms=now)
            px = (
                self._mark_prices.get((ex, sym))
                or self._last_prices.get((ex, sym))
                or pos.mark_price
                or pos.entry_price
            )
            signed_notional = pos.quantity * px * spec.contract_size
            gross_exp += abs(signed_notional)
            net_exp += signed_notional
            if spec.contract_type == ContractType.SPOT:
                pos_val += signed_notional
            else:
                upnl = compute_unrealized_pnl(
                    position_qty=pos.quantity,
                    entry_price=pos.entry_price,
                    mark_price=px,
                    contract_size=spec.contract_size,
                )
                upnl_total += upnl
                pos_val += abs(signed_notional)

        equity = cash_usdt - borrowed_usdt - interest_usdt + (
            sum(
                (
                    self._cash.get((exchange, a), ZERO) * (self._last_prices.get((exchange, f"{a}/USDT"), ZERO))
                    for (ex_a, a) in self._cash
                    if ex_a == exchange and a != "USDT"
                ),
                ZERO,
            )
            + upnl_total
        )
        if equity > self._peak_equity:
            self._peak_equity = equity
        dd_pct = ((self._peak_equity - equity) / self._peak_equity) if self._peak_equity > ZERO else ZERO

        pt = EquityPoint(
            env=self.env,
            run_id=self.run_id,
            ts_ms=now,
            equity=equity,
            cash=cash_usdt,
            position_value=pos_val,
            unrealized_pnl=upnl_total,
            realized_pnl=self._cumulative_realized_pnl,
            cumulative_fees=self._cumulative_fees,
            cumulative_funding=self._cumulative_funding,
            cumulative_interest=self._cumulative_interest,
            drawdown_pct=dd_pct,
            gross_exposure=gross_exp,
            net_exposure=net_exp,
        )
        if self._ledger is not None:
            self._ledger.record_equity_point(pt)
        return pt

    # ------------------------------------------------------------------
    # Internal execution & portfolio update helpers
    # ------------------------------------------------------------------

    def _build_order_from_request(
        self,
        req: OrderRequest,
        *,
        spec: SymbolSpec,
        status: OrderStatus,
        reject_reason: str | None,
        ts_ms: int,
    ) -> Order:
        rem = ZERO if status in {OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.EXPIRED_IN_MATCH} else req.quantity
        return Order(
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=req.client_order_id,
            exchange_order_id=self._next_id("exord"),
            exchange=req.exchange,
            symbol=req.symbol,
            side=req.side,
            position_side=req.position_side,
            order_type=req.order_type,
            time_in_force=req.time_in_force,
            post_only=req.post_only,
            reduce_only=req.reduce_only,
            price=req.price,
            stop_price=req.stop_price,
            quantity=req.quantity,
            filled_qty=ZERO,
            avg_fill_price=ZERO,
            remaining_qty=rem,
            fee_total=ZERO,
            fee_asset=spec.margin_asset,
            leverage=req.leverage,
            margin_mode=req.margin_mode,
            status=status,
            reject_reason=reject_reason,
            signal_id=req.signal_id,
            symbol_spec_version=spec.spec_version_id,
            created_ts_ms=req.created_ts_ms or ts_ms,
            updated_ts_ms=ts_ms,
            exchange_ts_ms=ts_ms,
            local_recv_ts_ms=ts_ms,
        )

    def _execute_maker_fill(
        self,
        *,
        entry: _RestingOrderEntry,
        spec: SymbolSpec,
        fill_qty: Decimal,
        snapshot_ref: str,
        ts_ms: int,
    ) -> tuple[Order, Fill]:
        assert entry.order.price is not None
        updated_order = self._apply_fill_to_order_and_portfolio(
            order=entry.order,
            spec=spec,
            fill_price=entry.order.price,
            fill_qty=fill_qty,
            reference_price=entry.order.price,
            slippage_bps=ZERO,
            liquidity=LiquidityRole.MAKER,
            snapshot_ref=snapshot_ref,
            ts_ms=ts_ms,
        )
        entry.order = updated_order
        self._emit_order_update(updated_order)
        # Retrieve latest emitted fill
        latest_fill = self._last_emitted_fill
        return updated_order, latest_fill

    def _apply_fill_to_order_and_portfolio(
        self,
        *,
        order: Order,
        spec: SymbolSpec,
        fill_price: Decimal,
        fill_qty: Decimal,
        reference_price: Decimal,
        slippage_bps: Decimal,
        liquidity: LiquidityRole,
        snapshot_ref: str | None,
        ts_ms: int,
    ) -> Order:
        fee_amt, fee_asset = compute_fill_fee(
            spec=spec,
            liquidity=liquidity,
            side=order.side,
            fill_price=fill_price,
            fill_qty=fill_qty,
            vip_level=self.vip_level,
            use_platform_token_discount=self.use_platform_token_discount,
        )
        realized_pnl = self._update_portfolio_on_fill(
            order=order,
            spec=spec,
            fill_price=fill_price,
            fill_qty=fill_qty,
            fee_amt=fee_amt,
            fee_asset=fee_asset,
            ts_ms=ts_ms,
        )

        new_filled = order.filled_qty + fill_qty
        new_rem = order.quantity - new_filled
        new_avg = ((order.avg_fill_price * order.filled_qty) + (fill_price * fill_qty)) / new_filled
        new_status = OrderStatus.FILLED if new_rem <= ZERO else OrderStatus.PARTIALLY_FILLED
        fee_in_usdt = fee_amt * fill_price if fee_asset == spec.base_asset else fee_amt
        self._cumulative_fees += fee_in_usdt

        mark_px = self._mark_prices.get((order.exchange, order.symbol), fill_price)
        fill = Fill(
            fill_id=self._next_id("fill"),
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=order.client_order_id,
            exchange_order_id=order.exchange_order_id,
            exchange_trade_id=self._next_id("trd"),
            exchange=order.exchange,
            symbol=order.symbol,
            side=order.side,
            liquidity=liquidity,
            price=fill_price,
            quantity=fill_qty,
            quote_qty=fill_price * fill_qty * spec.contract_size,
            reference_price=reference_price,
            slippage_bps=slippage_bps,
            fee=fee_amt,
            fee_asset=fee_asset,
            realized_pnl=realized_pnl,
            mark_price=mark_px,
            snapshot_ref=snapshot_ref,
            exchange_ts_ms=ts_ms,
            local_recv_ts_ms=ts_ms,
        )
        self._last_emitted_fill = fill
        self._emit_fill(fill)

        return order.model_copy(
            update={
                "filled_qty": new_filled,
                "remaining_qty": max(ZERO, new_rem),
                "avg_fill_price": new_avg,
                "fee_total": order.fee_total + fee_amt,
                "fee_asset": fee_asset,
                "status": new_status,
                "updated_ts_ms": ts_ms,
                "exchange_ts_ms": ts_ms,
                "local_recv_ts_ms": ts_ms,
            }
        )

    def _update_portfolio_on_fill(
        self,
        *,
        order: Order,
        spec: SymbolSpec,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee_amt: Decimal,
        fee_asset: str,
        ts_ms: int,
    ) -> Decimal:
        ex = order.exchange
        self._last_prices[(ex, order.symbol)] = fill_price

        if spec.contract_type == ContractType.SPOT:
            base_key = (ex, spec.base_asset)
            quote_key = (ex, spec.quote_asset)
            quote_notional = fill_price * fill_qty
            realized_pnl = ZERO

            pos = self._positions.get((ex, order.symbol))
            old_qty = pos.quantity if pos else ZERO
            old_entry = pos.entry_price if pos else ZERO

            if order.side == OrderSide.BUY:
                avail_quote = self._cash.get(quote_key, ZERO)
                if avail_quote >= quote_notional:
                    self._cash[quote_key] = avail_quote - quote_notional
                elif self.allow_spot_borrow:
                    deficit = quote_notional - max(ZERO, avail_quote)
                    self._cash[quote_key] = ZERO
                    self._borrowed[quote_key] = self._borrowed.get(quote_key, ZERO) + deficit
                else:
                    self._cash[quote_key] = avail_quote - quote_notional

                net_base_added = fill_qty - (fee_amt if fee_asset == spec.base_asset else ZERO)
                self._cash[base_key] = self._cash.get(base_key, ZERO) + net_base_added
                if fee_asset == spec.quote_asset:
                    self._cash[quote_key] = self._cash.get(quote_key, ZERO) - fee_amt

                new_qty = old_qty + net_base_added
                new_entry = (
                    ((old_qty * old_entry) + (net_base_added * fill_price)) / new_qty
                    if new_qty > ZERO and old_qty >= ZERO
                    else fill_price
                )
            else:
                self._cash[base_key] = self._cash.get(base_key, ZERO) - fill_qty
                net_quote_added = quote_notional - (fee_amt if fee_asset == spec.quote_asset else ZERO)
                borrowed_q = self._borrowed.get(quote_key, ZERO)
                if borrowed_q > ZERO:
                    repay = min(borrowed_q, net_quote_added)
                    self._borrowed[quote_key] = borrowed_q - repay
                    net_quote_added -= repay
                self._cash[quote_key] = self._cash.get(quote_key, ZERO) + net_quote_added
                if old_qty > ZERO:
                    closed = min(old_qty, fill_qty)
                    realized_pnl = closed * (fill_price - old_entry)
                    self._cumulative_realized_pnl += realized_pnl
                new_qty = max(ZERO, old_qty - fill_qty)
                new_entry = old_entry if new_qty > ZERO else ZERO

            spot_pos = Position(
                env=self.env,
                run_id=self.run_id,
                strategy_id=self.strategy_id,
                exchange=ex,
                symbol=order.symbol,
                margin_mode=order.margin_mode,
                leverage=order.leverage,
                quantity=new_qty,
                entry_price=new_entry,
                mark_price=fill_price,
                unrealized_pnl=ZERO,
                realized_pnl=(pos.realized_pnl if pos else ZERO) + realized_pnl,
                updated_ts_ms=ts_ms,
            )
            self._positions[(ex, order.symbol)] = spot_pos
            if self._ledger is not None:
                self._ledger.upsert_position(spot_pos)
            return realized_pnl

        # PERPETUAL / DELIVERY Contract position accounting
        signed_delta = fill_qty if order.side == OrderSide.BUY else -fill_qty
        key = (ex, order.symbol)
        pos = self._positions.get(key)
        old_qty = pos.quantity if pos else ZERO
        old_entry = pos.entry_price if pos else ZERO
        old_iso = pos.isolated_margin if pos else ZERO
        old_realized = pos.realized_pnl if pos else ZERO
        old_funding = pos.cumulative_funding if pos else ZERO

        realized_pnl = ZERO
        if old_qty == ZERO or (old_qty > ZERO and signed_delta > ZERO) or (old_qty < ZERO and signed_delta < ZERO):
            new_qty = old_qty + signed_delta
            new_entry = ((abs(old_qty) * old_entry) + (fill_qty * fill_price)) / abs(new_qty)
            added_im = (fill_qty * fill_price * spec.contract_size) / order.leverage
            new_iso = (old_iso + added_im) if order.margin_mode == MarginMode.ISOLATED else ZERO
        else:
            closed_qty = min(abs(old_qty), fill_qty)
            if old_qty > ZERO:
                realized_pnl = closed_qty * (fill_price - old_entry) * spec.contract_size
            else:
                realized_pnl = closed_qty * (old_entry - fill_price) * spec.contract_size

            new_qty = old_qty + signed_delta
            if new_qty == ZERO:
                new_entry = ZERO
                new_iso = ZERO
            elif (old_qty > ZERO and new_qty > ZERO) or (old_qty < ZERO and new_qty < ZERO):
                new_entry = old_entry
                new_iso = (old_iso * (abs(new_qty) / abs(old_qty))) if order.margin_mode == MarginMode.ISOLATED else ZERO
            else:
                new_entry = fill_price
                new_iso = (
                    (abs(new_qty) * fill_price * spec.contract_size) / order.leverage
                    if order.margin_mode == MarginMode.ISOLATED
                    else ZERO
                )

        cash_key = (ex, spec.margin_asset)
        self._cash[cash_key] = self._cash.get(cash_key, ZERO) + realized_pnl - fee_amt
        self._cumulative_realized_pnl += realized_pnl

        mark_px = self._mark_prices.get(key, fill_price)
        abs_new = abs(new_qty)
        notional = abs_new * mark_px * spec.contract_size
        im = (abs_new * new_entry * spec.contract_size / order.leverage) if abs_new > ZERO else ZERO
        mm, _, _ = compute_maintenance_margin(spec, notional) if abs_new > ZERO else (ZERO, ZERO, ZERO)
        upnl = compute_unrealized_pnl(
            position_qty=new_qty,
            entry_price=new_entry,
            mark_price=mark_px,
            contract_size=spec.contract_size,
        )
        backing = new_iso if order.margin_mode == MarginMode.ISOLATED else self._cash.get(cash_key, ZERO)
        liq_px = (
            compute_liquidation_price(
                spec=spec,
                position_qty=new_qty,
                entry_price=new_entry,
                margin_backing=backing,
            )
            if abs_new > ZERO
            else None
        )

        updated_pos = Position(
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            exchange=ex,
            symbol=order.symbol,
            position_side=PositionSide.NET,
            margin_mode=order.margin_mode,
            leverage=order.leverage,
            quantity=new_qty,
            entry_price=new_entry,
            mark_price=mark_px,
            liquidation_price=liq_px,
            unrealized_pnl=upnl,
            realized_pnl=old_realized + realized_pnl,
            isolated_margin=new_iso,
            initial_margin=im,
            maintenance_margin=mm,
            cumulative_funding=old_funding,
            updated_ts_ms=ts_ms,
        )
        self._positions[key] = updated_pos
        if self._ledger is not None:
            self._ledger.upsert_position(updated_pos)
        return realized_pnl

    def _execute_liquidation(
        self,
        *,
        pos: Position,
        spec: SymbolSpec,
        mark_price: Decimal,
        ts_ms: int,
    ) -> Fill:
        for open_o in list(self.query_open_orders(pos.exchange, pos.symbol)):
            self.cancel_order(open_o.client_order_id, pos.exchange, pos.symbol)

        abs_qty = abs(pos.quantity)
        close_side = OrderSide.SELL if pos.quantity > ZERO else OrderSide.BUY
        raw_pnl = pos.quantity * (mark_price - pos.entry_price) * spec.contract_size
        raw_penalty = abs_qty * mark_price * spec.contract_size * self.liquidation_penalty_rate

        if pos.margin_mode == MarginMode.ISOLATED:
            # Isolated bankruptcy protection: total loss (-raw_pnl + penalty) cannot exceed isolated_margin
            max_loss = pos.isolated_margin
            price_loss = -raw_pnl
            if price_loss >= max_loss:
                effective_pnl = -max_loss
                penalty = ZERO
            else:
                penalty = min(raw_penalty, max_loss - price_loss)
                effective_pnl = raw_pnl
        else:
            effective_pnl = raw_pnl
            penalty = raw_penalty

        cash_key = (pos.exchange, spec.margin_asset)
        self._cash[cash_key] = self._cash.get(cash_key, ZERO) + effective_pnl - penalty
        self._cumulative_realized_pnl += effective_pnl
        self._cumulative_fees += penalty

        closed_pos = pos.model_copy(
            update={
                "quantity": ZERO,
                "mark_price": mark_price,
                "liquidation_price": None,
                "unrealized_pnl": ZERO,
                "realized_pnl": pos.realized_pnl + effective_pnl,
                "isolated_margin": ZERO,
                "initial_margin": ZERO,
                "maintenance_margin": ZERO,
                "updated_ts_ms": ts_ms,
            }
        )
        self._positions[(pos.exchange, pos.symbol)] = closed_pos
        if self._ledger is not None:
            self._ledger.upsert_position(closed_pos)

        liq_fill = Fill(
            fill_id=self._next_id("liqfill"),
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=self._next_id("liqcid"),
            exchange=pos.exchange,
            symbol=pos.symbol,
            side=close_side,
            liquidity=LiquidityRole.TAKER,
            price=mark_price,
            quantity=abs_qty,
            quote_qty=abs_qty * mark_price * spec.contract_size,
            reference_price=mark_price,
            slippage_bps=ZERO,
            fee=penalty,
            fee_asset=spec.margin_asset,
            realized_pnl=effective_pnl,
            mark_price=mark_price,
            snapshot_ref="MARK_PRICE_LIQUIDATION",
            exchange_ts_ms=ts_ms,
            local_recv_ts_ms=ts_ms,
        )
        self._emit_fill(liq_fill)
        return liq_fill
