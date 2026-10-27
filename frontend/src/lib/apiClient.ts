import type {
  GlobalSearchResponse,
  HealthResponse,
  KillSwitchPreviewResponse,
  KillSwitchRequest,
  KillSwitchResponse,
  LaunchRunRequest,
  LaunchRunResponse,
  LedgerListResponse,
  LedgerOrdersQueryResponse,
  MaintenanceModeRequest,
  NLStrategyGenerateRequest,
  OrderFillsDrilldownResponse,
  ReconciliationDetailResponse,
  ReconciliationRunRequest,
  RiskEventAckResponse,
  RiskEventListResponse,
  RunChartDataResponse,
  RunPreflightRequest,
  RunPreflightResponse,
  RunStatusResponse,
  RunSummaryItem,
  SensitivityRequest,
  SensitivityResponse,
  StrategyApprovalRequest,
  StrategyDetailResponse,
  StrategyParamUpdateRequest,
  StrategySummaryItem,
  StrategyUploadRequest,
  StrategyVersionResponse,
  SymbolSpecSummary,
  SystemDiagnosticsResponse,
  SystemOverviewResponse,
  WalkForwardRequest,
  WalkForwardResponse,
} from "../types/openapi.generated";

export class QuantApiError extends Error {
  public readonly status: number;
  public readonly reasonCode: string;
  public readonly rawDetail: string;

  constructor(status: number, reasonCode: string, message: string, rawDetail: string) {
    super(message);
    this.name = "QuantApiError";
    this.status = status;
    this.reasonCode = reasonCode;
    this.rawDetail = rawDetail;
  }
}

function parseErrorPayload(status: number, rawText: string): { reasonCode: string; message: string } {
  try {
    const json = JSON.parse(rawText) as Record<string, unknown>;
    const detail = json.detail;
    if (typeof detail === "string") {
      const prefixMatch = /^([A-Z0-9_]{3,}):\s*(.+)$/.exec(detail);
      if (prefixMatch) {
        return { reasonCode: prefixMatch[1], message: prefixMatch[2] };
      }
      return { reasonCode: `HTTP_${status}`, message: detail };
    }
    if (detail && typeof detail === "object") {
      const dObj = detail as Record<string, unknown>;
      return {
        reasonCode: String(dObj.code || `HTTP_${status}`),
        message: String(dObj.message || rawText),
      };
    }
  } catch {
    // plain text body
  }
  return { reasonCode: `HTTP_${status}`, message: rawText || `请求失败 (HTTP ${status})` };
}

async function requestJson<T>(
  path: string,
  apiKey: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const errText = await res.text();
    const { reasonCode, message } = parseErrorPayload(res.status, errText);
    throw new QuantApiError(res.status, reasonCode, message, errText);
  }
  return (await res.json()) as T;
}

export interface OrdersQueryParams {
  env?: string;
  strategy_id?: string;
  run_id?: string;
  symbol?: string;
  side?: string;
  status?: string;
  start_ts_ms?: number;
  end_ts_ms?: number;
  q?: string;
  sort_by?: string;
  sort_dir?: string;
  limit?: number;
  offset?: number;
}

