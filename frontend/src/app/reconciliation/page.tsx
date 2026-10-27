"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  CheckCircle2,
  Download,
  ExternalLink,
  FlaskConical,
  GitCompareArrows,
  Play,
} from "lucide-react";
import Link from "next/link";
import { EnvBadge, SeverityBadge } from "../../components/quant/badges";
import { QuantDataTable } from "../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../components/quant/state-views";
import { Button } from "../../components/ui/button";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";

type ReconTierKey =
  | "exchange_vs_local"
  | "shadow_vs_live"
  | "backtest_vs_shadow";

interface ReconReportSummaryItem {
  report_id: string;
  tier: string;
  env: string;
  run_id: string;
  reference_run_id?: string | null;
  passed: boolean;
  discrepancy_count: number;
  max_severity: string;
  severity_counts: Record<string, number>;
  total_abs_drift_usdt: string;
  summary: Record<string, unknown>;
  items: Array<Record<string, unknown>>;
  created_ts_ms: number;
}

const TIER_TABS: Array<{ id: ReconTierKey; title: string; subtitle: string }> = [
  {
    id: "exchange_vs_local",
    title: "Tier 1 · 系统 vs 交易所",
    subtitle: "核对实盘持仓、挂单、逐笔成交与资金费结算权威一致性",
  },
  {
    id: "shadow_vs_live",
    title: "Tier 2 · 影子 vs 实盘",
    subtitle: "核对同版本策略在模拟撮合与真实交易所间的滑点与成交率偏差",
  },
  {
    id: "backtest_vs_shadow",
    title: "Tier 3 · 回测 vs 影子 (6维归因)",
    subtitle: "拆解撮合假设、盘口排队、网络延迟、手续费与资金费差异来源",
  },
];

