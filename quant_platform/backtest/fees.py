"""Unified Maker/Taker fee calculator with VIP tiers, platform token discount, and Spot/Contract asset rules (P4 / Red Line 5)."""

from __future__ import annotations

from decimal import Decimal

from quant_platform.core.decimal_utils import ONE, ZERO
from quant_platform.core.enums import ContractType, LiquidityRole, OrderSide
from quant_platform.core.models import FeeTier, SymbolSpec


def resolve_fee_tier(spec: SymbolSpec, vip_level: int = 0) -> FeeTier:
    if spec.fee_tiers:
        for tier in spec.fee_tiers:
            if tier.vip_level == vip_level:
                return tier
        sorted_tiers = sorted(spec.fee_tiers, key=lambda t: t.vip_level)
        eligible = [t for t in sorted_tiers if t.vip_level <= vip_level]
        if eligible:
            return eligible[-1]
        return sorted_tiers[0]
    return FeeTier(vip_level=vip_level, maker_fee=spec.maker_fee, taker_fee=spec.taker_fee, platform_token_discount=ZERO)


def compute_fill_fee(
    *,
    spec: SymbolSpec,
    liquidity: LiquidityRole,
    side: OrderSide,
    fill_price: Decimal,
    fill_qty: Decimal,
    vip_level: int = 0,
    use_platform_token_discount: bool = False,
) -> tuple[Decimal, str]:
    """
    Compute (fee_amount, fee_asset) matching exchange deduction conventions:
    - PERPETUAL / DELIVERY: fee charged in spec.margin_asset on notional = fill_price * fill_qty * contract_size.
    - SPOT:
      * BUY: fee charged in spec.base_asset (fill_qty * rate) unless platform token discount converts to quote/platform fee;
      * SELL: fee charged in spec.quote_asset (fill_price * fill_qty * rate).
    """
    tier = resolve_fee_tier(spec, vip_level=vip_level)
    base_rate = tier.maker_fee if liquidity == LiquidityRole.MAKER else tier.taker_fee
    if use_platform_token_discount and tier.platform_token_discount > ZERO:
        rate = base_rate * (ONE - tier.platform_token_discount)
    else:
        rate = base_rate

    if spec.contract_type in {ContractType.PERPETUAL, ContractType.DELIVERY}:
        notional = fill_price * fill_qty * spec.contract_size
        return notional * rate, spec.margin_asset

    # SPOT fee deduction rule (Binance standard)
    if side == OrderSide.BUY and not use_platform_token_discount:
        return fill_qty * rate, spec.base_asset
    quote_notional = fill_price * fill_qty
    return quote_notional * rate, spec.quote_asset
