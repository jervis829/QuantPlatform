"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  ArrowLeft,
  Code2,
  ExternalLink,
  History,
  Play,
  ScrollText,
  SlidersHorizontal,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ApprovalBadge,
  EnvBadge,
  RunStatusBadge,
} from "../../../components/quant/badges";
import { QuantDataTable } from "../../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../../components/quant/state-views";
import { StrategyCodeAndVersionPanel } from "../../../components/strategy/code-diff-viewer";
import {
  LiveThreeStepWizardModal,
  QuickBacktestModal,
  ShadowLaunchModal,
} from "../../../components/strategy/launch-modals";
import { SearchableLogPanel } from "../../../components/strategy/live-log-viewer";
import { SchemaParamEditor } from "../../../components/strategy/schema-param-editor";
import {
  BacktestActionButton,
  Button,
  IsolatedDangerZone,
  LiveDangerActionButton,
  ShadowActionButton,
} from "../../../components/ui/button";
import { QuantApiError, quantApi } from "../../../lib/apiClient";
import { formatUtcTimestamp } from "../../../lib/formatters";
import { usePreferences } from "../../../lib/preferences-context";
import { cn } from "../../../lib/utils";
import type {
  RunSummaryItem,
  StrategyVersionResponse,
} from "../../../types/openapi.generated";

type DetailSection = "config" | "code" | "runs" | "logs";
type RunEnvCategory = "all" | "backtest" | "shadow" | "live";

