"""Typed Pydantic request & response schemas for FastAPI REST & OpenAPI TypeScript generation (P11 + UI Redesign)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    engine_version: str
    maintenance_mode: bool


class RunningEnvCounts(BaseModel):
    backtest: int = 0
    shadow: int = 0
    live: int = 0


class StrategyBacktestBrief(BaseModel):
    run_id: str
    cumulative_return: float
    max_drawdown: float
    sharpe: float


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
    latest_approval_status: str = "PENDING"
    active_envs: list[str] = Field(default_factory=list)
    run_counts: RunningEnvCounts = Field(default_factory=RunningEnvCounts)
    latest_backtest_metrics: StrategyBacktestBrief | None = None
    live_today_pnl_usdt: str = "0"


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
    leverage: str = "3"
    margin_mode: str = "CROSS"


class LaunchRunResponse(BaseModel):
    run_id: str
    env: str
    status: str
    strategy_id: str
    version_id: str


class RunSummaryItem(BaseModel):
    run_id: str
    env: str
    strategy_id: str
    strategy_version_id: str
    status: str
    started_at_ms: int
    ended_at_ms: int | None = None
    cumulative_return: float | None = None
    max_drawdown: float | None = None
    sharpe: float | None = None
    fill_count: int = 0
    error_message: str | None = None


class RunStatusResponse(BaseModel):
    run_id: str
    env: str
    strategy_id: str
    version_id: str
    status: str
    stage: str
    processed_bars: int
    total_bars: int
    progress_pct: float
    elapsed_ms: int
    estimated_remaining_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class SymbolSpecSummary(BaseModel):
    exchange: str
    symbol: str
    contract_type: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    tick_size: str
    step_size: str
    min_qty: str
    max_qty: str
    min_notional: str
    max_leverage: str
    maker_fee: str
    taker_fee: str


class PreflightBlockingReason(BaseModel):
    code: str
    message: str
    remediation: str


class PreflightAccountCheck(BaseModel):
    is_testnet: bool
    available_balance_usdt: str
    api_key_no_withdraw: bool
    ip_whitelist_verified: bool


class PreflightMarginRiskEstimate(BaseModel):
    estimated_initial_margin_usdt: str
    configured_leverage: str
    symbol_max_leverage: str
    risk_max_total_leverage: str
    max_order_notional_limit_usdt: str
    max_daily_loss_limit_usdt: str
    estimated_liq_distance_pct: str
    current_clock_skew_ms: int


class RunPreflightRequest(BaseModel):
    strategy_id: str
    version_id: str
    env: str = Field(description="backtest | shadow | live")
    exchange: str = "binance"
    symbol: str = "BTC/USDT:USDT"
    initial_usdt: str = "10000"
    leverage: str = "3"
    margin_mode: str = "CROSS"


class RunPreflightResponse(BaseModel):
    passed: bool
    blocking_reasons: list[PreflightBlockingReason]
    approval_status: str
    approved_by: str | None = None
    account_check: PreflightAccountCheck
    margin_and_risk_estimate: PreflightMarginRiskEstimate
    symbol_spec: SymbolSpecSummary


class ChartBarPoint(BaseModel):
    time_sec: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class ChartTradeMarker(BaseModel):
    time_sec: int
    side: str
    price: str
    qty: str
    client_order_id: str
    fill_id: str
    realized_pnl: str
    fee: str
    liquidity: str


class ChartEquityPoint(BaseModel):
    time_sec: int
    equity: float
    benchmark_equity: float
    drawdown_pct: float


class RunChartDataResponse(BaseModel):
    run_id: str
    env: str
    exchange: str
    symbol: str
    symbol_spec: SymbolSpecSummary
    bars: list[ChartBarPoint]
    trade_markers: list[ChartTradeMarker]
    equity_series: list[ChartEquityPoint]


class SensitivityRequest(BaseModel):
    param_x_name: str
    param_x_values: list[Any]
    param_y_name: str
    param_y_values: list[Any]


class SensitivityCellItem(BaseModel):
    x: Any
    y: Any
    cumulative_return: float
    sharpe: float
    max_drawdown: float
    run_id: str


class SensitivityResponse(BaseModel):
    param_x_name: str
    param_y_name: str
    x_values: list[Any]
    y_values: list[Any]
    cells: list[SensitivityCellItem]


class WalkForwardRequest(BaseModel):
    param_candidates: list[dict[str, Any]]
    train_size: int = 15
    test_size: int = 8


class WalkForwardWindowItem(BaseModel):
    window_index: int
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int
    best_params: dict[str, Any]
    in_sample_score: float
    oos_return: float
    oos_sharpe: float
    oos_max_drawdown: float
    oos_run_id: str


class WalkForwardResponse(BaseModel):
    window_count: int
    windows: list[WalkForwardWindowItem]


class LedgerListResponse(BaseModel):
    table: str
    env: str
    run_id: str
    items: list[dict[str, Any]]


class LedgerOrderRow(BaseModel):
    env: str
    run_id: str
    strategy_id: str
    client_order_id: str
    exchange_order_id: str | None = None
    exchange: str
    symbol: str
    side: str
    order_type: str
    time_in_force: str
    price: str | None = None
    stop_price: str | None = None
    quantity: str
    filled_qty: str
    avg_fill_price: str
    remaining_qty: str
    fee_total: str
    fee_asset: str
    leverage: str
    margin_mode: str
    status: str
    reject_reason: str | None = None
    fill_count: int
    realized_pnl_usdt: str
    created_ts_ms: int
    updated_ts_ms: int


class LedgerQuerySummary(BaseModel):
    order_count: int
    filled_count: int
    rejected_count: int
    total_notional_usdt: str
    total_fee_usdt: str
    realized_pnl_usdt: str


class LedgerOrdersQueryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    summary: LedgerQuerySummary
    symbol_specs: dict[str, SymbolSpecSummary]
    items: list[LedgerOrderRow]


class OrderFillsDrilldownResponse(BaseModel):
    client_order_id: str
    env: str
    run_id: str
    fills: list[dict[str, Any]]
    signal_traces: list[dict[str, Any]]


class ReconciliationRunRequest(BaseModel):
    tier: str = Field(description="exchange_vs_local | shadow_vs_live | backtest_vs_shadow")
    run_id: str | None = None
    shadow_run_id: str | None = None
    live_run_id: str | None = None
    backtest_run_id: str | None = None
    auto_heal: bool = False


class ReconciliationSeverityCounts(BaseModel):
    CRITICAL: int = 0
    HIGH: int = 0
    MEDIUM: int = 0
    LOW: int = 0


class ReconciliationDetailResponse(BaseModel):
    report_id: str
    tier: str
    env: str
    run_id: str
    reference_run_id: str | None = None
    passed: bool
    discrepancy_count: int
    max_severity: str
    severity_counts: ReconciliationSeverityCounts
    total_abs_drift_usdt: str
    summary: dict[str, Any]
    items: list[dict[str, Any]]
    created_ts_ms: int


class ReconciliationGlobalSummary(BaseModel):
    unresolved_count: int
    critical_or_high_count: int
    total_abs_drift_usdt: str
    last_check_ts_ms: int | None = None


class ExchangeConnectionItem(BaseModel):
    exchange: str
    channel: str
    healthy: bool
    last_heartbeat_ms: int | None = None
    clock_skew_ms: int


class SystemOverviewResponse(BaseModel):
    running_counts: RunningEnvCounts
    completed_counts: RunningEnvCounts
    today_pnl_usdt: str
    today_return_pct: float
    yesterday_pnl_usdt: str
    unacknowledged_alert_count: int
    critical_alert_count: int
    reconciliation_summary: ReconciliationGlobalSummary
    exchange_connections: list[ExchangeConnectionItem]
    kill_switch_active: bool
    maintenance_mode: bool
    server_ts_ms: int


class RateLimitStatusItem(BaseModel):
    exchange: str
    used_weight: int
    max_weight: int
    backoff_until_ms: int
    recent_429_count: int


class SandboxExecutionStatItem(BaseModel):
    ok: bool
    reason_code: str
    elapsed_ms: int


class KillSwitchHistoryItem(BaseModel):
    id: int
    actor: str
    action: str
    reason: str
    canceled_orders: int
    flattened_positions: int
    elapsed_ms: float
    entry_hash: str
    prev_hash: str
    ts_ms: int


class SystemDiagnosticsResponse(BaseModel):
    connections: list[ExchangeConnectionItem]
    rate_limits: list[RateLimitStatusItem]
    sandbox_stats: list[SandboxExecutionStatItem]
    sandbox_total_runs: int
    sandbox_blocked_runs: int
    kill_switch_history: list[KillSwitchHistoryItem]
    audit_chain_valid: bool
    maintenance_mode: bool


class MaintenanceModeRequest(BaseModel):
    enabled: bool
    reason: str = ""


class RiskEventItem(BaseModel):
    event_id: str
    env: str
    run_id: str
    strategy_id: str
    rule_code: str
    severity: str
    action: str
    symbol: str | None = None
    client_order_id: str | None = None
    reason: str
    acknowledged: bool = False
    acked_by: str | None = None
    acked_at_ms: int | None = None
    ts_ms: int


class RiskEventListResponse(BaseModel):
    items: list[RiskEventItem]
    unacknowledged_count: int


class RiskEventAckResponse(BaseModel):
    event_id: str
    acknowledged: bool
    acked_by: str
    acked_at_ms: int


class KillSwitchRequest(BaseModel):
    close_positions: bool = True
    reason: str = "manual_ui"


class KillSwitchResponse(BaseModel):
    kill_switch_active: bool
    canceled_orders: int
    flattened_positions: int
    reason: str
    elapsed_ms: float


class KillSwitchOrderPreview(BaseModel):
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    price: str
    remaining_qty: str
    notional_usdt: str


class KillSwitchPositionPreview(BaseModel):
    symbol: str
    position_side: str
    quantity: str
    entry_price: str
    mark_price: str
    notional_usdt: str
    unrealized_pnl_usdt: str


class KillSwitchPreviewResponse(BaseModel):
    active_live_run_id: str | None = None
    open_orders_count: int
    open_orders_notional_usdt: str
    open_orders: list[KillSwitchOrderPreview]
    open_positions_count: int
    gross_position_notional_usdt: str
    unrealized_pnl_usdt: str
    positions: list[KillSwitchPositionPreview]


class GlobalSearchHit(BaseModel):
    kind: str = Field(description="strategy | run | order | nav")
    id: str
    title: str
    subtitle: str
    env: str | None = None
    href: str


class GlobalSearchResponse(BaseModel):
    query: str
    hits: list[GlobalSearchHit]
