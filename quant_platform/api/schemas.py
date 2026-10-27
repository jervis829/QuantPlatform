"""Typed Pydantic request & response schemas for FastAPI REST & OpenAPI TypeScript generation (P11)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    engine_version: str
    maintenance_mode: bool


class StrategyUploadRequest(BaseModel):
    strategy_id: str
    name: str
    description: str = ""
    code: str
    params: dict[str, Any] | None = None


class NLStrategyGenerateRequest(BaseModel):
    strategy_id: str
    name: str
    prompt: str
    description: str = ""


class StrategyParamUpdateRequest(BaseModel):
    params: dict[str, Any]


class StrategyApprovalRequest(BaseModel):
    note: str = ""


class StrategyVersionResponse(BaseModel):
    version_id: str
    strategy_id: str
    version: int
    code: str
    code_hash: str
    param_schema: dict[str, Any]
    params: dict[str, Any]
    diff_text: str
    source_type: str
    generation_prompt: str | None = None
    backtest_gate: dict[str, Any] | None = None
    approval_status: str
    created_by: str
    approved_by: str | None = None
    approved_at_ms: int | None = None
    created_at_ms: int


class StrategySummaryItem(BaseModel):
    strategy_id: str
    name: str
    description: str
    current_version: int
    source_type: str
    created_by: str
    updated_at_ms: int


class StrategyDetailResponse(BaseModel):
    meta: StrategySummaryItem
    latest_version: StrategyVersionResponse
    versions: list[StrategyVersionResponse]
    runs: list[dict[str, Any]]


class LaunchRunRequest(BaseModel):
    strategy_id: str
    version_id: str
    env: str = Field(description="backtest | shadow | live")
    symbol: str = "BTC/USDT:USDT"
    exchange: str = "binance"
    initial_usdt: str = "10000"


class LaunchRunResponse(BaseModel):
    run_id: str
    env: str
    status: str
    strategy_id: str
    version_id: str


class LedgerListResponse(BaseModel):
    table: str
    env: str
    run_id: str
    items: list[dict[str, Any]]


class ReconciliationRunRequest(BaseModel):
    tier: str = Field(description="exchange_vs_local | shadow_vs_live | backtest_vs_shadow")
    run_id: str | None = None
    shadow_run_id: str | None = None
    live_run_id: str | None = None
    backtest_run_id: str | None = None
    auto_heal: bool = False


class KillSwitchRequest(BaseModel):
    close_positions: bool = True
    reason: str = "manual_ui"


class KillSwitchResponse(BaseModel):
    kill_switch_active: bool
    canceled_orders: int
    flattened_positions: int
    reason: str
    elapsed_ms: float
