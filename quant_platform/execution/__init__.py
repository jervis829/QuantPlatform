"""Unified execution pipeline, order state machine, idempotency, rate limiter, and isomorphic runners (P6)."""

from quant_platform.execution.coordinator import ExecutionCoordinator, detect_state_drift
from quant_platform.execution.id_mapping import OrderIdMapper, generate_client_order_id
from quant_platform.execution.rate_limiter import WeightRateLimiter
from quant_platform.execution.runners import (
    BacktestRunner,
    LiveRunner,
    MarketEventTuple,
    ShadowRunner,
    UnifiedExecutionPipeline,
)
from quant_platform.execution.state_machine import (
    LEGAL_TRANSITIONS,
    STATUS_PROGRESSION_RANK,
    TERMINAL_ORDER_STATUSES,
    OrderStateMachine,
)

__all__ = [
    "BacktestRunner",
    "ExecutionCoordinator",
    "LEGAL_TRANSITIONS",
    "LiveRunner",
    "MarketEventTuple",
    "OrderIdMapper",
    "OrderStateMachine",
    "STATUS_PROGRESSION_RANK",
    "ShadowRunner",
    "TERMINAL_ORDER_STATUSES",
    "UnifiedExecutionPipeline",
    "WeightRateLimiter",
    "detect_state_drift",
    "generate_client_order_id",
]
