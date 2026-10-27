"""SymbolSpec service, raw exchange metadata parser, and mandatory order normalizer (P2)."""

from quant_platform.specs.exchange_parser import parse_binance_raw_symbol_spec
from quant_platform.specs.normalizer import (
    SpecEnforcedBroker,
    normalize_and_validate_order,
    resolve_margin_bracket,
)
from quant_platform.specs.service import SymbolSpecService

__all__ = [
    "SpecEnforcedBroker",
    "SymbolSpecService",
    "normalize_and_validate_order",
    "parse_binance_raw_symbol_spec",
    "resolve_margin_bracket",
]
