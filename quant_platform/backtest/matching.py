"""OrderBook depth walker, parameterized slippage model, and limit queue fill estimator (P4 & P7)."""

from __future__ import annotations

from decimal import Decimal

from quant_platform.core.decimal_utils import BPS_DIVISOR, ONE, StrictDecimal, ZERO
from quant_platform.core.enums import OrderSide
from quant_platform.core.models import DomainModel, OrderBookLevel


class SlippageModelConfig(DomainModel):
    """Serializable, replayable matching & queue-position parameters shared by Backtest and Shadow."""

    extra_impact_bps: StrictDecimal = ZERO
    queue_ahead_ratio: StrictDecimal = Decimal("0.5")
    trade_participation_rate: StrictDecimal = ONE


def estimate_limit_fill_probability(
    *,
    queue_ahead_qty: Decimal,
    order_qty: Decimal,
    expected_touch_volume: Decimal,
) -> Decimal:
    """
    Estimate expected filled fraction in [0, 1] for a resting limit order given
    estimated volume ahead in queue and expected traded volume at the limit price.
    """
    if order_qty <= ZERO or expected_touch_volume <= queue_ahead_qty:
        return ZERO
    fillable = expected_touch_volume - queue_ahead_qty
    if fillable >= order_qty:
        return ONE
    return fillable / order_qty


def walk_orderbook_levels(
    *,
    side: OrderSide,
    quantity: Decimal,
    levels: list[OrderBookLevel],
    limit_price: Decimal | None = None,
    extra_impact_bps: Decimal = ZERO,
) -> tuple[list[tuple[Decimal, Decimal, Decimal, Decimal]], list[OrderBookLevel]]:
    """
    Walk order book depth (asks for BUY, bids for SELL) level by level.
    Returns:
      - slices: list of (fill_price, fill_qty, reference_price, slippage_bps)
      - remaining_levels: order book levels after liquidity consumption
    """
    if not levels or quantity <= ZERO:
        return [], list(levels)

    ref_price = levels[0].price
    remaining_order = quantity
    slices: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    remaining_levels: list[OrderBookLevel] = []

    impact_mult = (
        ONE + (extra_impact_bps / BPS_DIVISOR)
        if side == OrderSide.BUY
        else ONE - (extra_impact_bps / BPS_DIVISOR)
    )

    for idx, lvl in enumerate(levels):
        crossable = True
        if limit_price is not None:
            if side == OrderSide.BUY and lvl.price > limit_price:
                crossable = False
            elif side == OrderSide.SELL and lvl.price < limit_price:
                crossable = False

        if not crossable or remaining_order <= ZERO:
            remaining_levels.append(lvl)
            continue

        matched_qty = min(remaining_order, lvl.quantity)
        exec_price = lvl.price * impact_mult
        if side == OrderSide.BUY:
            slip_bps = ((exec_price - ref_price) / ref_price) * BPS_DIVISOR
        else:
            slip_bps = ((ref_price - exec_price) / ref_price) * BPS_DIVISOR

        slices.append((exec_price, matched_qty, ref_price, slip_bps))
        remaining_order -= matched_qty
        leftover_lvl_qty = lvl.quantity - matched_qty
        if leftover_lvl_qty > ZERO:
            remaining_levels.append(OrderBookLevel(price=lvl.price, quantity=leftover_lvl_qty))

    return slices, remaining_levels
