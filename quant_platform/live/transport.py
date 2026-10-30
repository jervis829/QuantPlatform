"""Raw-string exchange transport layer (`ccxt` wrapper + deterministic Testnet/SIGKILL simulator) (P8 / Red Line 4)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from quant_platform.core.decimal_utils import ZERO, to_strict_decimal
from quant_platform.core.interfaces import Clock


class ExchangeTransportClient(ABC):
    """
    Raw transport interface returning exchange-authoritative string-encoded payloads.
    ExchangeBroker parses strings directly into Decimal and enforces SymbolSpec explicitly.
    """

    @abstractmethod
    def place_order_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def fetch_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def fetch_open_orders_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_positions_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_balances_raw(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_my_trades_raw(self, symbol: str | None = None, since_ms: int | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_funding_history_raw(
        self, symbol: str | None = None, since_ms: int | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class CcxtExchangeTransport(ExchangeTransportClient):
    """
    `ccxt` transport adapter (Red Line 4: transport-only!).
    Sends explicit string-formatted quantities/prices and reads raw exchange `info` strings
    without relying on ccxt's float fields. Defaults to sandbox/testnet mode.
    """

    def __init__(
        self,
        *,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
        is_testnet: bool = True,
        default_type: str = "future",
    ) -> None:
        import ccxt

        cls = getattr(ccxt, exchange_id)
        cfg: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": default_type},
        }
        if passphrase:
            cfg["password"] = passphrase
        self._client = cls(cfg)
        if is_testnet:
            self._client.set_sandbox_mode(True)

    def place_order_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {"newClientOrderId": payload["clientOrderId"]}
        if payload.get("timeInForce"):
            params["timeInForce"] = payload["timeInForce"]
        if payload.get("reduceOnly"):
            params["reduceOnly"] = True
        if payload.get("stopPrice") is not None:
            params["stopPrice"] = str(payload["stopPrice"])
        if payload.get("postOnly"):
            params["timeInForce"] = "GTX"
        res = self._client.create_order(
            symbol=payload["symbol"],
            type=payload["type"].lower(),
            side=payload["side"].lower(),
            amount=str(payload["quantity"]),
            price=str(payload["price"]) if payload.get("price") is not None else None,
            params=params,
        )
        return dict(res.get("info") or res)

    def cancel_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any]:
        res = self._client.cancel_order(
            id=None,
            symbol=symbol,
            params={"origClientOrderId": client_order_id},
        )
        return dict(res.get("info") or res)

    def fetch_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any] | None:
        res = self._client.fetch_order(
            id=None,
            symbol=symbol,
            params={"origClientOrderId": client_order_id},
        )
        return dict(res.get("info") or res) if res else None

    def fetch_open_orders_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        rows = self._client.fetch_open_orders(symbol=symbol)
        return [dict(r.get("info") or r) for r in rows]

    def fetch_positions_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        symbols = [symbol] if symbol else None
        rows = self._client.fetch_positions(symbols=symbols)
        return [dict(r.get("info") or r) for r in rows]

    def fetch_balances_raw(self) -> list[dict[str, Any]]:
        bal = self._client.fetch_balance()
        info = bal.get("info", {})
        assets = info.get("assets") or info.get("balances") or []
        return [dict(a) for a in assets]

    def fetch_my_trades_raw(self, symbol: str | None = None, since_ms: int | None = None) -> list[dict[str, Any]]:
        rows = self._client.fetch_my_trades(symbol=symbol, since=since_ms)
        return [dict(r.get("info") or r) for r in rows]

    def fetch_funding_history_raw(
        self, symbol: str | None = None, since_ms: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self._client.fetch_funding_history(symbol=symbol, since=since_ms)
        return [dict(r.get("info") or r) for r in rows]


class MockRawExchangeTransport(ExchangeTransportClient):
    """
    Deterministic raw-string exchange simulator (Binance schema) with optional atomic JSON file persistence
    for Testnet integration and real `SIGKILL` crash-recovery tests.
    """

    def __init__(
        self,
        *,
        exchange: str = "binance",
        clock: Clock,
        default_mark_price: str = "50000.0",
        initial_balances: dict[str, str] | None = None,
        initial_positions: list[dict[str, Any]] | None = None,
        initial_open_orders: list[dict[str, Any]] | None = None,
        initial_fills: list[dict[str, Any]] | None = None,
        initial_funding: list[dict[str, Any]] | None = None,
        state_file: Path | None = None,
    ) -> None:
        self.exchange = exchange
        self.clock = clock
        self.default_mark_price = default_mark_price
        self.state_file = state_file
        self._seq = 10_000
        self._balances: dict[str, dict[str, str]] = {
            asset: {"asset": asset, "walletBalance": amt, "availableBalance": amt}
            for asset, amt in (initial_balances or {"USDT": "10000"}).items()
        }
        self._positions: dict[str, dict[str, Any]] = {
            p["symbol"]: dict(p) for p in (initial_positions or [])
        }
        self._orders: dict[str, dict[str, Any]] = {
            o["clientOrderId"]: dict(o) for o in (initial_open_orders or [])
        }
        self._fills: list[dict[str, Any]] = [dict(f) for f in (initial_fills or [])]
        self._funding: list[dict[str, Any]] = [dict(fp) for fp in (initial_funding or [])]
        self._flush_state()

    @classmethod
    def from_state_file(cls, *, exchange: str, clock: Clock, state_file: Path) -> "MockRawExchangeTransport":
        data = json.loads(state_file.read_text(encoding="utf-8"))
        inst = cls(
            exchange=exchange,
            clock=clock,
            initial_balances={},
            state_file=state_file,
        )
        inst._seq = int(data.get("seq", 10_000))
        inst._balances = dict(data["balances"])
        inst._positions = dict(data["positions"])
        inst._orders = dict(data["orders"])
        inst._fills = list(data["fills"])
        inst._funding = list(data["funding"])
        return inst

    def _flush_state(self) -> None:
        if self.state_file is None:
            return
        open_orders = [o for o in self._orders.values() if o["status"] in {"NEW", "PARTIALLY_FILLED"}]
        data = {
            "seq": self._seq,
            "balances": self._balances,
            "positions": self._positions,
            "orders": self._orders,
            "open_orders": open_orders,
            "fills": self._fills,
            "funding": self._funding,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def place_order_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        oid = str(self._seq)
        cid = payload["clientOrderId"]
        sym = payload["symbol"]
        side = payload["side"]
        otype = payload["type"]
        qty = to_strict_decimal(payload["quantity"])
        now = self.clock.now_ms()
        px_str = str(payload["price"]) if payload.get("price") is not None else self.default_mark_price

        if otype == "MARKET":
            exec_px = to_strict_decimal(self.default_mark_price)
            quote_qty = qty * exec_px
            fee = quote_qty * Decimal("0.0004")
            order_raw = {
                "orderId": oid,
                "clientOrderId": cid,
                "symbol": sym,
                "side": side,
                "type": otype,
                "timeInForce": payload.get("timeInForce", "GTC"),
                "price": "0",
                "avgPrice": str(exec_px),
                "origQty": str(qty),
                "executedQty": str(qty),
                "cumQuote": str(quote_qty),
                "status": "FILLED",
                "reduceOnly": bool(payload.get("reduceOnly", False)),
                "updateTime": now,
            }
            self._orders[cid] = order_raw
            self._fills.append(
                {
                    "id": f"tr_{oid}",
                    "orderId": oid,
                    "clientOrderId": cid,
                    "symbol": sym,
                    "side": side,
                    "price": str(exec_px),
                    "qty": str(qty),
                    "quoteQty": str(quote_qty),
                    "commission": str(fee),
                    "commissionAsset": "USDT",
                    "maker": False,
                    "realizedPnl": "0",
                    "time": now,
                }
            )
            signed_delta = qty if side == "BUY" else -qty
            cur_pos = self._positions.get(
                sym,
                {
                    "symbol": sym,
                    "positionAmt": "0",
                    "entryPrice": str(exec_px),
                    "markPrice": str(exec_px),
                    "unRealizedProfit": "0",
                    "leverage": str(payload.get("leverage", "1")),
                    "marginType": "cross",
                },
            )
            new_qty = to_strict_decimal(cur_pos["positionAmt"]) + signed_delta
            cur_pos["positionAmt"] = str(new_qty)
            cur_pos["entryPrice"] = str(exec_px) if new_qty != ZERO else "0"
            cur_pos["markPrice"] = str(exec_px)
            self._positions[sym] = cur_pos
            self._flush_state()
            return dict(order_raw)

        # Resting LIMIT / STOP_MARKET / STOP_LIMIT order
        order_raw = {
            "orderId": oid,
            "clientOrderId": cid,
            "symbol": sym,
            "side": side,
            "type": otype,
            "timeInForce": payload.get("timeInForce", "GTC"),
            "price": px_str if payload.get("price") is not None else "0",
            "stopPrice": str(payload["stopPrice"]) if payload.get("stopPrice") is not None else None,
            "avgPrice": "0",
            "origQty": str(qty),
            "executedQty": "0",
            "status": "NEW",
            "reduceOnly": bool(payload.get("reduceOnly", False)),
            "updateTime": now,
        }
        self._orders[cid] = order_raw
        self._flush_state()
        return dict(order_raw)

    def cancel_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any]:
        ord_raw = self._orders[client_order_id]
        ord_raw["status"] = "CANCELED"
        ord_raw["updateTime"] = self.clock.now_ms()
        self._flush_state()
        return dict(ord_raw)

    def fetch_order_raw(self, *, client_order_id: str, symbol: str) -> dict[str, Any] | None:
        o = self._orders.get(client_order_id)
        return dict(o) if o else None

    def fetch_open_orders_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(o)
            for o in self._orders.values()
            if o["status"] in {"NEW", "PARTIALLY_FILLED"} and (symbol is None or o["symbol"] == symbol)
        ]

    def fetch_positions_raw(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(p)
            for sym, p in self._positions.items()
            if symbol is None or sym == symbol
        ]

    def fetch_balances_raw(self) -> list[dict[str, Any]]:
        return [dict(b) for b in self._balances.values()]

    def fetch_my_trades_raw(self, symbol: str | None = None, since_ms: int | None = None) -> list[dict[str, Any]]:
        return [
            dict(f)
            for f in self._fills
            if (symbol is None or f["symbol"] == symbol) and (since_ms is None or int(f["time"]) >= since_ms)
        ]

    def fetch_funding_history_raw(
        self, symbol: str | None = None, since_ms: int | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(fp)
            for fp in self._funding
            if (symbol is None or fp["symbol"] == symbol) and (since_ms is None or int(fp["time"]) >= since_ms)
        ]
