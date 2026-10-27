import type {
  HealthResponse,
  KillSwitchRequest,
  KillSwitchResponse,
  LaunchRunRequest,
  LaunchRunResponse,
  LedgerListResponse,
  NLStrategyGenerateRequest,
  ReconciliationRunRequest,
  StrategyApprovalRequest,
  StrategyDetailResponse,
  StrategyParamUpdateRequest,
  StrategySummaryItem,
  StrategyUploadRequest,
  StrategyVersionResponse,
} from "../types/openapi.generated";

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
    throw new Error(`HTTP ${res.status}: ${errText}`);
  }
  return (await res.json()) as T;
}

export const quantApi = {
  health: (apiKey: string) => requestJson<HealthResponse>("/api/v1/health", apiKey),

  listStrategies: (apiKey: string) =>
    requestJson<StrategySummaryItem[]>("/api/v1/strategies", apiKey),

  getStrategyDetail: (apiKey: string, strategyId: string) =>
    requestJson<StrategyDetailResponse>(`/api/v1/strategies/${strategyId}`, apiKey),

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
      `/api/v1/strategies/${strategyId}/params`,
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

  launchRun: (apiKey: string, body: LaunchRunRequest) =>
    requestJson<LaunchRunResponse>("/api/v1/runs/launch", apiKey, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getBacktestReport: (apiKey: string, env: string, runId: string) =>
    requestJson<Record<string, unknown>>(
      `/api/v1/runs/${env}/${encodeURIComponent(runId)}/report`,
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

  listRiskEvents: (apiKey: string) =>
    requestJson<{ items: Array<Record<string, unknown>> }>(
      "/api/v1/risk/events",
      apiKey
    ),

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

  openEventSocket: (onEvent: (ev: Record<string, unknown>) => void): WebSocket | null => {
    if (typeof window === "undefined") {
      return null;
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws/stream`);
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
