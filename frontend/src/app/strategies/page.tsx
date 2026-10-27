"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Code2,
  Play,
  Plus,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApprovalBadge, EnvBadge } from "../../components/quant/badges";
import { QuantDataTable } from "../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../components/quant/state-views";
import { CreateStrategyModal } from "../../components/strategy/create-strategy-drawer";
import { QuickBacktestModal } from "../../components/strategy/launch-modals";
import { BacktestActionButton, Button } from "../../components/ui/button";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";
import type { StrategySummaryItem } from "../../types/openapi.generated";

type PatrolFilter = "ALL" | "NEEDS_ACTION" | "LIVE_ACTIVE" | "APPROVED_READY";

function needsOperatorIntervention(s: StrategySummaryItem): {
  needed: boolean;
  reason: string;
  tone: "warning" | "danger" | "ok";
} {
  const status = (s.latest_approval_status || "PENDING").toUpperCase();
  if (status === "REJECTED") {
    return { needed: true, reason: "版本已驳回 · 需修订", tone: "danger" };
  }
  if (status === "PENDING") {
    return { needed: true, reason: "待人工审批 (红线8)", tone: "warning" };
  }
  if (!s.latest_backtest_metrics) {
    return { needed: true, reason: "尚未执行回测验证", tone: "warning" };
  }
  return { needed: false, reason: "已审批且回测就绪", tone: "ok" };
}