function StrategyDetailInner() {
  const params = useParams<{ strategyId: string }>();
  const strategyId = decodeURIComponent(String(params?.strategyId ?? ""));
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { apiKey, roleLabel } = usePreferences();

  const initialSection = (searchParams.get("section") as DetailSection) || "config";
  const [activeSection, setActiveSection] = useState<DetailSection>(
    ["config", "code", "runs", "logs"].includes(initialSection)
      ? initialSection
      : "config"
  );
  const [runEnvFilter, setRunEnvFilter] = useState<RunEnvCategory>("all");

  const [selectedVersionId, setSelectedVersionId] = useState<string>("");
  const [btModalOpen, setBtModalOpen] = useState(false);
  const [shModalOpen, setShModalOpen] = useState(false);
  const [liveWizardOpen, setLiveWizardOpen] = useState(false);

  const canEdit = roleLabel !== "VIEWER";
  const canApprove = roleLabel === "OPERATOR" || roleLabel === "ADMIN";

  const detailQuery = useQuery({
    queryKey: ["strategy-detail", strategyId, apiKey],
    queryFn: () => quantApi.getStrategyDetail(apiKey, strategyId),
    enabled: Boolean(strategyId),
  });

  const runsQuery = useQuery({
    queryKey: ["runs", "by-strategy", strategyId, apiKey],
    queryFn: () => quantApi.listRuns(apiKey, { strategy_id: strategyId, limit: 200 }),
    enabled: Boolean(strategyId),
  });

  const detail = detailQuery.data;

  useEffect(() => {
    if (detail && !selectedVersionId) {
      setSelectedVersionId(detail.latest_version.version_id);
    }
  }, [detail, selectedVersionId]);

  const selectedVersion: StrategyVersionResponse | null = useMemo(() => {
    if (!detail) return null;
    return (
      detail.versions.find((v) => v.version_id === selectedVersionId) ??
      detail.latest_version
    );
  }, [detail, selectedVersionId]);

  const switchSection = (sec: DetailSection) => {
    setActiveSection(sec);
    const sp = new URLSearchParams(window.location.search);
    sp.set("section", sec);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };

  const paramSaveMutation = useMutation({
    mutationFn: (nextParams: Record<string, unknown>) =>
      quantApi.updateStrategyParams(apiKey, strategyId, { params: nextParams }),
    onSuccess: (newVer) => {
      setSelectedVersionId(newVer.version_id);
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
    },
  });

  const approveMutation = useMutation({
    mutationFn: ({ versionId, note }: { versionId: string; note: string }) =>
      quantApi.approveVersion(apiKey, versionId, { note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ versionId, note }: { versionId: string; note: string }) =>
      quantApi.rejectVersion(apiKey, versionId, { note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
    },
  });

  const allRuns = runsQuery.data ?? [];
  const runCounts = useMemo(
    () => ({
      all: allRuns.length,
      backtest: allRuns.filter((r) => r.env === "backtest").length,
      shadow: allRuns.filter((r) => r.env === "shadow").length,
      live: allRuns.filter((r) => r.env === "live").length,
    }),
    [allRuns]
  );

  const filteredRuns = useMemo(
    () =>
      runEnvFilter === "all"
        ? allRuns
        : allRuns.filter((r) => r.env === runEnvFilter),
    [allRuns, runEnvFilter]
  );

  const runColumns = useMemo<ColumnDef<RunSummaryItem, unknown>[]>(
    () => [
      {
        id: "env",
        accessorKey: "env",
        header: "环境",
        cell: ({ row }) => (
          <EnvBadge env={row.original.env} pulse={row.original.status === "RUNNING"} />
        ),
      },
      {
        id: "run_id",
        accessorKey: "run_id",
        header: "运行编号",
        cell: ({ row }) => (
          <code className="font-mono text-xs text-slate-200">
            {row.original.run_id.slice(0, 22)}...
          </code>
        ),
      },
      {
        id: "version",
        accessorKey: "strategy_version_id",
        header: "策略版本",
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-300">
            {row.original.strategy_version_id}
          </span>
        ),
      },
      {
        id: "status",
        accessorKey: "status",
        header: "运行状态",
        cell: ({ row }) => <RunStatusBadge status={row.original.status} />,
      },
      {
        id: "return",
        accessorFn: (r) => r.cumulative_return ?? -999,
        header: "累计收益",
        meta: { align: "right", mono: true },
        cell: ({ row }) =>
          row.original.cumulative_return !== null &&
          row.original.cumulative_return !== undefined ? (
            <NumericDisplay
              value={row.original.cumulative_return}
              mode="ratioPercent"
              coloredBySign
              showPlusSign
            />
          ) : (
            <span className="text-slate-500">—</span>
          ),
      },
      {
        id: "fills",
        accessorFn: (r) => r.fill_count ?? 0,
        header: "成交笔数",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{row.original.fill_count ?? 0}</span>,
      },
      {
        id: "started_at",
        accessorKey: "started_at_ms",
        header: "启动时间 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-400">
            {formatUtcTimestamp(row.original.started_at_ms, false)}
          </span>
        ),
      },
      {
        id: "links",
        header: "下钻入口",
        enableSorting: false,
        meta: { align: "right" },
        cell: ({ row }) => {
          const r = row.original;
          return (
            <div
              className="inline-flex items-center gap-1.5"
              onClick={(e) => e.stopPropagation()}
            >
              {r.env === "backtest" && (
                <Link href={`/reports/${encodeURIComponent(r.run_id)}`}>
                  <Button size="sm" variant="secondary">
                    <span>报告</span>
                    <ExternalLink className="h-3 w-3" />
                  </Button>
                </Link>
              )}
              <Link
                href={`/ledger?env=${encodeURIComponent(r.env)}&runId=${encodeURIComponent(r.run_id)}`}
              >
                <Button size="sm" variant="outline">
                  账本明细
                </Button>
              </Link>
            </div>
          );
        },
      },
    ],
    []
  );

  if (detailQuery.isLoading) {
    return (
      <RunLoadingState
        title={`正在加载策略详情 (${strategyId})`}
        currentStageLabel="拉取版本树、Pydantic 参数 Schema 与三环境运行历史"
        progressPct={65}
      />
    );
  }

  if (detailQuery.isError || !detail || !selectedVersion) {
    const apiErr =
      detailQuery.error instanceof QuantApiError ? detailQuery.error : null;
    return (
      <ActionableErrorState
        title={`无法加载策略 ${strategyId}`}
        reasonCode={apiErr?.reasonCode ?? "STRATEGY_NOT_FOUND"}
        message={apiErr?.message ?? String(detailQuery.error)}
        remediation="请确认策略 ID 是否正确，或返回策略工作台选择其它策略。"
        rawDetail={apiErr?.rawDetail}
        actions={[
          {
            label: "返回策略巡检工作台",
            onClick: () => router.push("/strategies"),
          },
        ]}
      />
    );
  }

  const { meta } = detail;
  const btMetrics = meta.latest_backtest_metrics;
  const latestRunStatus = allRuns[0]?.status ?? "IDLE";

  return (
    <div className="space-y-5 pb-10">
      {/* ===================================================================== */}
      {/* ABOVE-THE-FOLD ZONE 1: OVERVIEW (Env + Run Status + Core Metrics)     */}
      {/* ===================================================================== */}
      <section className="rounded-lg border border-border bg-card p-4 space-y-3.5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Link
                href="/strategies"
                className="inline-flex items-center gap-1 hover:text-white"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                <span>返回我的策略</span>
              </Link>
              <span>/</span>
              <code className="font-mono text-slate-300">{meta.strategy_id}</code>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-lg font-bold text-white">{meta.name}</h1>
              <ApprovalBadge status={selectedVersion.approval_status} />
              <RunStatusBadge status={latestRunStatus} />
              {(meta.active_envs ?? []).map((e) => (
                <EnvBadge key={e} env={e} pulse={e === "live"} />
              ))}
            </div>
            {meta.description && (
              <p className="text-xs text-muted-foreground">{meta.description}</p>
            )}
          </div>

          {/* Version Switcher right in Overview Header */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">当前版本:</span>
            <select
              value={selectedVersion.version_id}
              onChange={(e) => setSelectedVersionId(e.target.value)}
              className="h-8 rounded border border-border bg-slate-950 px-2.5 text-xs text-white focus:border-blue-500 focus:outline-none"
            >
              {[...detail.versions]
                .sort((a, b) => b.version - a.version)
                .map((v) => (
                  <option key={v.version_id} value={v.version_id}>
                    第 {v.version} 版 (v{v.version} ·{" "}
                    {v.approval_status === "APPROVED"
                      ? "已审核"
                      : v.approval_status === "REJECTED"
                        ? "已退回"
                        : "待审核"}
                    )
                  </option>
                ))}
            </select>
          </div>
        </div>

        {/* Simple, Actionable Approval Banner if Unapproved */}
        {selectedVersion.approval_status !== "APPROVED" && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-500/45 bg-amber-950/25 px-3 py-2 text-xs text-amber-200">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />
              <span>
                当前版本 <strong className="font-mono">v{selectedVersion.version}</strong> 尚未审核：您可以随时免费跑<strong>历史回测</strong>；确认策略无误后点击右侧「确认审核通过」，即可解锁模拟盘与实盘。
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => switchSection("code")}
              >
                查看策略代码
              </Button>
              {canApprove && (
                <Button
                  size="sm"
                  variant="shadow"
                  disabled={approveMutation.isPending}
                  onClick={() =>
                    approveMutation.mutate({
                      versionId: selectedVersion.version_id,
                      note: "人工确认审核通过",
                    })
                  }
                >
                  {approveMutation.isPending ? "正在确认..." : "✓ 确认审核通过"}
                </Button>
              )}
            </div>
          </div>
        )}

        {/* First-Glance KPI Cards (4 Core Metrics Above the Fold) */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <KpiMetricCard
            label="最近回测收益率"
            timeWindow="历史验证"
            benchmarkLabel={btMetrics ? "点击右侧按钮可看 K 线" : "尚未跑过回测"}
          >
            {btMetrics ? (
              <NumericDisplay
                value={btMetrics.cumulative_return}
                mode="ratioPercent"
                coloredBySign
                showPlusSign
              />
            ) : (
              <span className="text-sm text-slate-500">未回测</span>
            )}
          </KpiMetricCard>

          <KpiMetricCard
            label="最大回撤 / 夏普"
            timeWindow="风险收益比"
            benchmarkLabel={btMetrics ? `夏普比率: ${btMetrics.sharpe.toFixed(2)}` : "—"}
          >
            {btMetrics ? (
              <span className="font-mono text-rose-300">
                {(Math.abs(btMetrics.max_drawdown) * -100).toFixed(2)}%
              </span>
            ) : (
              <span className="text-sm text-slate-500">—</span>
            )}
          </KpiMetricCard>

          <KpiMetricCard
            label="今日实盘盈亏"
            timeWindow="近 24 小时"
            benchmarkLabel="实盘已实现收益"
          >
            <NumericDisplay
              value={meta.live_today_pnl_usdt ?? "0"}
              mode="currency"
              coloredBySign
              showPlusSign
            />
          </KpiMetricCard>

          <KpiMetricCard
            label="累计运行次数"
            timeWindow="全部记录"
            benchmarkLabel={`回测 ${runCounts.backtest} · 模拟 ${runCounts.shadow} · 实盘 ${runCounts.live}`}
            onClick={() => switchSection("runs")}
          >
            <span className="font-mono">{runCounts.all} 次</span>
          </KpiMetricCard>
        </div>
      </section>

      {/* ===================================================================== */}
      {/* ABOVE-THE-FOLD ZONE 2: ACTION ZONE (Physically Separated by Danger)   */}
      {/* ===================================================================== */}
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-12">
        {/* Safe & Caution Research Zone (7 cols) */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-3.5 lg:col-span-7">
          <div>
            <div className="text-xs font-semibold text-slate-100">
              验证策略（零真实资金风险）
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              先跑「历史回测」看过去收益与 K 线买卖点，再用「模拟盘」接入实时行情演练。
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {btMetrics && (
              <Link href={`/reports/${encodeURIComponent(btMetrics.run_id)}`}>
                <Button variant="outline">
                  <span>查看回测K线</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </Button>
              </Link>
            )}
            <BacktestActionButton onClick={() => setBtModalOpen(true)}>
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>跑历史回测</span>
            </BacktestActionButton>
            <ShadowActionButton onClick={() => setShModalOpen(true)}>
              <span>启动模拟盘</span>
            </ShadowActionButton>
          </div>
        </div>

        {/* Physically Isolated Live Capital Danger Zone (5 cols) */}
        <div className="lg:col-span-5">
          <IsolatedDangerZone
            title="实盘交易区（真金白银）"
            subtitle="需完成账户确认 → 保证金与杠杆检查 → 输入策略名三步安全核验。"
          >
            <LiveDangerActionButton onClick={() => setLiveWizardOpen(true)}>
              启动实盘交易
            </LiveDangerActionButton>
          </IsolatedDangerZone>
        </div>
      </section>

      {/* ===================================================================== */}
      {/* DEEP WORKSPACE ZONES: CONFIG · CODE · RUNS · LOGS                     */}
      {/* ===================================================================== */}
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-1 border-b border-border pb-2">
          {(
            [
              {
                id: "config",
                label: "调整参数",
                icon: SlidersHorizontal,
              },
              {
                id: "code",
                label: `策略代码与版本 (${detail.versions.length})`,
                icon: Code2,
              },
              {
                id: "runs",
                label: `运行记录 (${runCounts.all})`,
                icon: History,
              },
              {
                id: "logs",
                label: "运行日志",
                icon: ScrollText,
              },
            ] as Array<{ id: DetailSection; label: string; icon: React.ComponentType<{ className?: string }> }>
          ).map((tab) => {
            const Icon = tab.icon;
            const active = activeSection === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => switchSection(tab.id)}
                className={cn(
                  "inline-flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium transition-colors",
                  active
                    ? "bg-blue-600 text-white shadow-sm"
                    : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Zone 3: Config (Schema Form + Live Diff against Saved Version) */}
        {activeSection === "config" && (
          <SchemaParamEditor
            strategyId={meta.strategy_id}
            versionNumber={selectedVersion.version}
            schema={selectedVersion.param_schema}
            savedParams={selectedVersion.params}
            canEdit={canEdit}
            isSaving={paramSaveMutation.isPending}
            onSaveNewVersion={async (nextParams) => {
              await paramSaveMutation.mutateAsync(nextParams);
            }}
          />
        )}

        {/* Zone 4: Code (Read-only + Version History + Diff + Manual Approval) */}
        {activeSection === "code" && (
          <StrategyCodeAndVersionPanel
            versions={detail.versions}
            selectedVersion={selectedVersion}
            onSelectVersion={(v) => setSelectedVersionId(v.version_id)}
            canApprove={canApprove}
            isApproving={approveMutation.isPending || rejectMutation.isPending}
            onApprove={async (versionId, note) => {
              await approveMutation.mutateAsync({ versionId, note });
            }}
            onReject={async (versionId, note) => {
              await rejectMutation.mutateAsync({ versionId, note });
            }}
          />
        )}

        {/* Zone 5: Runs (Categorized by Backtest · Shadow · Live) */}
        {activeSection === "runs" && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="inline-flex rounded-md border border-border bg-slate-950 p-1">
                {(
                  [
                    ["all", `全部环境 (${runCounts.all})`],
                    ["backtest", `回测 Backtest (${runCounts.backtest})`],
                    ["shadow", `影子 Shadow (${runCounts.shadow})`],
                    ["live", `实盘 Live (${runCounts.live})`],
                  ] as Array<[RunEnvCategory, string]>
                ).map(([cat, label]) => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setRunEnvFilter(cat)}
                    className={cn(
                      "rounded px-3 py-1 text-xs font-medium transition-colors",
                      runEnvFilter === cat
                        ? "bg-blue-600 text-white"
                        : "text-slate-400 hover:text-slate-200"
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {allRuns.length === 0 ? (
              <EmptyState
                title="该策略尚无任何环境运行记录"
                description="建议先点击上方「发起回测」生成首次回测报告与买卖信号标记。"
                primaryAction={{
                  label: "立即发起首次回测",
                  onClick: () => setBtModalOpen(true),
                }}
              />
            ) : (
              <QuantDataTable
                tableId={`runs_${meta.strategy_id}`}
                data={filteredRuns}
                columns={runColumns}
                getRowId={(r) => `${r.env}_${r.run_id}`}
                renderExpandedRow={(r) => (
                  <div className="grid grid-cols-2 gap-3 font-mono text-xs sm:grid-cols-4">
                    <div>
                      <span className="text-slate-400">完整 Run ID: </span>
                      <span className="break-all text-white">{r.run_id}</span>
                    </div>
                    <div>
                      <span className="text-slate-400">夏普比率: </span>
                      <span className="text-white">
                        {r.sharpe !== null && r.sharpe !== undefined
                          ? r.sharpe.toFixed(3)
                          : "—"}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400">最大回撤: </span>
                      <span className="text-rose-300">
                        {r.max_drawdown !== null && r.max_drawdown !== undefined
                          ? `${(Math.abs(r.max_drawdown) * -100).toFixed(2)}%`
                          : "—"}
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400">结束时间: </span>
                      <span className="text-white">
                        {r.ended_at_ms ? formatUtcTimestamp(r.ended_at_ms, false) : "运行中"}
                      </span>
                    </div>
                  </div>
                )}
              />
            )}
          </div>
        )}

        {/* Zone 6: Logs (Searchable + Pauseable Auto-scroll) */}
        {activeSection === "logs" && (
          <SearchableLogPanel strategyId={meta.strategy_id} runs={detail.runs} />
        )}
      </div>

      {/* Environment Launch Modals (Graded by Danger Tier) */}
      <QuickBacktestModal
        open={btModalOpen}
        onOpenChange={setBtModalOpen}
        strategyId={meta.strategy_id}
        strategyName={meta.name}
        versionId={selectedVersion.version_id}
      />

      <ShadowLaunchModal
        open={shModalOpen}
        onOpenChange={setShModalOpen}
        strategyId={meta.strategy_id}
        strategyName={meta.name}
        versionId={selectedVersion.version_id}
        approvalStatus={selectedVersion.approval_status}
        onJumpToApproval={() => switchSection("code")}
      />

      <LiveThreeStepWizardModal
        open={liveWizardOpen}
        onOpenChange={setLiveWizardOpen}
        strategyId={meta.strategy_id}
        strategyName={meta.name}
        versionId={selectedVersion.version_id}
        versionParams={selectedVersion.params}
        onJumpToApproval={() => switchSection("code")}
      />
    </div>
  );
}

export default function StrategyDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载策略详情...
        </div>
      }
    >
      <StrategyDetailInner />
    </Suspense>
  );
}
