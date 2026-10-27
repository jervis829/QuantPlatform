/* AUTO-GENERATED FROM FastAPI OpenAPI Schema — DO NOT EDIT MANUALLY */

export interface ChartBarPoint {
  time_sec: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ChartEquityPoint {
  time_sec: number;
  equity: number;
  benchmark_equity: number;
  drawdown_pct: number;
}

export interface ChartTradeMarker {
  time_sec: number;
  side: string;
  price: string;
  qty: string;
  client_order_id: string;
  fill_id: string;
  realized_pnl: string;
  fee: string;
  liquidity: string;
}

export interface ExchangeConnectionItem {
  exchange: string;
  channel: string;
  healthy: boolean;
  last_heartbeat_ms?: number | null;
  clock_skew_ms: number;
}

export interface GlobalSearchHit {
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  env?: string | null;
  href: string;
}

export interface GlobalSearchResponse {
  query: string;
  hits: Array<GlobalSearchHit>;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  status: string;
  engine_version: string;
  maintenance_mode: boolean;
}

export interface KillSwitchHistoryItem {
  id: number;
  actor: string;
  action: string;
  reason: string;
  canceled_orders: number;
  flattened_positions: number;
  elapsed_ms: number;
  entry_hash: string;
  prev_hash: string;
  ts_ms: number;
}

export interface KillSwitchOrderPreview {
  client_order_id: string;
  symbol: string;
  side: string;
  order_type: string;
  price: string;
  remaining_qty: string;
  notional_usdt: string;
}

export interface KillSwitchPositionPreview {
  symbol: string;
  position_side: string;
  quantity: string;
  entry_price: string;
  mark_price: string;
  notional_usdt: string;
  unrealized_pnl_usdt: string;
}

export interface KillSwitchPreviewResponse {
  active_live_run_id?: string | null;
  open_orders_count: number;
  open_orders_notional_usdt: string;
  open_orders: Array<KillSwitchOrderPreview>;
  open_positions_count: number;
  gross_position_notional_usdt: string;
  unrealized_pnl_usdt: string;
  positions: Array<KillSwitchPositionPreview>;
}

export interface KillSwitchRequest {
  close_positions?: boolean;
  reason?: string;
}

export interface KillSwitchResponse {
  kill_switch_active: boolean;
  canceled_orders: number;
  flattened_positions: number;
  reason: string;
  elapsed_ms: number;
}

export interface LaunchRunRequest {
  strategy_id: string;
  version_id: string;
  env: string;
  symbol?: string;
  exchange?: string;
  initial_usdt?: string;
  leverage?: string;
  margin_mode?: string;
}

export interface LaunchRunResponse {
  run_id: string;
  env: string;
  status: string;
  strategy_id: string;
  version_id: string;
}

export interface LedgerListResponse {
  table: string;
  env: string;
  run_id: string;
  items: Array<Record<string, unknown>>;
}

export interface LedgerOrderRow {
  env: string;
  run_id: string;
  strategy_id: string;
  client_order_id: string;
  exchange_order_id?: string | null;
  exchange: string;
  symbol: string;
  side: string;
  order_type: string;
  time_in_force: string;
  price?: string | null;
  stop_price?: string | null;
  quantity: string;
  filled_qty: string;
  avg_fill_price: string;
  remaining_qty: string;
  fee_total: string;
  fee_asset: string;
  leverage: string;
  margin_mode: string;
  status: string;
  reject_reason?: string | null;
  fill_count: number;
  realized_pnl_usdt: string;
  created_ts_ms: number;
  updated_ts_ms: number;
}

export interface LedgerOrdersQueryResponse {
  total: number;
  limit: number;
  offset: number;
  summary: LedgerQuerySummary;
  symbol_specs: Record<string, SymbolSpecSummary>;
  items: Array<LedgerOrderRow>;
}

export interface LedgerQuerySummary {
  order_count: number;
  filled_count: number;
  rejected_count: number;
  total_notional_usdt: string;
  total_fee_usdt: string;
  realized_pnl_usdt: string;
}

export interface MaintenanceModeRequest {
  enabled: boolean;
  reason?: string;
}

export interface NLStrategyGenerateRequest {
  strategy_id: string;
  name: string;
  prompt: string;
  description?: string;
}

export interface OrderFillsDrilldownResponse {
  client_order_id: string;
  env: string;
  run_id: string;
  fills: Array<Record<string, unknown>>;
  signal_traces: Array<Record<string, unknown>>;
}

export interface PreflightAccountCheck {
  is_testnet: boolean;
  available_balance_usdt: string;
  api_key_no_withdraw: boolean;
  ip_whitelist_verified: boolean;
}

export interface PreflightBlockingReason {
  code: string;
  message: string;
  remediation: string;
}

export interface PreflightMarginRiskEstimate {
  estimated_initial_margin_usdt: string;
  configured_leverage: string;
  symbol_max_leverage: string;
  risk_max_total_leverage: string;
  max_order_notional_limit_usdt: string;
  max_daily_loss_limit_usdt: string;
  estimated_liq_distance_pct: string;
  current_clock_skew_ms: number;
}

export interface RateLimitStatusItem {
  exchange: string;
  used_weight: number;
  max_weight: number;
  backoff_until_ms: number;
  recent_429_count: number;
}

export interface ReconciliationDetailResponse {
  report_id: string;
  tier: string;
  env: string;
  run_id: string;
  reference_run_id?: string | null;
  passed: boolean;
  discrepancy_count: number;
  max_severity: string;
  severity_counts: ReconciliationSeverityCounts;
  total_abs_drift_usdt: string;
  summary: Record<string, unknown>;
  items: Array<Record<string, unknown>>;
  created_ts_ms: number;
}

export interface ReconciliationGlobalSummary {
  unresolved_count: number;
  critical_or_high_count: number;
  total_abs_drift_usdt: string;
  last_check_ts_ms?: number | null;
}

export interface ReconciliationRunRequest {
  tier: string;
  run_id?: string | null;
  shadow_run_id?: string | null;
  live_run_id?: string | null;
  backtest_run_id?: string | null;
  auto_heal?: boolean;
}

export interface ReconciliationSeverityCounts {
  CRITICAL?: number;
  HIGH?: number;
  MEDIUM?: number;
  LOW?: number;
}

export interface RiskEventAckResponse {
  event_id: string;
  acknowledged: boolean;
  acked_by: string;
  acked_at_ms: number;
}

export interface RiskEventItem {
  event_id: string;
  env: string;
  run_id: string;
  strategy_id: string;
  rule_code: string;
  severity: string;
  action: string;
  symbol?: string | null;
  client_order_id?: string | null;
  reason: string;
  acknowledged?: boolean;
  acked_by?: string | null;
  acked_at_ms?: number | null;
  ts_ms: number;
}

export interface RiskEventListResponse {
  items: Array<RiskEventItem>;
  unacknowledged_count: number;
}

export interface RunChartDataResponse {
  run_id: string;
  env: string;
  exchange: string;
  symbol: string;
  symbol_spec: SymbolSpecSummary;
  bars: Array<ChartBarPoint>;
  trade_markers: Array<ChartTradeMarker>;
  equity_series: Array<ChartEquityPoint>;
}

export interface RunPreflightRequest {
  strategy_id: string;
  version_id: string;
  env: string;
  exchange?: string;
  symbol?: string;
  initial_usdt?: string;
  leverage?: string;
  margin_mode?: string;
}

export interface RunPreflightResponse {
  passed: boolean;
  blocking_reasons: Array<PreflightBlockingReason>;
  approval_status: string;
  approved_by?: string | null;
  account_check: PreflightAccountCheck;
  margin_and_risk_estimate: PreflightMarginRiskEstimate;
  symbol_spec: SymbolSpecSummary;
}

export interface RunStatusResponse {
  run_id: string;
  env: string;
  strategy_id: string;
  version_id: string;
  status: string;
  stage: string;
  processed_bars: number;
  total_bars: number;
  progress_pct: number;
  elapsed_ms: number;
  estimated_remaining_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface RunSummaryItem {
  run_id: string;
  env: string;
  strategy_id: string;
  strategy_version_id: string;
  status: string;
  started_at_ms: number;
  ended_at_ms?: number | null;
  cumulative_return?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
  fill_count?: number;
  error_message?: string | null;
}

export interface RunningEnvCounts {
  backtest?: number;
  shadow?: number;
  live?: number;
}

export interface SandboxExecutionStatItem {
  ok: boolean;
  reason_code: string;
  elapsed_ms: number;
}

export interface SensitivityCellItem {
  x: unknown;
  y: unknown;
  cumulative_return: number;
  sharpe: number;
  max_drawdown: number;
  run_id: string;
}

export interface SensitivityRequest {
  param_x_name: string;
  param_x_values: Array<unknown>;
  param_y_name: string;
  param_y_values: Array<unknown>;
}

export interface SensitivityResponse {
  param_x_name: string;
  param_y_name: string;
  x_values: Array<unknown>;
  y_values: Array<unknown>;
  cells: Array<SensitivityCellItem>;
}

export interface StrategyApprovalRequest {
  note?: string;
}

export interface StrategyBacktestBrief {
  run_id: string;
  cumulative_return: number;
  max_drawdown: number;
  sharpe: number;
}

export interface StrategyDetailResponse {
  meta: StrategySummaryItem;
  latest_version: StrategyVersionResponse;
  versions: Array<StrategyVersionResponse>;
  runs: Array<Record<string, unknown>>;
}

export interface StrategyParamUpdateRequest {
  params: Record<string, unknown>;
}

export interface StrategySummaryItem {
  strategy_id: string;
  name: string;
  description: string;
  current_version: number;
  source_type: string;
  created_by: string;
  updated_at_ms: number;
  latest_approval_status?: string;
  active_envs?: Array<string>;
  run_counts?: RunningEnvCounts;
  latest_backtest_metrics?: StrategyBacktestBrief | null;
  live_today_pnl_usdt?: string;
}

export interface StrategyUploadRequest {
  strategy_id: string;
  name: string;
  description?: string;
  code: string;
  params?: Record<string, unknown> | null;
}

export interface StrategyVersionResponse {
  version_id: string;
  strategy_id: string;
  version: number;
  code: string;
  code_hash: string;
  param_schema: Record<string, unknown>;
  params: Record<string, unknown>;
  diff_text: string;
  source_type: string;
  generation_prompt?: string | null;
  backtest_gate?: Record<string, unknown> | null;
  approval_status: string;
  created_by: string;
  approved_by?: string | null;
  approved_at_ms?: number | null;
  created_at_ms: number;
}

export interface SymbolSpecSummary {
  exchange: string;
  symbol: string;
  contract_type: string;
  base_asset: string;
  quote_asset: string;
  margin_asset: string;
  tick_size: string;
  step_size: string;
  min_qty: string;
  max_qty: string;
  min_notional: string;
  max_leverage: string;
  maker_fee: string;
  taker_fee: string;
}

export interface SystemDiagnosticsResponse {
  connections: Array<ExchangeConnectionItem>;
  rate_limits: Array<RateLimitStatusItem>;
  sandbox_stats: Array<SandboxExecutionStatItem>;
  sandbox_total_runs: number;
  sandbox_blocked_runs: number;
  kill_switch_history: Array<KillSwitchHistoryItem>;
  audit_chain_valid: boolean;
  maintenance_mode: boolean;
}

export interface SystemOverviewResponse {
  running_counts: RunningEnvCounts;
  completed_counts: RunningEnvCounts;
  today_pnl_usdt: string;
  today_return_pct: number;
  yesterday_pnl_usdt: string;
  unacknowledged_alert_count: number;
  critical_alert_count: number;
  reconciliation_summary: ReconciliationGlobalSummary;
  exchange_connections: Array<ExchangeConnectionItem>;
  kill_switch_active: boolean;
  maintenance_mode: boolean;
  server_ts_ms: number;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface WalkForwardRequest {
  param_candidates: Array<Record<string, unknown>>;
  train_size?: number;
  test_size?: number;
}

export interface WalkForwardResponse {
  window_count: number;
  windows: Array<WalkForwardWindowItem>;
}

export interface WalkForwardWindowItem {
  window_index: number;
  train_start_ms: number;
  train_end_ms: number;
  test_start_ms: number;
  test_end_ms: number;
  best_params: Record<string, unknown>;
  in_sample_score: number;
  oos_return: number;
  oos_sharpe: number;
  oos_max_drawdown: number;
  oos_run_id: string;
}

export type ApiEndpointPath =
  | "/api/v1/audit-logs"
  | "/api/v1/health"
  | "/api/v1/ledger/orders/query"
  | "/api/v1/ledger/orders/{env}/{run_id}/{client_order_id}/fills"
  | "/api/v1/ledger/{table_name}"
  | "/api/v1/reconciliation/fault-drill"
  | "/api/v1/reconciliation/reports"
  | "/api/v1/reconciliation/reports/{report_id}"
  | "/api/v1/reconciliation/reports/{report_id}/export"
  | "/api/v1/reconciliation/run"
  | "/api/v1/risk/events"
  | "/api/v1/risk/events/{event_id}/ack"
  | "/api/v1/risk/kill-switch"
  | "/api/v1/risk/kill-switch/preview"
  | "/api/v1/runs"
  | "/api/v1/runs/backtest/{run_id}/sensitivity"
  | "/api/v1/runs/backtest/{run_id}/walk-forward"
  | "/api/v1/runs/launch"
  | "/api/v1/runs/preflight"
  | "/api/v1/runs/{env}/{run_id}/cancel"
  | "/api/v1/runs/{env}/{run_id}/chart-data"
  | "/api/v1/runs/{env}/{run_id}/report"
  | "/api/v1/runs/{env}/{run_id}/status"
  | "/api/v1/search"
  | "/api/v1/specs"
  | "/api/v1/strategies"
  | "/api/v1/strategies/generate-nl"
  | "/api/v1/strategies/upload"
  | "/api/v1/strategies/versions/{version_id}/approve"
  | "/api/v1/strategies/versions/{version_id}/reject"
  | "/api/v1/strategies/{strategy_id}"
  | "/api/v1/strategies/{strategy_id}/params"
  | "/api/v1/system/diagnostics"
  | "/api/v1/system/maintenance"
  | "/api/v1/system/overview";