function StrategiesWorkbenchInner() {
  const router = useRouter();
  const { apiKey } = usePreferences();

  const [patrolFilter, setPatrolFilter] = useState<PatrolFilter>("ALL");
  const [createOpen, setCreateOpen] = useState(false);
  const [createDefaultMode, setCreateDefaultMode] = useState<"python" | "nl">("python");
  const [quickBtTarget, setQuickBtTarget] = useState<{
    strategyId: string;
    name: string;
    versionId: string;
  } | null>(null);

  const strategiesQuery = useQuery({
    queryKey: ["strategies", apiKey],
    queryFn: () => quantApi.listStrategies(apiKey),
  });

  const allStrategies = strategiesQuery.data ?? [];

  const summary = useMemo(() => {
    let pendingOrActionCount = 0;
    let liveActiveCount = 0;
    let nlCount = 0;
    let totalLivePnl = 0;

    allStrategies.forEach((s) => {
      if (needsOperatorIntervention(s).needed) pendingOrActionCount += 1;
      if ((s.active_envs ?? []).includes("live") || (s.run_counts?.live ?? 0) > 0) {
        liveActiveCount += 1;
      }
      if (s.source_type === "NL_GENERATED") nlCount += 1;
      const pnlNum = Number(s.live_today_pnl_usdt ?? "0");
      if (Number.isFinite(pnlNum)) totalLivePnl += pnlNum;
    });

    return {
      total: allStrategies.length,
      nlCount,
      pythonCount: allStrategies.length - nlCount,
      pendingOrActionCount,
      liveActiveCount,
      totalLivePnl,
    };
  }, [allStrategies]);

  const filteredStrategies = useMemo(() => {
    return allStrategies.filter((s) => {
      if (patrolFilter === "NEEDS_ACTION") {
        return needsOperatorIntervention(s).needed;
      }
      if (patrolFilter === "LIVE_ACTIVE") {
        return (s.active_envs ?? []).includes("live") || (s.run_counts?.live ?? 0) > 0;
      }
      if (patrolFilter === "APPROVED_READY") {
        return (s.latest_approval_status || "").toUpperCase() === "APPROVED";
      }
      return true;
    });
  }, [allStrategies, patrolFilter]);

  const columns = useMemo<ColumnDef<StrategySummaryItem, unknown>[]>(
    () => [
      {
        id: "strategy",
        accessorFn: (r) => `${r.name} ${r.strategy_id}`,
        header: "策略名称 / ID",
        cell: ({ row }) => {
          const s = row.original;
          const isNl = s.source_type === "NL_GENERATED";
          return (
            <div className="space-y-0.5">
              <div className="flex items-center gap-2">
                <Link
                  href={`/strategies/${encodeURIComponent(s.strategy_id)}`}
                  onClick={(e) => e.stopPropagation()}
                  className="font-semibold text-white hover:text-blue-400 hover:underline"
                >
                  {s.name}
                </Link>
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px]",
                    isNl
                      ? "bg-violet-500/20 text-violet-300"
                      : "bg-slate-800 text-slate-300"
                  )}
                >
                  {isNl ? "NL生成" : "Python"}
                </span>
              </div>
              <div className="font-mono text-[11px] text-slate-400">{s.strategy_id}</div>
            </div>
          );
        },
      },
      {
        id: "version_approval",
        accessorFn: (r) => `${r.current_version}_${r.latest_approval_status ?? "PENDING"}`,
        header: "最新版本与审批门禁",
        cell: ({ row }) => {
          const s = row.original;
          return (
            <div className="flex items-center gap-2">
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs font-bold text-slate-200">
                v{s.current_version}
              </span>
              <ApprovalBadge status={s.latest_approval_status ?? "PENDING"} />
            </div>
          );
        },
      },
      {
        id: "envs",
        accessorFn: (r) =>
          (r.run_counts?.live ?? 0) * 1000 +
          (r.run_counts?.shadow ?? 0) * 100 +
          (r.run_counts?.backtest ?? 0),
        header: "三环境运行分布",
        cell: ({ row }) => {
          const s = row.original;
          const bt = s.run_counts?.backtest ?? 0;
          const sh = s.run_counts?.shadow ?? 0;
          const lv = s.run_counts?.live ?? 0;
          const activeEnvs = s.active_envs ?? [];
          return (
            <div className="space-y-1">
              <div className="flex flex-wrap items-center gap-1">
                {activeEnvs.length > 0 ? (
                  activeEnvs.map((e) => <EnvBadge key={e} env={e} pulse={e === "live"} />)
                ) : (
                  <span className="text-[11px] text-slate-500">当前无常驻实例</span>
                )}
              </div>
              <div className="font-mono text-[10px] text-slate-400">
                历史: BT {bt} · SH {sh} · LIVE {lv}
              </div>
            </div>
          );
        },
      },
      {
        id: "bt_return",
        accessorFn: (r) => r.latest_backtest_metrics?.cumulative_return ?? -999,
        header: "近期回测收益",
        meta: { align: "right", mono: true },
        cell: ({ row }) => {
          const m = row.original.latest_backtest_metrics;
          if (!m) return <span className="text-slate-500">未回测</span>;
          return (
            <NumericDisplay
              value={m.cumulative_return}
              mode="ratioPercent"
              coloredBySign
              showPlusSign
              className="font-semibold"
            />
          );
        },
      },
      {
        id: "bt_risk",
        accessorFn: (r) => r.latest_backtest_metrics?.sharpe ?? -999,
        header: "最大回撤 / 夏普",
        meta: { align: "right", mono: true },
        cell: ({ row }) => {
          const m = row.original.latest_backtest_metrics;
          if (!m) return <span className="text-slate-500">—</span>;
          return (
            <div className="space-y-0.5">
              <div className="text-rose-300">
                {(Math.abs(m.max_drawdown) * -100).toFixed(2)}%
              </div>
              <div className="text-[10px] text-slate-400">
                Sharpe {m.sharpe.toFixed(2)}
              </div>
            </div>
          );
        },
      },
      {
        id: "live_pnl",
        accessorFn: (r) => Number(r.live_today_pnl_usdt ?? 0),
        header: "今日实盘盈亏",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.live_today_pnl_usdt ?? "0"}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        ),
      },
      {
        id: "intervention",
        accessorFn: (r) => (needsOperatorIntervention(r).needed ? 1 : 0),
        header: "巡检诊断 (是否需介入)",
        cell: ({ row }) => {
          const diag = needsOperatorIntervention(row.original);
          return (
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium",
                diag.tone === "danger"
                  ? "bg-rose-500/20 text-rose-300"
                  : diag.tone === "warning"
                    ? "bg-amber-500/20 text-amber-300"
                    : "bg-emerald-500/15 text-emerald-300"
              )}
            >
              {diag.needed ? (
                <AlertCircle className="h-3 w-3 shrink-0" />
              ) : (
                <CheckCircle2 className="h-3 w-3 shrink-0" />
              )}
              <span>{diag.reason}</span>
            </span>
          );
        },
      },
      {
        id: "actions",
        header: "安全快捷操作",
        enableSorting: false,
        meta: { align: "right" },
        cell: ({ row }) => {
          const s = row.original;
          const latestVersionId = `${s.strategy_id}:v${s.current_version}`;
          return (
            <div
              className="inline-flex items-center justify-end gap-1.5"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Click 1 -> Opens 1-screen QuickBacktestModal; Click 2 -> Immediate Backtest! (<= 3 clicks) */}
              <BacktestActionButton
                size="sm"
                onClick={() =>
                  setQuickBtTarget({
                    strategyId: s.strategy_id,
                    name: s.name,
                    versionId: latestVersionId,
                  })
                }
              >
                <Play className="h-3 w-3 fill-current" />
                <span>快速回测</span>
              </BacktestActionButton>
              <Link href={`/strategies/${encodeURIComponent(s.strategy_id)}`}>
                <Button size="sm" variant="outline">
                  <span>详情</span>
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
          );
        },
      },
    ],
    []
  );

  const apiErr =
    strategiesQuery.error instanceof QuantApiError ? strategiesQuery.error : null;

  return (
    <div className="space-y-5">
      {/* Top Header: Title + Primary Strategy Registration Actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">策略巡检工作台</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            10 秒快速巡检全部策略状态、近期回测表现与待审批项；行内仅保留安全回测入口，实盘操作严格隔离于策略详情危险区。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="default"
            onClick={() => {
              setCreateDefaultMode("nl");
              setCreateOpen(true);
            }}
          >
            <Sparkles className="h-3.5 w-3.5 text-violet-400" />
            <span>自然语言生成策略 (NL)</span>
          </Button>
          <Button
            variant="default"
            size="default"
            onClick={() => {
              setCreateDefaultMode("python");
              setCreateOpen(true);
            }}
          >
            <Plus className="h-4 w-4" />
            <span>上传 Python 策略</span>
          </Button>
        </div>
      </div>

      {/* First-Glance Patrol KPI Layer (4 Cards) */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KpiMetricCard
          label="已注册策略总数"
          timeWindow="全平台版本库"
          benchmarkLabel={`Python ${summary.pythonCount} · NL生成 ${summary.nlCount}`}
        >
          <span className="font-mono">{summary.total}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="需人工介入策略数"
          timeWindow="审批门禁 / 未回测"
          alertTone={summary.pendingOrActionCount > 0 ? "warning" : "none"}
          benchmarkLabel="点击下方「需人工介入」一键聚焦"
          onClick={() =>
            setPatrolFilter((f) => (f === "NEEDS_ACTION" ? "ALL" : "NEEDS_ACTION"))
          }
        >
          <span
            className={cn(
              "font-mono",
              summary.pendingOrActionCount > 0 ? "text-amber-300" : "text-emerald-400"
            )}
          >
            {summary.pendingOrActionCount}
          </span>
        </KpiMetricCard>

        <KpiMetricCard
          label="涉及实盘记录策略数"
          timeWindow="LIVE 环境"
          benchmarkLabel="点击筛选实盘策略"
          onClick={() =>
            setPatrolFilter((f) => (f === "LIVE_ACTIVE" ? "ALL" : "LIVE_ACTIVE"))
          }
        >
          <span className="font-mono text-rose-300">{summary.liveActiveCount}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="今日策略实盘净盈亏合计"
          timeWindow="UTC 24H"
          benchmarkLabel="统一账本已实现归因"
        >
          <NumericDisplay
            value={summary.totalLivePnl}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        </KpiMetricCard>
      </div>

      {/* Main Content: Tri-State Handling + QuantDataTable */}
      {strategiesQuery.isLoading ? (
        <RunLoadingState
          title="正在拉取策略注册表与近期表现指标..."
          currentStageLabel="聚合各版本审批状态、运行分布与最近回测绩效"
          progressPct={60}
        />
      ) : strategiesQuery.isError ? (
        <ActionableErrorState
          title="加载策略列表失败"
          reasonCode={apiErr?.reasonCode ?? "STRATEGIES_FETCH_FAILED"}
          message={apiErr?.message ?? String(strategiesQuery.error)}
          remediation="请确认后端 API 服务运行正常且当前 RBAC 令牌有效。"
          rawDetail={apiErr?.rawDetail}
          actions={[
            {
              label: "立即重试",
              onClick: () => void strategiesQuery.refetch(),
            },
          ]}
        />
      ) : allStrategies.length === 0 ? (
        <EmptyState
          title="当前尚无已注册的交易策略"
          description="您可以直接上传继承自 Strategy 的 Python 源码（经受限 AST 与无网络沙箱校验），或用自然语言描述逻辑生成策略并自动执行回测门禁。"
          icon={<Code2 className="h-5 w-5 text-slate-400" />}
          primaryAction={{
            label: "上传首个 Python 策略",
            onClick: () => {
              setCreateDefaultMode("python");
              setCreateOpen(true);
            },
          }}
          secondaryAction={{
            label: "使用自然语言生成策略 (NL)",
            variant: "secondary",
            onClick: () => {
              setCreateDefaultMode("nl");
              setCreateOpen(true);
            },
          }}
        />
      ) : (
        <QuantDataTable
          tableId="strategies"
          data={filteredStrategies}
          columns={columns}
          getRowId={(r) => r.strategy_id}
          onRowClick={(s) =>
            router.push(`/strategies/${encodeURIComponent(s.strategy_id)}`)
          }
          toolbarExtra={
            <div className="inline-flex rounded border border-border bg-slate-950 p-0.5">
              {(
                [
                  ["ALL", `全部 (${allStrategies.length})`],
                  ["NEEDS_ACTION", `需人工介入 (${summary.pendingOrActionCount})`],
                  ["APPROVED_READY", "已审批就绪"],
                  ["LIVE_ACTIVE", `实盘相关 (${summary.liveActiveCount})`],
                ] as Array<[PatrolFilter, string]>
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setPatrolFilter(id)}
                  className={cn(
                    "rounded px-2.5 py-0.5 text-[11px] transition-colors",
                    patrolFilter === id
                      ? "bg-blue-600 text-white font-semibold"
                      : "text-slate-400 hover:text-slate-200"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          }
          renderExpandedRow={(s) => (
            <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-4">
              <div>
                <div className="text-slate-400">策略描述</div>
                <div className="mt-0.5 text-slate-200">{s.description || "无描述"}</div>
              </div>
              <div>
                <div className="text-slate-400">创建人 / 最近更新</div>
                <div className="mt-0.5 font-mono text-slate-200">
                  {s.created_by} · {formatUtcTimestamp(s.updated_at_ms, false)}
                </div>
              </div>
              <div>
                <div className="text-slate-400">最近回测 Run ID</div>
                <div className="mt-0.5 font-mono text-sky-300">
                  {s.latest_backtest_metrics ? (
                    <Link
                      href={`/reports/${encodeURIComponent(s.latest_backtest_metrics.run_id)}`}
                      className="hover:underline"
                    >
                      {s.latest_backtest_metrics.run_id.slice(0, 24)}...
                    </Link>
                  ) : (
                    "暂无回测记录"
                  )}
                </div>
              </div>
              <div className="flex items-center justify-end">
                <Link href={`/strategies/${encodeURIComponent(s.strategy_id)}`}>
                  <Button size="sm" variant="secondary">
                    进入详情配置参数与查看源码 →
                  </Button>
                </Link>
              </div>
            </div>
          )}
        />
      )}

      {/* Modals */}
      <CreateStrategyModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        defaultMode={createDefaultMode}
      />

      {quickBtTarget && (
        <QuickBacktestModal
          open={Boolean(quickBtTarget)}
          onOpenChange={(o) => {
            if (!o) setQuickBtTarget(null);
          }}
          strategyId={quickBtTarget.strategyId}
          strategyName={quickBtTarget.name}
          versionId={quickBtTarget.versionId}
        />
      )}
    </div>
  );
}

export default function StrategiesWorkbenchPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载策略工作台...
        </div>
      }
    >
      <StrategiesWorkbenchInner />
    </Suspense>
  );
}