function ReconciliationBoardInner() {
  const { apiKey, roleLabel } = usePreferences();
  const queryClient = useQueryClient();

  const [activeTier, setActiveTier] = useState<ReconTierKey>("exchange_vs_local");
  const [selectedReportId, setSelectedReportId] = useState<string>("");

  const [liveRunId, setLiveRunId] = useState("");
  const [shadowRunId, setShadowRunId] = useState("");
  const [backtestRunId, setBacktestRunId] = useState("");
  const [autoHeal, setAutoHeal] = useState(false);

  const canOperate = roleLabel === "OPERATOR" || roleLabel === "ADMIN";

  const reportsQuery = useQuery({
    queryKey: ["reconciliation-reports", apiKey],
    queryFn: () => quantApi.listReconciliationReports(apiKey),
  });

  const runsQuery = useQuery({
    queryKey: ["runs", "all-for-recon", apiKey],
    queryFn: () => quantApi.listRuns(apiKey, { limit: 150 }),
  });

  const allReports = useMemo(
    () => ((reportsQuery.data?.items ?? []) as unknown as ReconReportSummaryItem[]),
    [reportsQuery.data?.items]
  );

  const tierReports = useMemo(
    () => allReports.filter((r) => r.tier === activeTier),
    [activeTier, allReports]
  );

  useEffect(() => {
    if (tierReports.length > 0) {
      if (!tierReports.some((r) => r.report_id === selectedReportId)) {
        setSelectedReportId(tierReports[0].report_id);
      }
    } else {
      setSelectedReportId("");
    }
  }, [selectedReportId, tierReports]);

  // Pre-fill run selectors from latest runs per env
  useEffect(() => {
    const runs = runsQuery.data ?? [];
    if (!liveRunId) {
      const lv = runs.find((r) => r.env === "live");
      if (lv) setLiveRunId(lv.run_id);
    }
    if (!shadowRunId) {
      const sh = runs.find((r) => r.env === "shadow");
      if (sh) setShadowRunId(sh.run_id);
    }
    if (!backtestRunId) {
      const bt = runs.find((r) => r.env === "backtest");
      if (bt) setBacktestRunId(bt.run_id);
    }
  }, [backtestRunId, liveRunId, runsQuery.data, shadowRunId]);

  const activeReport = useMemo(
    () =>
      tierReports.find((r) => r.report_id === selectedReportId) ??
      tierReports[0] ??
      null,
    [selectedReportId, tierReports]
  );

  const runReconMutation = useMutation({
    mutationFn: () =>
      quantApi.runReconciliation(apiKey, {
        tier: activeTier,
        run_id: activeTier === "exchange_vs_local" ? liveRunId || null : null,
        shadow_run_id:
          activeTier === "shadow_vs_live" || activeTier === "backtest_vs_shadow"
            ? shadowRunId
            : null,
        live_run_id: activeTier === "shadow_vs_live" ? liveRunId : null,
        backtest_run_id: activeTier === "backtest_vs_shadow" ? backtestRunId : null,
        auto_heal: activeTier === "exchange_vs_local" ? autoHeal : false,
      }),
    onSuccess: (res) => {
      if (res.report_id) setSelectedReportId(String(res.report_id));
      void queryClient.invalidateQueries({ queryKey: ["reconciliation-reports"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const faultDrillMutation = useMutation({
    mutationFn: () => quantApi.runReconciliationFaultDrill(apiKey),
    onSuccess: (rep) => {
      setActiveTier("exchange_vs_local");
      setSelectedReportId(rep.report_id);
      void queryClient.invalidateQueries({ queryKey: ["reconciliation-reports"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const handleExport = async (fmt: "csv" | "json") => {
    if (!activeReport) return;
    const content = await quantApi.exportReconciliationReport(
      apiKey,
      activeReport.report_id,
      fmt
    );
    const blob = new Blob([content], {
      type: fmt === "csv" ? "text/csv;charset=utf-8;" : "application/json;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${activeReport.report_id}.${fmt}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const discrepancyColumns = useMemo<
    ColumnDef<Record<string, unknown>, unknown>[]
  >(
    () => [
      {
        id: "severity",
        accessorFn: (r) => String(r.severity ?? "LOW"),
        header: "严重程度",
        cell: ({ row }) => (
          <SeverityBadge severity={String(row.original.severity ?? "LOW")} />
        ),
      },
      {
        id: "type",
        accessorFn: (r) => String(r.type ?? r.dimension ?? "DISCREPANCY"),
        header: "差异类型 / 归因维度",
        cell: ({ row }) => (
          <code className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs font-semibold text-white">
            {String(row.original.type ?? row.original.dimension ?? "-")}
          </code>
        ),
      },
      {
        id: "symbol",
        accessorFn: (r) => String(r.symbol ?? "GLOBAL"),
        header: "标的 / 资产",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="font-semibold text-sky-300">
            {String(row.original.symbol ?? "—")}
          </span>
        ),
      },
      {
        id: "local_vs_remote",
        accessorFn: (r) =>
          `${String(r.local_value ?? r.backtest_value ?? r.shadow_value ?? "-")} -> ${String(
            r.exchange_value ?? r.live_value ?? r.remote_value ?? "-"
          )}`,
        header: "本地预期值 vs 远端实际值",
        meta: { mono: true },
        cell: ({ row }) => {
          const it = row.original;
          const loc = String(
            it.local_value ?? it.backtest_value ?? it.shadow_value ?? "—"
          );
          const rem = String(
            it.exchange_value ?? it.live_value ?? it.remote_value ?? "—"
          );
          return (
            <span className="text-xs">
              <span className="text-slate-300">{loc}</span>
              <span className="mx-1.5 text-slate-500">≠</span>
              <strong className="text-amber-300">{rem}</strong>
            </span>
          );
        },
      },
      {
        id: "abs_drift_usdt",
        accessorFn: (r) => Number(r.abs_drift_usdt ?? 0),
        header: "影响金额 (USDT)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={String(row.original.abs_drift_usdt ?? "0")}
            mode="currency"
            className="font-bold text-rose-300"
          />
        ),
      },
      {
        id: "identifiers",
        accessorFn: (r) => `${String(r.order_id ?? "")} ${String(r.fill_id ?? "")}`,
        header: "定位单号 (order_id / fill_id)",
        meta: { mono: true },
        cell: ({ row }) => {
          const oid = row.original.order_id ? String(row.original.order_id) : null;
          const fid = row.original.fill_id ? String(row.original.fill_id) : null;
          return (
            <div className="text-[11px] text-slate-300">
              {oid ? <span>ord:{oid}</span> : null}
              {oid && fid ? <span className="mx-1 text-slate-600">·</span> : null}
              {fid ? <span className="text-amber-200">fill:{fid}</span> : null}
              {!oid && !fid && <span className="text-slate-500">账户/仓位级</span>}
            </div>
          );
        },
      },
      {
        id: "ts_ms",
        accessorFn: (r) => Number(r.ts_ms ?? 0),
        header: "发生时间戳 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => {
          const ts = Number(row.original.ts_ms ?? 0);
          return (
            <span className="text-[11px] text-slate-400">
              {ts > 0 ? formatUtcTimestamp(ts, true) : "—"}
            </span>
          );
        },
      },
    ],
    []
  );

  const apiErr =
    runReconMutation.error instanceof QuantApiError ? runReconMutation.error : null;

  return (
    <div className="space-y-5 pb-12">
      {/* Header + 1-Click Fault Drill Verification Button */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">
            三层对账与一致性归因看板 (/reconciliation)
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            快速回答「哪里不一致、严重程度如何、影响多少钱」；点击任意差异行可展开下钻到具体 order_id / fill_id / 毫秒时间戳并直跳账本。
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canOperate && (
            <Button
              size="sm"
              variant="shadow"
              disabled={faultDrillMutation.isPending}
              onClick={() => faultDrillMutation.mutate()}
              title="在后端数据库真实执行一次包含仓位不平、漏成交与资金费差异的 Tier-1 对账故障演练"
            >
              <FlaskConical className="h-3.5 w-3.5" />
              <span>
                {faultDrillMutation.isPending
                  ? "正在注入差异并执行对账..."
                  : "运行差异故障演练 (仓位不平 + 漏成交 + 资金费差异)"}
              </span>
            </Button>
          )}
          {activeReport && (
            <>
              <Button size="sm" variant="outline" onClick={() => void handleExport("csv")}>
                <Download className="h-3.5 w-3.5" />
                <span>导出 CSV</span>
              </Button>
              <Button size="sm" variant="outline" onClick={() => void handleExport("json")}>
                <Download className="h-3.5 w-3.5" />
                <span>导出 JSON</span>
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 3 Tier Tabs */}
      <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
        {TIER_TABS.map((tab) => {
          const active = activeTier === tab.id;
          const cnt = allReports.filter((r) => r.tier === tab.id).length;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTier(tab.id)}
              className={cn(
                "rounded-lg border p-3.5 text-left transition-colors",
                active
                  ? "border-blue-500 bg-blue-950/25"
                  : "border-border bg-card hover:border-slate-700"
              )}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-white">{tab.title}</span>
                <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  {cnt} 份报告
                </span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">{tab.subtitle}</p>
            </button>
          );
        })}
      </div>

      {/* Tier Execution Bar + Report Selector */}
      <section className="rounded-lg border border-border bg-card p-3.5 space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-end gap-2.5">
            {activeTier === "exchange_vs_local" && (
              <>
                <div className="space-y-1">
                  <label className="text-slate-400">实盘 Run ID (live_run_id)</label>
                  <input
                    type="text"
                    value={liveRunId}
                    onChange={(e) => setLiveRunId(e.target.value)}
                    placeholder="默认对齐最近活跃实盘实例"
                    className="h-8 w-60 rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                  />
                </div>
                <label className="flex h-8 items-center gap-1.5 rounded border border-slate-800 bg-slate-950 px-2.5 text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoHeal}
                    onChange={(e) => setAutoHeal(e.target.checked)}
                    className="accent-emerald-500"
                  />
                  <span>以交易所权威状态自动自愈修复本地账本 (auto_heal)</span>
                </label>
              </>
            )}

            {activeTier === "shadow_vs_live" && (
              <>
                <div className="space-y-1">
                  <label className="text-slate-400">影子 Run ID (shadow_run_id)</label>
                  <input
                    type="text"
                    value={shadowRunId}
                    onChange={(e) => setShadowRunId(e.target.value)}
                    className="h-8 w-56 rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-slate-400">实盘 Run ID (live_run_id)</label>
                  <input
                    type="text"
                    value={liveRunId}
                    onChange={(e) => setLiveRunId(e.target.value)}
                    className="h-8 w-56 rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                  />
                </div>
              </>
            )}

            {activeTier === "backtest_vs_shadow" && (
              <>
                <div className="space-y-1">
                  <label className="text-slate-400">回测 Run ID (backtest_run_id)</label>
                  <input
                    type="text"
                    value={backtestRunId}
                    onChange={(e) => setBacktestRunId(e.target.value)}
                    className="h-8 w-56 rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-slate-400">影子 Run ID (shadow_run_id)</label>
                  <input
                    type="text"
                    value={shadowRunId}
                    onChange={(e) => setShadowRunId(e.target.value)}
                    className="h-8 w-56 rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                  />
                </div>
              </>
            )}

            <Button
              size="default"
              variant="default"
              disabled={!canOperate || runReconMutation.isPending}
              onClick={() => runReconMutation.mutate()}
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>{runReconMutation.isPending ? "对账核对中..." : "执行当前层级对账"}</span>
            </Button>
          </div>

          {tierReports.length > 0 && (
            <div className="space-y-1">
              <label className="text-slate-400">切换历史对账报告</label>
              <select
                value={activeReport?.report_id ?? ""}
                onChange={(e) => setSelectedReportId(e.target.value)}
                className="h-8 rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
              >
                {tierReports.map((rep) => (
                  <option key={rep.report_id} value={rep.report_id}>
                    {rep.report_id} · {rep.passed ? "一致" : `${rep.discrepancy_count}项差异`} ·{" "}
                    {formatUtcTimestamp(rep.created_ts_ms, false)}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {runReconMutation.isError && (
          <ActionableErrorState
            title="触发对账任务失败"
            reasonCode={apiErr?.reasonCode ?? "RECONCILIATION_RUN_FAILED"}
            message={apiErr?.message ?? String(runReconMutation.error)}
            remediation="请确认对应的实盘/影子/回测 Run ID 已存在，或点击右上角「运行差异故障演练」快速验证 Tier-1 对账。"
          />
        )}
      </section>

      {/* Main Report View */}
      {reportsQuery.isLoading ? (
        <RunLoadingState
          title="正在加载三层对账报告与差异明细..."
          currentStageLabel="按严重程度聚合差异项并计算美元敞口影响"
          progressPct={65}
        />
      ) : !activeReport ? (
        <EmptyState
          title={`当前层级 (${activeTier}) 尚无对账报告`}
          description="您可以选择对应的运行记录执行对账，或点击下方按钮在真实数据库中执行一次故障注入对账演练（覆盖仓位不平、漏成交与资金费率差异）。"
          icon={<GitCompareArrows className="h-5 w-5 text-slate-400" />}
          primaryAction={
            canOperate
              ? {
                  label: "运行差异故障演练（仓位不平 + 漏成交 + 资金费差异）",
                  onClick: () => faultDrillMutation.mutate(),
                }
              : undefined
          }
        />
      ) : (
        <div className="space-y-4">
          {/* First-Glance Severity & Affected Money Summary */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiMetricCard
              label="当前报告差异影响总金额"
              timeWindow={formatUtcTimestamp(activeReport.created_ts_ms, false)}
              alertTone={
                Number(activeReport.total_abs_drift_usdt) > 0 ? "critical" : "none"
              }
              benchmarkLabel={`报告 ID: ${activeReport.report_id}`}
            >
              <NumericDisplay
                value={activeReport.total_abs_drift_usdt}
                mode="currency"
                className={
                  Number(activeReport.total_abs_drift_usdt) > 0
                    ? "text-rose-300"
                    : "text-emerald-400"
                }
              />
            </KpiMetricCard>

            <KpiMetricCard
              label="最高严重等级"
              timeWindow={activeReport.passed ? "核对通过" : "存在不一致"}
              benchmarkLabel={`总差异条数: ${activeReport.discrepancy_count} 项`}
            >
              <SeverityBadge severity={activeReport.max_severity} />
            </KpiMetricCard>

            <KpiMetricCard
              label="致命与高危差异 (CRITICAL / HIGH)"
              timeWindow="需立即人工核查"
              alertTone={
                (activeReport.severity_counts.CRITICAL ?? 0) +
                  (activeReport.severity_counts.HIGH ?? 0) >
                0
                  ? "critical"
                  : "none"
              }
              benchmarkLabel="仓位不平 · 漏成交 · 余额不一致"
            >
              <span className="font-mono text-rose-300">
                CRITICAL {activeReport.severity_counts.CRITICAL ?? 0} · HIGH{" "}
                {activeReport.severity_counts.HIGH ?? 0}
              </span>
            </KpiMetricCard>

            <KpiMetricCard
              label="中低风险偏差 (MEDIUM / LOW)"
              timeWindow="费用与滑点偏差"
              benchmarkLabel="资金费漏记 · 排队滑点偏移"
            >
              <span className="font-mono text-amber-300">
                MEDIUM {activeReport.severity_counts.MEDIUM ?? 0} · LOW{" "}
                {activeReport.severity_counts.LOW ?? 0}
              </span>
            </KpiMetricCard>
          </div>

          {activeReport.passed ? (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-950/20 p-4 text-xs text-emerald-200">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              <span>
                本次对账完全一致（0 项差异，影响金额 0.00 USDT）· 目标运行{" "}
                <code className="font-mono font-bold">{activeReport.run_id}</code>。
              </span>
            </div>
          ) : (
            <QuantDataTable
              tableId={`recon_${activeTier}`}
              data={activeReport.items}
              columns={discrepancyColumns}
              getRowId={(r, i) =>
                `${String(r.type ?? "d")}_${String(r.order_id ?? "")}_${String(
                  r.fill_id ?? ""
                )}_${i}`
              }
              renderExpandedRow={(item) => {
                const oid = item.order_id ? String(item.order_id) : "";
                const fid = item.fill_id ? String(item.fill_id) : "";
                const ts = Number(item.ts_ms ?? 0);
                return (
                  <div className="space-y-2.5 rounded border border-slate-800 bg-slate-900/70 p-3 text-xs">
                    <div className="grid grid-cols-2 gap-3 font-mono sm:grid-cols-4">
                      <div>
                        <span className="text-slate-400">关联委托 order_id: </span>
                        <strong className="text-white">{oid || "无（仓位/资金级）"}</strong>
                      </div>
                      <div>
                        <span className="text-slate-400">交易所单号: </span>
                        <strong className="text-sky-300">
                          {item.exchange_order_id ? String(item.exchange_order_id) : "—"}
                        </strong>
                      </div>
                      <div>
                        <span className="text-slate-400">成交/流水号 fill_id: </span>
                        <strong className="text-amber-300">{fid || "—"}</strong>
                      </div>
                      <div>
                        <span className="text-slate-400">精确毫秒时间戳: </span>
                        <strong className="text-white">
                          {ts > 0 ? `${ts} (${formatUtcTimestamp(ts, true)})` : "—"}
                        </strong>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 pt-2">
                      <div className="flex items-center gap-2">
                        <EnvBadge env={activeReport.env} />
                        <span className="font-mono text-[11px] text-slate-400">
                          所属运行: {activeReport.run_id}
                        </span>
                      </div>
                      <Link
                        href={`/ledger?env=${encodeURIComponent(
                          activeReport.env
                        )}&runId=${encodeURIComponent(activeReport.run_id)}${
                          oid ? `&q=${encodeURIComponent(oid)}` : ""
                        }`}
                      >
                        <Button size="sm" variant="secondary">
                          <span>下钻至统一账本核查原始记录</span>
                          <ExternalLink className="h-3.5 w-3.5" />
                        </Button>
                      </Link>
                    </div>
                  </div>
                );
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

export default function ReconciliationPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载三层对账看板...
        </div>
      }
    >
      <ReconciliationBoardInner />
    </Suspense>
  );
}
