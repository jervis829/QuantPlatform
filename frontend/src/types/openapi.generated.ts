/* AUTO-GENERATED FROM FastAPI OpenAPI Schema — DO NOT EDIT MANUALLY */

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  status: string;
  engine_version: string;
  maintenance_mode: boolean;
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

export interface NLStrategyGenerateRequest {
  strategy_id: string;
  name: string;
  prompt: string;
  description?: string;
}

export interface ReconciliationRunRequest {
  tier: string;
  run_id?: string | null;
  shadow_run_id?: string | null;
  live_run_id?: string | null;
  backtest_run_id?: string | null;
  auto_heal?: boolean;
}

export interface StrategyApprovalRequest {
  note?: string;
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

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export type ApiEndpointPath =
  | "/api/v1/audit-logs"
  | "/api/v1/health"
  | "/api/v1/ledger/{table_name}"
  | "/api/v1/reconciliation/reports"
  | "/api/v1/reconciliation/reports/{report_id}/export"
  | "/api/v1/reconciliation/run"
  | "/api/v1/risk/events"
  | "/api/v1/risk/kill-switch"
  | "/api/v1/runs/launch"
  | "/api/v1/runs/{env}/{run_id}/report"
  | "/api/v1/strategies"
  | "/api/v1/strategies/generate-nl"
  | "/api/v1/strategies/upload"
  | "/api/v1/strategies/versions/{version_id}/approve"
  | "/api/v1/strategies/versions/{version_id}/reject"
  | "/api/v1/strategies/{strategy_id}"
  | "/api/v1/strategies/{strategy_id}/params";
