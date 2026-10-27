"use client";

import React, { useCallback, useEffect, useState } from "react";
import { DiffViewer } from "../components/DiffViewer";
import { DynamicSchemaForm } from "../components/DynamicSchemaForm";
import { quantApi } from "../lib/apiClient";
import type {
  RiskEventItem,
  StrategyDetailResponse,
  StrategySummaryItem,
  StrategyVersionResponse,
} from "../types/openapi.generated";

type TabId =
  | "strategies"
  | "detail"
  | "report"
  | "ledger"
  | "reconcile"
  | "risk";

const DEFAULT_PYTHON_TEMPLATE = `from decimal import Decimal
from pydantic import BaseModel, Field
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import sma


class SmaCrossParams(BaseModel):
    fast_window: int = Field(default=5, ge=2, le=100)
    slow_window: int = Field(default=12, ge=3, le=300)
    order_qty: Decimal = Field(default=Decimal("0.05"))


class SmaCrossStrategy(Strategy):
    ParamModel = SmaCrossParams

    def on_start(self):
        self._closes = []
        self._prev_fast = None
        self._prev_slow = None
        return None

    def on_bar(self, bar: Bar):
        self._closes.append(bar.close)
        if len(self._closes) < self.params.slow_window:
            return None
        f_val = sma(self._closes, self.params.fast_window)
        s_val = sma(self._closes, self.params.slow_window)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")
        out = None
        if self._prev_fast is not None and self._prev_slow is not None:
            if self._prev_fast <= self._prev_slow and f_val > s_val and cur_qty == Decimal("0"):
                out = [Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.params.order_qty,
                    reason="sma_cross_up",
                    ts_ms=self.ctx.now_ms,
                )]
            elif self._prev_fast >= self._prev_slow and f_val < s_val and cur_qty > Decimal("0"):
                out = [Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=cur_qty,
                    reduce_only=True,
                    reason="sma_cross_down",
                    ts_ms=self.ctx.now_ms,
                )]
        self._prev_fast = f_val
        self._prev_slow = s_val
        return out
`;

