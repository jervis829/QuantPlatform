"""ExchangeBroker: live/testnet BrokerAdapter enforcing Startup Sync Gate & raw Decimal precision (P8 / Red Lines 4, 9, 10)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from quant_platform.core.decimal_utils import (
    ONE,
    ZERO,
    quantize_to_step as round_to_step,
    to_strict_decimal as to_decimal,
)
from quant_platform.core.enums import (
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
    Fill,
    FundingPayment,
    Order,
    OrderRequest,
    Position,
)
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import FillRecord, FundingPaymentRecord
from quant_platform.exceptions import BrokerNotReadyError
from quant_platform.live.transport import ExchangeTransportClient
from quant_platform.specs import normalize_and_validate_order


class ExchangeBroker(BrokerAdapter):
    """
    Authoritative ExchangeBroker implementing BrokerAdapter for Live & Testnet execution.
    Invariants:
    - Refuses all order submissions until `startup_sync()` completes (Red Line 10);
    - Normalizes every outgoing order against versioned `SymbolSpec` (Red Line 9);
    - Parses raw exchange string responses directly into `Decimal` without float intermediates.
    """

    def __init__(
        self,
        *,
        run_id: str,
        strategy_id: str,
        exchange: str,
        transport: ExchangeTransportClient,
        clock: Clock,
        data_feed: DataFeed,
        ledger: UnifiedLedgerRepository | None = None,
    ) -> None:
        self.env = RunEnv.LIVE
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.exchange = exchange
        self.transport = transport
        self._clock = clock
        self._data_feed = data_feed
        self._ledger = ledger

        self._ready = False
        self._orders: dict[str, Order] = {}
        self._positions: dict[tuple[str, str], Position] = {}
        self._balances: dict[str, Balance] = {}
        self._funding_payments: list[FundingPayment] = []
        self._seen_trade_ids: set[str] = set()
        self.recorded_fills_by_cid: dict[str, list[Fill]] = {}

        self._order_callbacks: list[OrderUpdateCallback] = []
        self._fill_callbacks: list[FillCallback] = []

    def is_ready(self) -> bool:
        return self._ready

    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        self._order_callbacks.append(callback)

    def register_fill_callback(self, callback: FillCallback) -> None:
        self._fill_callbacks.append(callback)

    def _emit_order(self, order: Order) -> None:
        self._orders[order.client_order_id] = order
        if self._ledger is not None:
            self._ledger.upsert_order(order)
        for cb in self._order_callbacks:
            cb(order)

    def _emit_fill(self, fill: Fill) -> None:
        tid = fill.exchange_trade_id or fill.fill_id
        if tid in self._seen_trade_ids:
            return
        self._seen_trade_ids.add(tid)
        self.recorded_fills_by_cid.setdefault(fill.client_order_id, []).append(fill)
        if self._ledger is not None:
            existing = self._ledger.session.execute(
                select(FillRecord).where(
                    FillRecord.env == self.env.value,
                    FillRecord.run_id == self.run_id,
                    FillRecord.fill_id == fill.fill_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                self._ledger.record_fill(fill)
        for cb in self._fill_callbacks:
            cb(fill)

    def startup_sync(self) -> dict[str, Any]:
        """Pull authoritative open orders, positions, balances, recent fills, and funding before enabling trading."""
        now = self._clock.now_ms()

        # 1. Balances
        raw_bals = self.transport.fetch_balances_raw()
        for rb in raw_bals:
            asset = str(rb["asset"])
            wallet = to_decimal(str(rb.get("walletBalance", rb.get("total", "0"))))
            avail = to_decimal(str(rb.get("availableBalance", rb.get("free", str(wallet)))))
            locked = max(ZERO, wallet - avail)
            bal = Balance(
                env=self.env,
                run_id=self.run_id,
                exchange=self.exchange,
                asset=asset,
                total=wallet,
                available=avail,
                locked=locked,
                ts_ms=now,
            )
            self._balances[asset] = bal
            if self._ledger is not None:
                self._ledger.record_balance_snapshot(bal)

        # 2. Positions
        raw_positions = self.transport.fetch_positions_raw()
        synced_pos_count = 0
        for rp in raw_positions:
            sym = str(rp["symbol"])
            qty = to_decimal(str(rp.get("positionAmt", "0")))
            entry_px = to_decimal(str(rp.get("entryPrice", "0")))
            mark_px = to_decimal(str(rp.get("markPrice", str(entry_px))))
            upnl = to_decimal(str(rp.get("unRealizedProfit", "0")))
            lev = to_decimal(str(rp.get("leverage", "1")))
            mtype = str(rp.get("marginType", "cross")).upper()
            mmode = MarginMode.ISOLATED if mtype == "ISOLATED" else MarginMode.CROSS
            pos = Position(
                env=self.env,
                run_id=self.run_id,
                strategy_id=self.strategy_id,
                exchange=self.exchange,
                symbol=sym,
                position_side=PositionSide.NET,
                margin_mode=mmode,
                leverage=lev,
                quantity=qty,
                entry_price=entry_px,
                mark_price=mark_px,
                unrealized_pnl=upnl,
                updated_ts_ms=now,
            )
            self._positions[(self.exchange, sym)] = pos
            if self._ledger is not None:
                self._ledger.upsert_position(pos)
            synced_pos_count += 1

        # 3. Open Orders
        raw_open_orders = self.transport.fetch_open_orders_raw()
        for ro in raw_open_orders:
            ord_obj = self._parse_raw_order(ro, now_ms=now)
            self._orders[ord_obj.client_order_id] = ord_obj
            if self._ledger is not None:
                self._ledger.upsert_order(ord_obj)

        # 4. Recent Fills
        raw_trades = self.transport.fetch_my_trades_raw()
        for rt in raw_trades:
            fill_obj = self._parse_raw_trade(rt, now_ms=now)
            self._emit_fill(fill_obj)

        # 5. Funding Payments
        raw_funding = self.transport.fetch_funding_history_raw()
        for rf in raw_funding:
            pid = f"exfund_{rf['tranId']}"
            fp = FundingPayment(
                payment_id=pid,
                env=self.env,
                run_id=self.run_id,
                exchange=self.exchange,
                symbol=str(rf["symbol"]),
                funding_rate=to_decimal(str(rf.get("fundingRate", "0"))),
                mark_price=to_decimal(str(rf.get("markPrice", "0"))),
                position_qty=to_decimal(str(rf.get("positionAmt", "0"))),
                amount=to_decimal(str(rf["income"])),
                asset=str(rf.get("asset", "USDT")),
                settlement_ts_ms=int(rf["time"]),
                local_recv_ts_ms=now,
            )
            self._funding_payments.append(fp)
            if self._ledger is not None:
                existing_fp = self._ledger.session.execute(
                    select(FundingPaymentRecord).where(
                        FundingPaymentRecord.env == self.env.value,
                        FundingPaymentRecord.run_id == self.run_id,
                        FundingPaymentRecord.payment_id == pid,
                    )
                ).scalar_one_or_none()
                if existing_fp is None:
                    self._ledger.record_funding_payment(fp)

        self._ready = True
        return {
            "ready": True,
            "open_orders": len(raw_open_orders),
            "positions": synced_pos_count,
            "balances": len(raw_bals),
            "recent_fills": len(raw_trades),
            "funding_payments": len(raw_funding),
        }

    def _parse_raw_order(
        self,
        ro: dict[str, Any],
        *,
        now_ms: int,
        fallback_req: OrderRequest | None = None,
        spec_version: str = "",
    ) -> Order:
        orig_qty = to_decimal(str(ro.get("origQty", fallback_req.quantity if fallback_req else "0")))
        exec_qty = to_decimal(str(ro.get("executedQty", "0")))
        rem_qty = max(ZERO, orig_qty - exec_qty)
        raw_px = str(ro.get("price", "0"))
        px_dec = to_decimal(raw_px) if raw_px not in {"0", "None", ""} else None
        raw_stop = ro.get("stopPrice")
        stop_dec = to_decimal(str(raw_stop)) if raw_stop not in {None, "0", "None", ""} else None
        avg_px = to_decimal(str(ro.get("avgPrice", "0")))
        ex_ts = int(ro.get("updateTime", now_ms))
        return Order(
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=str(ro["clientOrderId"]),
            exchange_order_id=str(ro["orderId"]) if ro.get("orderId") is not None else None,
            exchange=self.exchange,
            symbol=str(ro["symbol"]),
            side=OrderSide(str(ro["side"]).upper()),
            position_side=PositionSide.NET,
            order_type=OrderType(str(ro["type"]).upper()),
            time_in_force=TimeInForce(str(ro.get("timeInForce", "GTC")).upper()),
            post_only=str(ro.get("timeInForce", "")).upper() == "GTX",
            reduce_only=bool(ro.get("reduceOnly", False)),
            price=px_dec,
            stop_price=stop_dec,
            quantity=orig_qty,
            filled_qty=exec_qty,
            avg_fill_price=avg_px,
            remaining_qty=rem_qty,
            leverage=fallback_req.leverage if fallback_req else ONE,
            margin_mode=fallback_req.margin_mode if fallback_req else MarginMode.CROSS,
            status=OrderStatus(str(ro["status"]).upper()),
            signal_id=fallback_req.signal_id if fallback_req else None,
            symbol_spec_version=spec_version,
            created_ts_ms=fallback_req.created_ts_ms if (fallback_req and fallback_req.created_ts_ms) else ex_ts,
            updated_ts_ms=ex_ts,
            exchange_ts_ms=ex_ts,
            local_recv_ts_ms=now_ms,
        )

    def _parse_raw_trade(self, rt: dict[str, Any], *, now_ms: int) -> Fill:
        tid = str(rt["id"])
        px = to_decimal(str(rt["price"]))
        qty = to_decimal(str(rt["qty"]))
        quote_qty = to_decimal(str(rt.get("quoteQty", str(px * qty))))
        fee = to_decimal(str(rt.get("commission", "0")))
        is_maker = bool(rt.get("maker", False))
        return Fill(
            fill_id=f"exfill_{tid}",
            env=self.env,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            client_order_id=str(rt.get("clientOrderId", "")),
            exchange_order_id=str(rt["orderId"]) if rt.get("orderId") is not None else None,
            exchange_trade_id=tid,
            exchange=self.exchange,
            symbol=str(rt["symbol"]),
            side=OrderSide(str(rt["side"]).upper()),
            liquidity=LiquidityRole.MAKER if is_maker else LiquidityRole.TAKER,
            price=px,
            quantity=qty,
            quote_qty=quote_qty,
            reference_price=px,
            slippage_bps=ZERO,
            fee=fee,
            fee_asset=str(rt.get("commissionAsset", "USDT")),
            realized_pnl=to_decimal(str(rt.get("realizedPnl", "0"))),
            exchange_ts_ms=int(rt.get("time", now_ms)),
            local_recv_ts_ms=now_ms,
        )

    def submit_order(self, request: OrderRequest) -> Order:
        if not self._ready:
            raise BrokerNotReadyError(self.exchange)
        now = self._clock.now_ms()
        spec = self._data_feed.get_symbol_spec(request.exchange, request.symbol, at_ts_ms=now)

        ref_px = request.price or request.stop_price
        if ref_px is None:
            pos = self._positions.get((request.exchange, request.symbol))
            ref_px = pos.mark_price if (pos and pos.mark_price > ZERO) else Decimal("50000")

        norm = normalize_and_validate_order(
            request,
            spec,
            reference_price=ref_px,
            auto_round=True,
            now_ms=now,
        )
        if norm.stop_price is not None:
            norm = norm.model_copy(update={"stop_price": round_to_step(norm.stop_price, spec.tick_size)})

        payload: dict[str, Any] = {
            "clientOrderId": norm.client_order_id,
            "symbol": norm.symbol,
            "side": norm.side.value,
            "type": norm.order_type.value,
            "timeInForce": norm.time_in_force.value,
            "quantity": str(norm.quantity),
            "price": str(norm.price) if norm.price is not None else None,
            "stopPrice": str(norm.stop_price) if norm.stop_price is not None else None,
            "reduceOnly": norm.reduce_only,
            "postOnly": norm.post_only,
            "leverage": str(norm.leverage),
        }
        raw_resp = self.transport.place_order_raw(payload)
        order = self._parse_raw_order(
            raw_resp,
            now_ms=now,
            fallback_req=norm,
            spec_version=spec.spec_version_id,
        )

        if order.filled_qty > ZERO:
            for rt in self.transport.fetch_my_trades_raw(symbol=norm.symbol):
                if str(rt.get("clientOrderId")) == norm.client_order_id:
                    self._emit_fill(self._parse_raw_trade(rt, now_ms=now))
            for rp in self.transport.fetch_positions_raw(symbol=norm.symbol):
                qty = to_decimal(str(rp.get("positionAmt", "0")))
                entry_px = to_decimal(str(rp.get("entryPrice", "0")))
                mark_px = to_decimal(str(rp.get("markPrice", str(entry_px))))
                pos_obj = Position(
                    env=self.env,
                    run_id=self.run_id,
                    strategy_id=self.strategy_id,
                    exchange=self.exchange,
                    symbol=norm.symbol,
                    margin_mode=norm.margin_mode,
                    leverage=norm.leverage,
                    quantity=qty,
                    entry_price=entry_px,
                    mark_price=mark_px,
                    updated_ts_ms=now,
                )
                self._positions[(self.exchange, norm.symbol)] = pos_obj
                if self._ledger is not None:
                    self._ledger.upsert_position(pos_obj)

        self._emit_order(order)
        return order

    def submit_protective_stop_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
    ) -> Order:
        """Place an exchange-native server-side `STOP_MARKET` `reduce_only=True` stop-loss order."""
        now = self._clock.now_ms()
        req = OrderRequest(
            client_order_id=client_order_id,
            exchange=self.exchange,
            symbol=symbol,
            side=side,
            order_type=OrderType.STOP_MARKET,
            time_in_force=TimeInForce.GTC,
            quantity=quantity,
            stop_price=stop_price,
            reduce_only=True,
            created_ts_ms=now,
        )
        return self.submit_order(req)

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        now = self._clock.now_ms()
        raw_resp = self.transport.cancel_order_raw(client_order_id=client_order_id, symbol=symbol)
        ord_obj = self._parse_raw_order(raw_resp, now_ms=now)
        self._emit_order(ord_obj)
        return ord_obj

    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        now = self._clock.now_ms()
        raw_resp = self.transport.fetch_order_raw(client_order_id=client_order_id, symbol=symbol)
        if raw_resp is None:
            return None
        ord_obj = self._parse_raw_order(raw_resp, now_ms=now)
        self._orders[client_order_id] = ord_obj
        return ord_obj

    def query_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[Order]:
        now = self._clock.now_ms()
        rows = self.transport.fetch_open_orders_raw(symbol=symbol)
        out = [self._parse_raw_order(r, now_ms=now) for r in rows]
        for o in out:
            self._orders[o.client_order_id] = o
        return out

    def query_positions(self, exchange: str | None = None, symbol: str | None = None) -> list[Position]:
        now = self._clock.now_ms()
        rows = self.transport.fetch_positions_raw(symbol=symbol)
        out: list[Position] = []
        for rp in rows:
            sym = str(rp["symbol"])
            pos = Position(
                env=self.env,
                run_id=self.run_id,
                strategy_id=self.strategy_id,
                exchange=self.exchange,
                symbol=sym,
                quantity=to_decimal(str(rp.get("positionAmt", "0"))),
                entry_price=to_decimal(str(rp.get("entryPrice", "0"))),
                mark_price=to_decimal(str(rp.get("markPrice", "0"))),
                unrealized_pnl=to_decimal(str(rp.get("unRealizedProfit", "0"))),
                leverage=to_decimal(str(rp.get("leverage", "1"))),
                updated_ts_ms=now,
            )
            self._positions[(self.exchange, sym)] = pos
            out.append(pos)
        return out

    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        now = self._clock.now_ms()
        rows = self.transport.fetch_balances_raw()
        out: list[Balance] = []
        for rb in rows:
            asset = str(rb["asset"])
            wallet = to_decimal(str(rb.get("walletBalance", rb.get("total", "0"))))
            avail = to_decimal(str(rb.get("availableBalance", rb.get("free", str(wallet)))))
            bal = Balance(
                env=self.env,
                run_id=self.run_id,
                exchange=self.exchange,
                asset=asset,
                total=wallet,
                available=avail,
                locked=max(ZERO, wallet - avail),
                ts_ms=now,
            )
            self._balances[asset] = bal
            out.append(bal)
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
            if (symbol is None or fp.symbol == symbol) and (since_ms is None or fp.settlement_ts_ms >= since_ms)
        ]
