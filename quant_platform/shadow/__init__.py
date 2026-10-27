"""Shadow trading broker, simulation parameter store, live-key isolation, and trace inspector (P7)."""

from quant_platform.shadow.broker import ShadowBroker
from quant_platform.shadow.params import (
    ShadowIsolationError,
    ShadowSimulationParams,
    assert_no_live_credentials,
)
from quant_platform.shadow.trace import ShadowTraceInspector

__all__ = [
    "ShadowBroker",
    "ShadowIsolationError",
    "ShadowSimulationParams",
    "ShadowTraceInspector",
    "assert_no_live_credentials",
]
