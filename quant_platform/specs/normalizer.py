"""Unified order normalization and SymbolSpec constraint validator (P2 / Red Line 9)."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

from quant_platform.core.decimal_utils import ZERO, is_exact_step_multiple, quantize_to_step
from quant_platform.core.enums import OrderType, TimeInForce
from quant_platform.core.interfaces import BrokerAdapter, Clock, FillCallback, OrderUpdateCallback
from quant_platform.core.models import (
    Balance,
    FundingPayment,
    MarginBracket,
    Order,
    OrderRequest,
    Position,
    SymbolSpec,
)
from quant_platform.exceptions import SymbolSpecViolationError


def resolve_margin_bracket(spec: SymbolSpec, notional: Decimal) -> MarginBracket | None:
    """Resolve active tiered maintenance margin bracket for a given position notional."""
    if not spec.margin_brackets:
        return None
    for b in spec.margin_brackets:
        if b.notional_floor <= notional <= b.notional_cap:
            return b
    return spec.margin_brackets[-1]


def normalize_and_validate_order(
    request: OrderRequest,
    spec: SymbolSpec,
    *,
    reference_price: Decimal | None = None,
    auto_round: bool = False,
    now_ms: int | None = None,
) -> OrderRequest:
    """
    Validate (and optionally quantize when converting raw Signals) an OrderRequest against versioned SymbolSpec.
    Structured rejection codes:
      - SPEC_SYMBOL_DELISTED
      - SPEC_CONTRACT_EXPIRED
      - SPEC_MISSING_PRICE
      - SPEC_PRICE_TICK_VIOLATION
      - SPEC_QTY_STEP_VIOLATION
      - SPEC_MIN_QTY_VIOLATION
      - SPEC_MAX_QTY_VIOLATION
      - SPEC_MIN_NOTIONAL_VIOLATION
      - SPEC_MAX_LEVERAGE_EXCEEDED
    """
    check_ts = now_ms if now_ms is not None else request.created_ts_ms
    if spec.delisted_at_ms is not None and check_ts >= spec.delisted_at_ms and not request.reduce_only:
        raise SymbolSpecViolationError(
            "SPEC_SYMBOL_DELISTED",
            f"Symbol {spec.exchange}:{spec.symbol} delisted at {spec.delisted_at_ms}",
            details={"delisted_at_ms": spec.delisted_at_ms, "ts_ms": check_ts},
        )
    if spec.settlement_ts_ms is not None and check_ts >= spec.settlement_ts_ms:
        raise SymbolSpecViolationError(
            "SPEC_CONTRACT_EXPIRED",
            f"Delivery contract {spec.exchange}:{spec.symbol} settled at {spec.settlement_ts_ms}",
            details={"settlement_ts_ms": spec.settlement_ts_ms, "ts_ms": check_ts},
        )

    price = request.price
    stop_price = request.stop_price
    qty = request.quantity
    tif = TimeInForce.GTX if request.post_only else request.time_in_force

    if request.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and price is None:
        raise SymbolSpecViolationError(
            "SPEC_MISSING_PRICE",
            f"{request.order_type.value} order requires explicit limit price",
        )

    if auto_round:
        if price is not None:
            price = quantize_to_step(price, spec.tick_size, rounding=ROUND_DOWN)
        if stop_price is not None:
            stop_price = quantize_to_step(stop_price, spec.tick_size, rounding=ROUND_DOWN)
        qty = quantize_to_step(qty, spec.step_size, rounding=ROUND_DOWN)
    else:
        if price is not None and not is_exact_step_multiple(price, spec.tick_size):
            raise SymbolSpecViolationError(
                "SPEC_PRICE_TICK_VIOLATION",
                f"Price {price} is not an exact multiple of tick_size {spec.tick_size}",
                details={"price": str(price), "tick_size": str(spec.tick_size)},
            )
        if stop_price is not None and not is_exact_step_multiple(stop_price, spec.tick_size):
            raise SymbolSpecViolationError(
                "SPEC_PRICE_TICK_VIOLATION",
                f"Stop price {stop_price} is not an exact multiple of tick_size {spec.tick_size}",
                details={"stop_price": str(stop_price), "tick_size": str(spec.tick_size)},
            )
        if not is_exact_step_multiple(qty, spec.step_size):
            raise SymbolSpecViolationError(
                "SPEC_QTY_STEP_VIOLATION",
                f"Quantity {qty} is not an exact multiple of step_size {spec.step_size}",
                details={"quantity": str(qty), "step_size": str(spec.step_size)},
            )

    if qty <= ZERO or qty < spec.min_qty:
        raise SymbolSpecViolationError(
            "SPEC_MIN_QTY_VIOLATION",
            f"Quantity {qty} < min_qty {spec.min_qty}",
            details={"quantity": str(qty), "min_qty": str(spec.min_qty)},
        )

    if qty > spec.max_qty:
        raise SymbolSpecViolationError(
            "SPEC_MAX_QTY_VIOLATION",
            f"Quantity {qty} > max_qty {spec.max_qty}",
            details={"quantity": str(qty), "max_qty": str(spec.max_qty)},
        )

    effective_price = price if price is not None else reference_price
    if effective_price is not None and not request.reduce_only:
        notional = effective_price * qty * spec.contract_size
        if notional < spec.min_notional:
            raise SymbolSpecViolationError(
                "SPEC_MIN_NOTIONAL_VIOLATION",
                f"Order notional {notional} < min_notional {spec.min_notional}",
                details={"notional": str(notional), "min_notional": str(spec.min_notional)},
            )
    else:
        notional = (effective_price or ZERO) * qty * spec.contract_size

    if request.leverage > spec.max_leverage:
        raise SymbolSpecViolationError(
            "SPEC_MAX_LEVERAGE_EXCEEDED",
            f"Requested leverage {request.leverage} > symbol max_leverage {spec.max_leverage}",
            details={"leverage": str(request.leverage), "max_leverage": str(spec.max_leverage)},
        )

    bracket = resolve_margin_bracket(spec, notional)
    if bracket is not None and request.leverage > bracket.max_leverage:
        raise SymbolSpecViolationError(
            "SPEC_MAX_LEVERAGE_EXCEEDED",
            f"Requested leverage {request.leverage} > bracket max_leverage {bracket.max_leverage}",
            details={
                "leverage": str(request.leverage),
                "bracket_max_leverage": str(bracket.max_leverage),
                "bracket": bracket.bracket,
            },
        )

    return request.model_copy(
        update={
            "price": price,
            "stop_price": stop_price,
            "quantity": qty,
            "time_in_force": tif,
        }
    )


class SpecLookupProtocol:
    def get_spec(self, exchange: str, symbol: str, at_ts_ms: int | None = None) -> SymbolSpec:
        raise NotImplementedError


class SpecEnforcedBroker(BrokerAdapter):
    """
    Mandatory BrokerAdapter gate enforcing historical/live SymbolSpec validation
    on EVERY order submission path and tagging Orders with symbol_spec_version (Red Line 9).
    """

    def __init__(
        self,
        *,
        inner: BrokerAdapter,
        spec_service: Any,
        clock: Clock,
        reference_price_fn: Callable[[str, str], Decimal | None] | None = None,
        auto_round: bool = False,
    ) -> None:
        self._inner = inner
        self._spec_service = spec_service
        self._clock = clock
        self._reference_price_fn = reference_price_fn
        self._auto_round = auto_round

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    def startup_sync(self) -> dict[str, Any]:
        return self._inner.startup_sync()

    def submit_order(self, request: OrderRequest) -> Order:
        now_ms = self._clock.now_ms()
        spec: SymbolSpec = self._spec_service.get_spec(request.exchange, request.symbol, at_ts_ms=now_ms)
        ref_price = self._reference_price_fn(request.exchange, request.symbol) if self._reference_price_fn else None
        validated = normalize_and_validate_order(
            request,
            spec,
            reference_price=ref_price,
            auto_round=self._auto_round,
            now_ms=now_ms,
        )
        order = self._inner.submit_order(validated)
        if not order.symbol_spec_version:
            order = order.model_copy(update={"symbol_spec_version": spec.spec_version_id})
        return order

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        return self._inner.cancel_order(client_order_id, exchange, symbol)

    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        return self._inner.query_order(client_order_id, exchange, symbol)

    def query_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[Order]:
        return self._inner.query_open_orders(exchange, symbol)

    def query_positions(self, exchange: str | None = None, symbol: str | None = None) -> list[Position]:
        return self._inner.query_positions(exchange, symbol)

    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        return self._inner.query_balance(exchange)

    def query_funding_payments(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        since_ms: int | None = None,
    ) -> list[FundingPayment]:
        return self._inner.query_funding_payments(exchange, symbol, since_ms)

    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        self._inner.register_order_callback(callback)

    def register_fill_callback(self, callback: FillCallback) -> None:
        self._inner.register_fill_callback(callback)