export default function QuantConsolePage() {
  const [apiKey, setApiKey] = useState("admin-dev-token");
  const [activeTab, setActiveTab] = useState<TabId>("strategies");
  const [banner, setBanner] = useState<string>("");
  const [wsLogs, setWsLogs] = useState<Array<Record<string, unknown>>>([]);

  // Strategy list & detail state
  const [strategies, setStrategies] = useState<StrategySummaryItem[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>("");
  const [detail, setDetail] = useState<StrategyDetailResponse | null>(null);
  const [selectedVer, setSelectedVer] = useState<StrategyVersionResponse | null>(null);

  // Upload & NL forms
  const [newStratId, setNewStratId] = useState("strat_btc_sma");
  const [newStratName, setNewStratName] = useState("BTC USDT-M SMA Crossover");
  const [uploadCode, setUploadCode] = useState(DEFAULT_PYTHON_TEMPLATE);
  const [nlPrompt, setNlPrompt] = useState(
    "当 BTC/USDT:USDT 5周期均线上穿 12周期均线时买入 0.05 BTC，下穿时平仓"
  );

  // Report & Unified Ledger state
  const [reportRunId, setReportRunId] = useState("");
  const [reportData, setReportData] = useState<Record<string, unknown> | null>(null);
  const [ledgerEnv, setLedgerEnv] = useState("backtest");
  const [ledgerRunId, setLedgerRunId] = useState("");
  const [ledgerTable, setLedgerTable] = useState("orders");
  const [ledgerItems, setLedgerItems] = useState<Array<Record<string, unknown>>>([]);

  // Reconciliation & Risk state
  const [btReconRunId, setBtReconRunId] = useState("");
  const [shReconRunId, setShReconRunId] = useState("");
  const [lvReconRunId, setLvReconRunId] = useState("");
  const [reconReports, setReconReports] = useState<Array<Record<string, unknown>>>([]);
  const [riskEvents, setRiskEvents] = useState<RiskEventItem[]>([]);
  const [auditLogs, setAuditLogs] = useState<Array<Record<string, unknown>>>([]);
  const [flattenOnKill, setFlattenOnKill] = useState(true);

  const refreshStrategies = useCallback(async () => {
    try {
      const items = await quantApi.listStrategies(apiKey);
      setStrategies(items);
    } catch (err) {
      setBanner(String(err));
    }
  }, [apiKey]);

  const loadStrategyDetail = useCallback(
    async (sid: string) => {
      try {
        const d = await quantApi.getStrategyDetail(apiKey, sid);
        setSelectedStrategyId(sid);
        setDetail(d);
        setSelectedVer(d.latest_version);
        setActiveTab("detail");
      } catch (err) {
        setBanner(String(err));
      }
    },
    [apiKey]
  );

  useEffect(() => {
    void refreshStrategies();
  }, [refreshStrategies]);

  useEffect(() => {
    const ws = quantApi.openEventSocket((ev) => {
      setWsLogs((prev) => [ev, ...prev].slice(0, 50));
    });
    return () => {
      if (ws) ws.close();
    };
  }, []);

  const handleUpload = async () => {
    try {
      const v = await quantApi.uploadStrategy(apiKey, {
        strategy_id: newStratId,
        name: newStratName,
        code: uploadCode,
      });
      setBanner(`已上传策略版本 ${v.version_id}（审批状态: ${v.approval_status}）`);
      await refreshStrategies();
      await loadStrategyDetail(newStratId);
    } catch (err) {
      setBanner(String(err));
    }
  };

  const handleNLGenerate = async () => {
    try {
      const sid = `${newStratId}_nl`;
      const v = await quantApi.generateNLStrategy(apiKey, {
        strategy_id: sid,
        name: `${newStratName} (NL)`,
        prompt: nlPrompt,
      });
      setBanner(`NL 策略已生成并完成沙箱+回测门禁：${v.version_id}（待人工审批: ${v.approval_status}）`);
      await refreshStrategies();
      await loadStrategyDetail(sid);
    } catch (err) {
      setBanner(String(err));
    }
  };

  const handleLaunch = async (env: "backtest" | "shadow" | "live") => {
    if (!detail || !selectedVer) return;
    try {
      const res = await quantApi.launchRun(apiKey, {
        strategy_id: detail.meta.strategy_id,
        version_id: selectedVer.version_id,
        env,
      });
      setBanner(`已运行 [${env.toUpperCase()}] run_id=${res.run_id} (${res.status})`);
      setLedgerEnv(env);
      setLedgerRunId(res.run_id);
      if (env === "backtest") {
        setReportRunId(res.run_id);
        setBtReconRunId(res.run_id);
      } else if (env === "shadow") {
        setShReconRunId(res.run_id);
      } else {
        setLvReconRunId(res.run_id);
      }
      await loadStrategyDetail(detail.meta.strategy_id);
    } catch (err) {
      setBanner(String(err));
    }
  };

  const cardStyle: React.CSSProperties = {
    background: "#111827",
    border: "1px solid #1f2937",
    borderRadius: "8px",
    padding: "16px",
    marginBottom: "16px",
  };

  return (
    <div style={{ maxWidth: "1360px", margin: "0 auto", padding: "20px" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid #1f2937",
          paddingBottom: "14px",
          marginBottom: "16px",
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: "22px" }}>
            QuantPlatform 自托管多策略加密资产交易管理台
          </h1>
          <small style={{ color: "#94a3b8" }}>
            三环境同构 (Backtest / Shadow / Live) · 版本化 SymbolSpec · 隔离沙箱 · 严格 Decimal
          </small>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <label style={{ fontSize: "13px", color: "#94a3b8" }}>RBAC 身份令牌:</label>
          <select
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            style={{
              background: "#1e293b",
              color: "#f8fafc",
              border: "1px solid #334155",
              padding: "6px 10px",
              borderRadius: "6px",
            }}
          >
            <option value="admin-dev-token">ADMIN (admin-dev-token)</option>
            <option value="operator-dev-token">OPERATOR (operator-dev-token)</option>
            <option value="researcher-dev-token">RESEARCHER (researcher-dev-token)</option>
            <option value="viewer-dev-token">VIEWER (viewer-dev-token)</option>
          </select>
        </div>
      </header>

      {banner && (
        <div
          style={{
            background: "#1e293b",
            borderLeft: "4px solid #38bdf8",
            padding: "10px 14px",
            borderRadius: "4px",
            marginBottom: "16px",
            fontSize: "13px",
          }}
        >
          {banner}
        </div>
      )}

      <nav style={{ display: "flex", gap: "8px", marginBottom: "18px", flexWrap: "wrap" }}>
        {(
          [
            ["strategies", "1. 策略列表与创建 (Upload / NL)"],
            ["detail", "2. 策略详情 · Diff · 动态表单 · 一键运行"],
            ["report", "3. 回测报告与净值归因"],
            ["ledger", "4. 三环境统一交易记录"],
            ["reconcile", "5. 三层对账中心"],
            ["risk", "6. 风控监控与紧急 Kill Switch"],
          ] as Array<[TabId, string]>
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            style={{
              background: activeTab === id ? "#2563eb" : "#1e293b",
              color: "#f8fafc",
              border: "1px solid #334155",
              padding: "8px 14px",
              borderRadius: "6px",
              cursor: "pointer",
              fontWeight: activeTab === id ? 700 : 500,
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      {activeTab === "strategies" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <section style={cardStyle}>
            <h2 style={{ marginTop: 0, fontSize: "17px" }}>已注册策略列表</h2>
            {strategies.length === 0 ? (
              <p style={{ color: "#94a3b8" }}>暂无策略，请在右侧上传 Python 代码或通过自然语言生成。</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid #334155" }}>
                    <th style={{ padding: "6px" }}>ID</th>
                    <th>名称</th>
                    <th>来源</th>
                    <th>当前版本</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s) => (
                    <tr key={s.strategy_id} style={{ borderBottom: "1px solid #1e293b" }}>
                      <td style={{ padding: "6px" }}>{s.strategy_id}</td>
                      <td>{s.name}</td>
                      <td>{s.source_type}</td>
                      <td>v{s.current_version}</td>
                      <td>
                        <button
                          onClick={() => void loadStrategyDetail(s.strategy_id)}
                          style={{
                            background: "#0ea5e9",
                            color: "#fff",
                            border: "none",
                            padding: "4px 10px",
                            borderRadius: "4px",
                            cursor: "pointer",
                          }}
                        >
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section style={cardStyle}>
            <h2 style={{ marginTop: 0, fontSize: "17px" }}>上传 Python 策略 / 自然语言生成策略</h2>
            <div style={{ display: "grid", gap: "8px", marginBottom: "12px" }}>
              <input
                value={newStratId}
                onChange={(e) => setNewStratId(e.target.value)}
                placeholder="strategy_id"
                style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px" }}
              />
              <input
                value={newStratName}
                onChange={(e) => setNewStratName(e.target.value)}
                placeholder="策略名称"
                style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px" }}
              />
              <textarea
                rows={10}
                value={uploadCode}
                onChange={(e) => setUploadCode(e.target.value)}
                style={{
                  background: "#020617",
                  color: "#e2e8f0",
                  border: "1px solid #334155",
                  fontFamily: "monospace",
                  fontSize: "12px",
                  padding: "8px",
                }}
              />
              <button
                onClick={() => void handleUpload()}
                style={{
                  background: "#16a34a",
                  color: "#fff",
                  border: "none",
                  padding: "8px",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontWeight: 600,
                }}
              >
                通过 AST + 隔离沙箱校验并注册 Python 策略
              </button>
            </div>
            <hr style={{ borderColor: "#1e293b" }} />
            <h3 style={{ fontSize: "15px" }}>自然语言生成策略（共享沙箱与自动回测门禁）</h3>
            <textarea
              rows={3}
              value={nlPrompt}
              onChange={(e) => setNlPrompt(e.target.value)}
              style={{ width: "100%", background: "#020617", color: "#fff", border: "1px solid #334155", padding: "8px" }}
            />
            <button
              onClick={() => void handleNLGenerate()}
              style={{
                marginTop: "8px",
                width: "100%",
                background: "#7c3aed",
                color: "#fff",
                border: "none",
                padding: "8px",
                borderRadius: "6px",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              NL -&gt; LLM 生成代码 + 沙箱测试 + 自动回测门禁（入库待人工审批）
            </button>
          </section>
        </div>
      )}

      {activeTab === "detail" && (
        <div>
          {!detail || !selectedVer ? (
            <section style={cardStyle}>请先从「策略列表」选择一个策略（当前选择: {selectedStrategyId || "无"}）。</section>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "16px" }}>
              <section style={cardStyle}>
                <h2 style={{ marginTop: 0 }}>
                  {detail.meta.name} <small style={{ color: "#94a3b8" }}>({detail.meta.strategy_id})</small>
                </h2>
                <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "12px" }}>
                  <span>选择版本:</span>
                  <select
                    value={selectedVer.version_id}
                    onChange={(e) => {
                      const found = detail.versions.find((v) => v.version_id === e.target.value);
                      if (found) setSelectedVer(found);
                    }}
                    style={{ background: "#1e293b", color: "#fff", padding: "4px 8px" }}
                  >
                    {detail.versions.map((v) => (
                      <option key={v.version_id} value={v.version_id}>
                        {v.version_id} [{v.approval_status}]
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={async () => {
                      await quantApi.approveVersion(apiKey, selectedVer.version_id, {
                        note: "Manual operator approval from UI",
                      });
                      await loadStrategyDetail(detail.meta.strategy_id);
                    }}
                    style={{ background: "#16a34a", color: "#fff", border: "none", padding: "5px 10px", borderRadius: "4px", cursor: "pointer" }}
                  >
                    人工审批通过 (Approve)
                  </button>
                  <button
                    onClick={async () => {
                      await quantApi.rejectVersion(apiKey, selectedVer.version_id, {
                        note: "Manual operator rejection from UI",
                      });
                      await loadStrategyDetail(detail.meta.strategy_id);
                    }}
                    style={{ background: "#dc2626", color: "#fff", border: "none", padding: "5px 10px", borderRadius: "4px", cursor: "pointer" }}
                  >
                    驳回 (Reject)
                  </button>
                </div>

                <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
                  <button
                    onClick={() => void handleLaunch("backtest")}
                    style={{ background: "#2563eb", color: "#fff", border: "none", padding: "8px 14px", borderRadius: "6px", cursor: "pointer", fontWeight: 600 }}
                  >
                    一键启动回测 (Backtest)
                  </button>
                  <button
                    onClick={() => void handleLaunch("shadow")}
                    style={{ background: "#d97706", color: "#fff", border: "none", padding: "8px 14px", borderRadius: "6px", cursor: "pointer", fontWeight: 600 }}
                  >
                    一键启动影子交易 (Shadow)
                  </button>
                  <button
                    onClick={() => void handleLaunch("live")}
                    style={{ background: "#059669", color: "#fff", border: "none", padding: "8px 14px", borderRadius: "6px", cursor: "pointer", fontWeight: 600 }}
                  >
                    一键启动模拟/实盘 (Live Testnet)
                  </button>
                </div>

                <h4>版本变更 Diff ({selectedVer.version_id})</h4>
                <DiffViewer diffText={selectedVer.diff_text} />

                <h4>策略源码（SHA256: {selectedVer.code_hash.slice(0, 16)}...）</h4>
                <pre style={{ background: "#020617", padding: "10px", borderRadius: "6px", overflowX: "auto", fontSize: "12px" }}>
                  {selectedVer.code}
                </pre>
              </section>

              <section style={cardStyle}>
                <h3 style={{ marginTop: 0 }}>Pydantic Schema 自动渲染参数表单</h3>
                <DynamicSchemaForm
                  schema={selectedVer.param_schema}
                  initialValues={selectedVer.params}
                  onSubmit={async (vals) => {
                    await quantApi.updateStrategyParams(apiKey, detail.meta.strategy_id, {
                      params: vals,
                    });
                    await loadStrategyDetail(detail.meta.strategy_id);
                  }}
                />

                <h3 style={{ marginTop: "20px" }}>历史运行记录 (Backtest / Shadow / Live)</h3>
                <ul style={{ fontSize: "13px", paddingLeft: "18px" }}>
                  {detail.runs.map((r, i) => (
                    <li key={i} style={{ marginBottom: "6px" }}>
                      <strong>[{String(r.env)}]</strong> {String(r.run_id)} — {String(r.status)}{" "}
                      {r.env === "backtest" && (
                        <button
                          onClick={async () => {
                            const rep = await quantApi.getBacktestReport(apiKey, "backtest", String(r.run_id));
                            setReportRunId(String(r.run_id));
                            setReportData(rep);
                            setActiveTab("report");
                          }}
                          style={{ marginLeft: "6px", fontSize: "11px", cursor: "pointer" }}
                        >
                          查看报告
                        </button>
                      )}
                    </li>
                  ))}
                </ul>

                <h3>WebSocket 实时事件日志流</h3>
                <pre style={{ background: "#020617", padding: "10px", borderRadius: "6px", maxHeight: "200px", overflowY: "auto", fontSize: "11px" }}>
                  {wsLogs.map((l, idx) => JSON.stringify(l)).join("\n") || "等待实时事件..."}
                </pre>
              </section>
            </div>
          )}
        </div>
      )}

      {activeTab === "report" && (
        <section style={cardStyle}>
          <h2 style={{ marginTop: 0 }}>回测绩效与归因报告</h2>
          <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
            <input
              value={reportRunId}
              onChange={(e) => setReportRunId(e.target.value)}
              placeholder="输入回测 run_id"
              style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px 10px", width: "360px" }}
            />
            <button
              onClick={async () => {
                try {
                  const rep = await quantApi.getBacktestReport(apiKey, "backtest", reportRunId);
                  setReportData(rep);
                } catch (err) {
                  setBanner(String(err));
                }
              }}
              style={{ background: "#2563eb", color: "#fff", border: "none", padding: "6px 14px", borderRadius: "6px", cursor: "pointer" }}
            >
              加载报告
            </button>
          </div>
          {reportData && (
            <pre style={{ background: "#020617", padding: "14px", borderRadius: "6px", overflowX: "auto", fontSize: "12px" }}>
              {JSON.stringify(reportData, null, 2)}
            </pre>
          )}
        </section>
      )}

      {activeTab === "ledger" && (
        <section style={cardStyle}>
          <h2 style={{ marginTop: 0 }}>三环境统一交易账本查询 (Red Line 5)</h2>
          <div style={{ display: "flex", gap: "8px", marginBottom: "14px", flexWrap: "wrap" }}>
            <select value={ledgerEnv} onChange={(e) => setLedgerEnv(e.target.value)} style={{ background: "#1e293b", color: "#fff", padding: "6px" }}>
              <option value="backtest">backtest</option>
              <option value="shadow">shadow</option>
              <option value="live">live</option>
            </select>
            <select value={ledgerTable} onChange={(e) => setLedgerTable(e.target.value)} style={{ background: "#1e293b", color: "#fff", padding: "6px" }}>
              <option value="orders">orders</option>
              <option value="fills">fills</option>
              <option value="positions">positions</option>
              <option value="balance_snapshots">balance_snapshots</option>
              <option value="equity_curve">equity_curve</option>
              <option value="funding_payments">funding_payments</option>
            </select>
            <input
              value={ledgerRunId}
              onChange={(e) => setLedgerRunId(e.target.value)}
              placeholder="run_id"
              style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px 10px", width: "320px" }}
            />
            <button
              onClick={async () => {
                try {
                  const res = await quantApi.queryLedger(apiKey, ledgerTable, ledgerEnv, ledgerRunId);
                  setLedgerItems(res.items);
                } catch (err) {
                  setBanner(String(err));
                }
              }}
              style={{ background: "#2563eb", color: "#fff", border: "none", padding: "6px 14px", borderRadius: "6px", cursor: "pointer" }}
            >
              查询统一表
            </button>
          </div>
          <pre style={{ background: "#020617", padding: "12px", borderRadius: "6px", overflowX: "auto", fontSize: "12px" }}>
            {JSON.stringify(ledgerItems, null, 2)}
          </pre>
        </section>
      )}

      {activeTab === "reconcile" && (
        <section style={cardStyle}>
          <h2 style={{ marginTop: 0 }}>三层对账中心（系统vs交易所 / 影子vs实盘 / 回测vs影子 6维归因）</h2>
          <div style={{ display: "flex", gap: "8px", marginBottom: "12px", flexWrap: "wrap" }}>
            <input value={btReconRunId} onChange={(e) => setBtReconRunId(e.target.value)} placeholder="backtest_run_id" style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px" }} />
            <input value={shReconRunId} onChange={(e) => setShReconRunId(e.target.value)} placeholder="shadow_run_id" style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px" }} />
            <input value={lvReconRunId} onChange={(e) => setLvReconRunId(e.target.value)} placeholder="live_run_id" style={{ background: "#0f172a", color: "#fff", border: "1px solid #334155", padding: "6px" }} />
            <button
              onClick={async () => {
                await quantApi.runReconciliation(apiKey, { tier: "exchange_vs_local", run_id: lvReconRunId || null });
                const list = await quantApi.listReconciliationReports(apiKey);
                setReconReports(list.items);
              }}
              style={{ background: "#059669", color: "#fff", border: "none", padding: "6px 10px", borderRadius: "4px", cursor: "pointer" }}
            >
              触发 Tier-1 (系统 vs 交易所)
            </button>
            <button
              onClick={async () => {
                await quantApi.runReconciliation(apiKey, { tier: "shadow_vs_live", shadow_run_id: shReconRunId, live_run_id: lvReconRunId });
                const list = await quantApi.listReconciliationReports(apiKey);
                setReconReports(list.items);
              }}
              style={{ background: "#d97706", color: "#fff", border: "none", padding: "6px 10px", borderRadius: "4px", cursor: "pointer" }}
            >
              触发 Tier-2 (影子 vs 实盘)
            </button>
            <button
              onClick={async () => {
                await quantApi.runReconciliation(apiKey, { tier: "backtest_vs_shadow", backtest_run_id: btReconRunId, shadow_run_id: shReconRunId });
                const list = await quantApi.listReconciliationReports(apiKey);
                setReconReports(list.items);
              }}
              style={{ background: "#2563eb", color: "#fff", border: "none", padding: "6px 10px", borderRadius: "4px", cursor: "pointer" }}
            >
              触发 Tier-3 (回测 vs 影子)
            </button>
          </div>
          <pre style={{ background: "#020617", padding: "12px", borderRadius: "6px", overflowX: "auto", fontSize: "12px" }}>
            {JSON.stringify(reconReports, null, 2)}
          </pre>
        </section>
      )}

      {activeTab === "risk" && (
        <section style={cardStyle}>
          <h2 style={{ marginTop: 0, color: "#f87171" }}>风控监控与亚秒级紧急停机开关 (Kill Switch &lt; 1s)</h2>
          <div style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px" }}>
            <label>
              <input
                type="checkbox"
                checked={flattenOnKill}
                onChange={(e) => setFlattenOnKill(e.target.checked)}
              />{" "}
              全撤挂单同时以 Reduce-Only 市价单平掉所有敞口
            </label>
            <button
              onClick={async () => {
                const res = await quantApi.triggerKillSwitch(apiKey, {
                  close_positions: flattenOnKill,
                  reason: "console_emergency_kill_switch",
                });
                setBanner(
                  `紧急停机已执行！撤单=${res.canceled_orders}, 平仓=${res.flattened_positions}, 耗时=${res.elapsed_ms}ms`
                );
                const evs = await quantApi.listRiskEvents(apiKey);
                setRiskEvents(evs.items);
                const aud = await quantApi.listAuditLogs(apiKey);
                setAuditLogs(aud.items);
              }}
              style={{
                background: "#dc2626",
                color: "#fff",
                border: "2px solid #fca5a5",
                padding: "10px 20px",
                borderRadius: "8px",
                cursor: "pointer",
                fontWeight: 800,
              }}
            >
              立即触发 KILL SWITCH
            </button>
            <button
              onClick={async () => {
                const evs = await quantApi.listRiskEvents(apiKey);
                setRiskEvents(evs.items);
                const aud = await quantApi.listAuditLogs(apiKey);
                setAuditLogs(aud.items);
              }}
              style={{ background: "#334155", color: "#fff", border: "none", padding: "8px 14px", borderRadius: "6px", cursor: "pointer" }}
            >
              刷新风控与不可篡改审计日志
            </button>
          </div>
          <h4>风控触发事件 (`risk_events`)</h4>
          <pre style={{ background: "#020617", padding: "10px", borderRadius: "6px", maxHeight: "220px", overflow: "auto", fontSize: "12px" }}>
            {JSON.stringify(riskEvents, null, 2)}
          </pre>
          <h4>哈希链防篡改审计日志 (`audit_logs`)</h4>
          <pre style={{ background: "#020617", padding: "10px", borderRadius: "6px", maxHeight: "220px", overflow: "auto", fontSize: "12px" }}>
            {JSON.stringify(auditLogs, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}
