"""Contract margin, tiered MMR, Mark-Price liquidation, ADL ranking, and funding rate settlement (P4)."""

from __future__ import annotations

from decimal import Decimal

from quant_platform.core.decimal_utils import ONE, ZERO
from quant_platform.core.enums import ContractType
from quant_platform.core.models import SymbolSpec
from quant_platform.specs.normalizer import resolve_margin_bracket


def compute_maintenance_margin(spec: SymbolSpec, notional: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Return (maintenance_margin, mmr, maint_amount) for a position notional."""
    bracket = resolve_margin_bracket(spec, notional)
    if bracket is None:
        mmr = Decimal("0.005")
        maint_amt = ZERO
    else:
        mmr = bracket.maint_margin_rate
        maint_amt = bracket.maint_amount
    mm = max(ZERO, notional * mmr - maint_amt)
    return mm, mmr, maint_amt


def compute_unrealized_pnl(
    *,
    position_qty: Decimal,
    entry_price: Decimal,
    mark_price: Decimal,
    contract_size: Decimal,
) -> Decimal:
    """Unrealized PnL strictly marked to Mark Price (never Last Trade Price)."""
    if position_qty == ZERO:
        return ZERO
    return position_qty * (mark_price - entry_price) * contract_size


def compute_liquidation_price(
    *,
    spec: SymbolSpec,
    position_qty: Decimal,
    entry_price: Decimal,
    margin_backing: Decimal,
) -> Decimal | None:
    """
    Analytical Liquidation Mark Price solving:
      margin_backing + position_qty * (P_liq - entry_price) * contract_size
        == |position_qty| * P_liq * contract_size * mmr - maint_amount
    """
    if spec.contract_type == ContractType.SPOT or position_qty == ZERO:
        return None
    abs_qty = abs(position_qty)
    c = spec.contract_size
    entry_notional = abs_qty * entry_price * c
    _, mmr, maint_amt = compute_maintenance_margin(spec, entry_notional)

    if position_qty > ZERO:
        denom = abs_qty * c * (ONE - mmr)
        if denom <= ZERO:
            return None
        p_liq = (entry_notional - margin_backing - maint_amt) / denom
    else:
        denom = abs_qty * c * (ONE + mmr)
        if denom <= ZERO:
            return None
        p_liq = (entry_notional + margin_backing + maint_amt) / denom

    return p_liq if p_liq > ZERO else ZERO


def should_liquidate_at_mark_price(
    *,
    spec: SymbolSpec,
    position_qty: Decimal,
    entry_price: Decimal,
    mark_price: Decimal,
    margin_backing: Decimal,
) -> bool:
    """
    Strict Mark-Price liquidation condition:
      margin_backing + UPnL(mark_price) <= MaintenanceMargin(mark_price)
    """
    if spec.contract_type == ContractType.SPOT or position_qty == ZERO:
        return False
    abs_qty = abs(position_qty)
    notional_mark = abs_qty * mark_price * spec.contract_size
    mm, _, _ = compute_maintenance_margin(spec, notional_mark)
    upnl = compute_unrealized_pnl(
        position_qty=position_qty,
        entry_price=entry_price,
        mark_price=mark_price,
        contract_size=spec.contract_size,
    )
    return (margin_backing + upnl) <= mm


def compute_funding_payment_amount(
    *,
    position_qty: Decimal,
    mark_price: Decimal,
    contract_size: Decimal,
    funding_rate: Decimal,
) -> Decimal:
    """
    Standard perpetual funding cashflow:
      amount = - position_qty * mark_price * contract_size * funding_rate
    Positive amount = credited to holder; Negative amount = debited from holder.
    """
    return -(position_qty * mark_price * contract_size * funding_rate)


def compute_adl_AutoDeleverage_rank(
    *,
    unrealized_pnl: Decimal,
    initial_margin: Decimal,
    leverage: Decimal,
) -> int:
    """
    Compute exchange Auto-Deleveraging (ADL) priority quantile in {1, 2, 3, 4, 5}
    from PnL ratio * effective leverage. Profitable high-leverage positions rank 5.
    """
    if initial_margin <= ZERO or unrealized_pnl <= ZERO:
        return 1
    pnl_ratio = unrealized_pnl / initial_margin
    score = pnl_ratio * leverage
    if score >= Decimal("8"):
        return 5
    if score >= Decimal("5"):
        return 4
    if score >= Decimal("2"):
        return 3
    if score >= Decimal("0.5"):
        return 2
    return 1
