"""Shadow trading simulation parameters and live-credential physical isolation guard (P7)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from quant_platform.core.decimal_utils import StrictDecimal, ZERO
from quant_platform.exceptions import QuantPlatformError


class ShadowIsolationError(QuantPlatformError):
    """Raised when live trading API keys or private credentials are passed into Shadow mode."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Shadow isolation violation: {reason}", code="SHADOW_ISOLATION_VIOLATION")


class ShadowSimulationParams(BaseModel):
    """Persisted simulation assumptions for ShadowBroker (queue position, trade participation, latency, fees)."""

    model_config = ConfigDict(extra="forbid")

    queue_ahead_ratio: StrictDecimal = Decimal("0.5")
    trade_participation_rate: StrictDecimal = Decimal("0.5")
    extra_impact_bps: StrictDecimal = ZERO
    latency_ms: int = 25
    vip_level: int = 0
    use_platform_token_discount: bool = False


def assert_no_live_credentials(credentials: dict[str, Any] | None) -> None:
    if not credentials:
        return
    forbidden_keys = {"api_key", "api_secret", "secret", "private_key", "passphrase", "withdraw_key"}
    leaked = [k for k, v in credentials.items() if k.lower() in forbidden_keys and bool(v)]
    if leaked:
        raise ShadowIsolationError(f"Live trading credentials forbidden in Shadow environment: {leaked}")
