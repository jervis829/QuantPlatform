"""Canonical deterministic hashing for reproducibility (Red Line 6)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Any

from quant_platform import ENGINE_VERSION
from quant_platform.core.decimal_utils import format_decimal
from quant_platform.exceptions import FloatForbiddenError


def _canonical_default(obj: Any) -> Any:
    if isinstance(obj, float):
        raise FloatForbiddenError("canonical_json")
    if isinstance(obj, Decimal):
        return format_decimal(obj)
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Object of type {type(obj).__name__} is not canonically serializable")


def canonical_json(data: Any) -> str:
    """Deterministic compact JSON serialization with sorted keys and exact Decimal strings."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_canonical_default,
    )


def sha256_hex(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def compute_run_id(
    *,
    strategy_code: str,
    config_snapshot: dict[str, Any],
    dataset_version: str,
    symbol_spec_version: str,
    engine_version: str = ENGINE_VERSION,
) -> str:
    """
    Red Line 6 deterministic run_id:
    run_id = sha256(strategy_code_hash + canonical(config_snapshot) + dataset_version + symbol_spec_version + engine_version)
    """
    code_hash = sha256_hex(strategy_code)
    payload = {
        "strategy_code_hash": code_hash,
        "config_snapshot": config_snapshot,
        "dataset_version": dataset_version,
        "symbol_spec_version": symbol_spec_version,
        "engine_version": engine_version,
    }
    digest = sha256_hex(canonical_json(payload))
    return f"run_{digest[:32]}"
