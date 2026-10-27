"""Content-addressed dataset versioning feeding into deterministic run_id (P1 / Red Line 6)."""

from __future__ import annotations

from typing import Any

from quant_platform.core.hashing import canonical_json, sha256_hex


def compute_dataset_version(source: str, slices: list[dict[str, Any]]) -> str:
    """
    Generate a deterministic content hash over all loaded dataset slices
    (exchange + symbol + kind + interval + range + source + content digest).
    """
    canonical_slices = sorted(
        slices,
        key=lambda s: (
            str(s.get("exchange", "")),
            str(s.get("symbol", "")),
            str(s.get("kind", "")),
            str(s.get("interval", "")),
        ),
    )
    digest = sha256_hex(canonical_json({"source": source, "slices": canonical_slices}))
    return f"ds_{digest[:28]}"
