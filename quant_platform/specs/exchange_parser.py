"""Explicit fixed-point exchange metadata parser (Binance Spot & USDT-M Futures) (P2 / Red Lines 4 & 9)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quant_platform.core.decimal_utils import ONE, ZERO, to_strict_decimal
from quant_platform.core.enums import ContractType
from quant_platform.core.models import FeeTier, MarginBracket, SymbolSpec


def parse_binance_raw_symbol_spec(
    *,
    exchange: str,
    unified_symbol: str,
    raw_symbol_info: dict[str, Any],
    raw_brackets: list[dict[str, Any]] | None = None,
    fee_tiers: list[FeeTier] | None = None,
    maker_fee: Decimal = Decimal("0.001"),
    taker_fee: Decimal = Decimal("0.001"),
    funding_interval_hours: int = 8,
    effective_from_ms: int = 0,
    version: int = 1,
) -> SymbolSpec:
    """
    Parse raw Binance Spot or USDT-M Perpetual/Delivery `exchangeInfo` symbol entry
    directly from string filter values into exact Decimal SymbolSpec (zero float usage).
    """
    raw_ct = str(raw_symbol_info.get("contractType", "")).upper()
    if raw_ct == "PERPETUAL":
        contract_type = ContractType.PERPETUAL
    elif raw_ct in {"CURRENT_QUARTER", "NEXT_QUARTER", "DELIVERY"}:
        contract_type = ContractType.DELIVERY
    else:
        contract_type = ContractType.SPOT

    base_asset = str(raw_symbol_info["baseAsset"])
    quote_asset = str(raw_symbol_info["quoteAsset"])
    margin_asset = str(raw_symbol_info.get("marginAsset") or quote_asset)
    contract_size = to_strict_decimal(str(raw_symbol_info.get("contractSize", "1")), "contractSize")

    tick_size = Decimal("0.01")
    step_size = Decimal("0.001")
    min_qty = Decimal("0.001")
    max_qty = Decimal("1000000000")
    min_notional = ZERO

    for flt in raw_symbol_info.get("filters", []):
        ftype = flt.get("filterType")
        if ftype == "PRICE_FILTER":
            tick_size = to_strict_decimal(str(flt["tickSize"]), "tickSize")
        elif ftype == "LOT_SIZE":
            step_size = to_strict_decimal(str(flt["stepSize"]), "stepSize")
            min_qty = to_strict_decimal(str(flt["minQty"]), "minQty")
            if "maxQty" in flt:
                max_qty = to_strict_decimal(str(flt["maxQty"]), "maxQty")
        elif ftype in {"MIN_NOTIONAL", "NOTIONAL"}:
            raw_notional = flt.get("notional") or flt.get("minNotional") or "0"
            min_notional = to_strict_decimal(str(raw_notional), "minNotional")

    brackets: list[MarginBracket] = []
    max_leverage = ONE if contract_type == ContractType.SPOT else Decimal("20")
    if raw_brackets:
        for b in raw_brackets:
            lev = to_strict_decimal(str(b["initialLeverage"]), "initialLeverage")
            brackets.append(
                MarginBracket(
                    bracket=int(b["bracket"]),
                    notional_floor=to_strict_decimal(str(b.get("notionalFloor", "0")), "notionalFloor"),
                    notional_cap=to_strict_decimal(str(b["notionalCap"]), "notionalCap"),
                    max_leverage=lev,
                    maint_margin_rate=to_strict_decimal(str(b["maintMarginRatio"]), "maintMarginRatio"),
                    maint_amount=to_strict_decimal(str(b.get("cum", "0")), "cum"),
                )
            )
        brackets.sort(key=lambda item: item.bracket)
        max_leverage = max(item.max_leverage for item in brackets)

    settlement_ts_ms: int | None = None
    if contract_type == ContractType.DELIVERY and "deliveryDate" in raw_symbol_info:
        settlement_ts_ms = int(raw_symbol_info["deliveryDate"])

    return SymbolSpec(
        exchange=exchange,
        symbol=unified_symbol,
        version=version,
        effective_from_ms=effective_from_ms,
        contract_type=contract_type,
        base_asset=base_asset,
        quote_asset=quote_asset,
        margin_asset=margin_asset,
        tick_size=tick_size,
        step_size=step_size,
        min_qty=min_qty,
        max_qty=max_qty,
        min_notional=min_notional,
        contract_size=contract_size,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        fee_tiers=fee_tiers or [FeeTier(vip_level=0, maker_fee=maker_fee, taker_fee=taker_fee)],
        max_leverage=max_leverage,
        margin_brackets=brackets,
        funding_interval_hours=funding_interval_hours,
        settlement_ts_ms=settlement_ts_ms,
    )