export const quantApi = {
  health: (apiKey: string) => requestJson<HealthResponse>("/api/v1/health", apiKey),

  systemOverview: (apiKey: string) =>
    requestJson<SystemOverviewResponse>("/api/v1/system/overview", apiKey),

  systemDiagnostics: (apiKey: string) =>
    requestJson<SystemDiagnosticsResponse>("/api/v1/system/diagnostics", apiKey),

  toggleMaintenance: (apiKey: string, body: MaintenanceModeRequest) =>
    requestJson<HealthResponse>("/api/v1/system/maintenance", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  globalSearch: (apiKey: string, q: string) =>
    requestJson<GlobalSearchResponse>(
      `/api/v1/search?q=${encodeURIComponent(q)}`,
      apiKey
    ),

  listSpecs: (apiKey: string) =>
    requestJson<SymbolSpecSummary[]>("/api/v1/specs", apiKey),

  listStrategies: (apiKey: string) =>
    requestJson<StrategySummaryItem[]>("/api/v1/strategies", apiKey),

  getStrategyDetail: (apiKey: string, strategyId: string) =>
    requestJson<StrategyDetailResponse>(
      `/api/v1/strategies/${encodeURIComponent(strategyId)}`,
      apiKey
    ),

  uploadStrategy: (apiKey: string, body: StrategyUploadRequest) =>
    requestJson<StrategyVersionResponse>("/api/v1/strategies/upload", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  generateNLStrategy: (apiKey: string, body: NLStrategyGenerateRequest) =>
    requestJson<StrategyVersionResponse>("/api/v1/strategies/generate-nl", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateStrategyParams: (
    apiKey: string,
    strategyId: string,
    body: StrategyParamUpdateRequest
  ) =>
    requestJson<StrategyVersionResponse>(
      `/api/v1/strategies/${encodeURIComponent(strategyId)}/params`,
      apiKey,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),

  approveVersion: (apiKey: string, versionId: string, body: StrategyApprovalRequest) =>
    requestJson<StrategyVersionResponse>(
      `/api/v1/strategies/versions/${encodeURIComponent(versionId)}/approve`,
      apiKey,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),

  rejectVersion: (apiKey: string, versionId: string, body: StrategyApprovalRequest) =>
    requestJson<StrategyVersionResponse>(
      `/api/v1/strategies/versions/${encodeURIComponent(versionId)}/reject`,
      apiKey,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),

  preflightRun: (apiKey: string, body: RunPreflightRequest) =>
    requestJson<RunPreflightResponse>("/api/v1/runs/preflight", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listRuns: (apiKey: string, params?: { env?: string; strategy_id?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.env) sp.set("env", params.env);
    if (params?.strategy_id) sp.set("strategy_id", params.strategy_id);
    if (params?.limit) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return requestJson<RunSummaryItem[]>(`/api/v1/runs${qs ? `?${qs}` : ""}`, apiKey);
  },

  launchRun: (apiKey: string, body: LaunchRunRequest) =>
    requestJson<LaunchRunResponse>("/api/v1/runs/launch", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getRunStatus: (apiKey: string, env: string, runId: string) =>
    requestJson<RunStatusResponse>(
      `/api/v1/runs/${encodeURIComponent(env)}/${encodeURIComponent(runId)}/status`,
      apiKey
    ),

  cancelRun: (apiKey: string, env: string, runId: string) =>
    requestJson<RunStatusResponse>(
      `/api/v1/runs/${encodeURIComponent(env)}/${encodeURIComponent(runId)}/cancel`,
      apiKey,
      { method: "POST" }
    ),

  getBacktestReport: (apiKey: string, env: string, runId: string) =>
    requestJson<Record<string, unknown>>(
      `/api/v1/runs/${encodeURIComponent(env)}/${encodeURIComponent(runId)}/report`,
      apiKey
    ),

  getRunChartData: (apiKey: string, env: string, runId: string) =>
    requestJson<RunChartDataResponse>(
      `/api/v1/runs/${encodeURIComponent(env)}/${encodeURIComponent(runId)}/chart-data`,
      apiKey
    ),

  runSensitivity: (apiKey: string, runId: string, body: SensitivityRequest) =>
    requestJson<SensitivityResponse>(
      `/api/v1/runs/backtest/${encodeURIComponent(runId)}/sensitivity`,
      apiKey,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),

  runWalkForward: (apiKey: string, runId: string, body: WalkForwardRequest) =>
    requestJson<WalkForwardResponse>(
      `/api/v1/runs/backtest/${encodeURIComponent(runId)}/walk-forward`,
      apiKey,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),

  queryOrdersMultiFilter: (apiKey: string, params: OrdersQueryParams) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") {
        sp.set(k, String(v));
      }
    });
    return requestJson<LedgerOrdersQueryResponse>(
      `/api/v1/ledger/orders/query?${sp.toString()}`,
      apiKey
    );
  },

  getOrderFillsDrilldown: (
    apiKey: string,
    env: string,
    runId: string,
    clientOrderId: string
  ) =>
    requestJson<OrderFillsDrilldownResponse>(
      `/api/v1/ledger/orders/${encodeURIComponent(env)}/${encodeURIComponent(runId)}/${encodeURIComponent(clientOrderId)}/fills`,
      apiKey
    ),

  queryLedger: (apiKey: string, table: string, env: string, runId: string) =>
    requestJson<LedgerListResponse>(
      `/api/v1/ledger/${table}?env=${encodeURIComponent(env)}&run_id=${encodeURIComponent(runId)}`,
      apiKey
    ),

  runReconciliation: (apiKey: string, body: ReconciliationRunRequest) =>
    requestJson<Record<string, unknown>>("/api/v1/reconciliation/run", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listReconciliationReports: (apiKey: string) =>
    requestJson<{ items: Array<Record<string, unknown>> }>(
      "/api/v1/reconciliation/reports",
      apiKey
    ),

  getReconciliationDetail: (apiKey: string, reportId: string) =>
    requestJson<ReconciliationDetailResponse>(
      `/api/v1/reconciliation/reports/${encodeURIComponent(reportId)}`,
      apiKey
    ),

  listRiskEvents: (apiKey: string) =>
    requestJson<RiskEventListResponse>("/api/v1/risk/events", apiKey),

  acknowledgeRiskEvent: (apiKey: string, eventId: string) =>
    requestJson<RiskEventAckResponse>(
      `/api/v1/risk/events/${encodeURIComponent(eventId)}/ack`,
      apiKey,
      { method: "POST" }
    ),

  previewKillSwitch: (apiKey: string) =>
    requestJson<KillSwitchPreviewResponse>("/api/v1/risk/kill-switch/preview", apiKey),

  triggerKillSwitch: (apiKey: string, body: KillSwitchRequest) =>
    requestJson<KillSwitchResponse>("/api/v1/risk/kill-switch", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listAuditLogs: (apiKey: string) =>
    requestJson<{ items: Array<Record<string, unknown>> }>(
      "/api/v1/audit-logs",
      apiKey
    ),

  openEventSocket: (
    onEvent: (ev: Record<string, unknown>) => void,
    onStatusChange?: (connected: boolean) => void
  ): WebSocket | null => {
    if (typeof window === "undefined") {
      return null;
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws/stream`);
    ws.onopen = () => onStatusChange?.(true);
    ws.onclose = () => onStatusChange?.(false);
    ws.onerror = () => onStatusChange?.(false);
    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data as string) as Record<string, unknown>;
        onEvent(parsed);
      } catch {
        // ignore malformed frame
      }
    };
    return ws;
  },
};
