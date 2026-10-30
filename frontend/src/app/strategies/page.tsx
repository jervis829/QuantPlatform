"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Code2,
  LineChart,
  Play,
  Plus,
  Sparkles,
  X,
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
    return { needed: true, reason: "已退回 · 需修改参数或代码", tone: "danger" };
  }
  if (!s.latest_backtest_metrics) {
    return { needed: true, reason: "建议先跑一次回测", tone: "warning" };
  }
  if (status === "PENDING") {
    return { needed: true, reason: "待审核（上实盘前需确认）", tone: "warning" };
  }
  return { needed: false, reason: "状态就绪", tone: "ok" };
}

function StrategiesWorkbenchInner() {
  const router = useRouter();
  const { apiKey } = usePreferences();

  const [patrolFilter, setPatrolFilter] = useState<PatrolFilter>("ALL");
  const [showGuide, setShowGuide] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [createDefaultMode, setCreateDefaultMode] = useState<"python" | "nl">("nl");
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
        header: "策略名称",
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
                    "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]",
                    isNl
                      ? "bg-violet-500/20 text-violet-300"
                      : "bg-slate-800 text-slate-300"
                  )}
                >
                  {isNl ? "一句话生成" : "Python"}
                </span>
                <span className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  v{s.current_version}
                </span>
              </div>
              {s.description && (
                <div className="max-w-xs truncate text-[11px] text-slate-400">
                  {s.description}
                </div>
              )}
            </div>
          );
        },
      },
      {
        id: "bt_return",
        accessorFn: (r) => r.latest_backtest_metrics?.cumulative_return ?? -999,
        header: "最近回测收益",
        meta: { align: "right", mono: true },
        cell: ({ row }) => {
          const m = row.original.latest_backtest_metrics;
          if (!m) return <span className="text-slate-500">尚未回测</span>;
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
                夏普 {m.sharpe.toFixed(2)}
              </div>
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
        header: "运行记录",
        cell: ({ row }) => {
          const s = row.original;
          const bt = s.run_counts?.backtest ?? 0;
          const sh = s.run_counts?.shadow ?? 0;
          const lv = s.run_counts?.live ?? 0;
          const activeEnvs = s.active_envs ?? [];
          return (
            <div className="space-y-1">
              {activeEnvs.length > 0 && (
                <div className="flex flex-wrap items-center gap-1">
                  {activeEnvs.map((e) => (
                    <EnvBadge key={e} env={e} pulse={e === "live"} />
                  ))}
                </div>
              )}
              <div className="text-[11px] text-slate-400">
                回测 {bt} · 模拟 {sh} · 实盘 {lv}
              </div>
            </div>
          );
        },
      },
      {
        id: "status_hint",
        accessorFn: (r) => (needsOperatorIntervention(r).needed ? 1 : 0),
        header: "当前状态",
        cell: ({ row }) => {
          const s = row.original;
          const diag = needsOperatorIntervention(s);
          return (
            <div className="flex flex-wrap items-center gap-1.5">
              <ApprovalBadge status={s.latest_approval_status ?? "PENDING"} />
              <span
                className={cn(
                  "inline-flex items-center gap-1 text-[11px]",
                  diag.tone === "danger"
                    ? "text-rose-300"
                    : diag.tone === "warning"
                      ? "text-amber-300"
                      : "text-emerald-300"
                )}
              >
                {diag.needed ? (
                  <AlertCircle className="h-3 w-3 shrink-0" />
                ) : (
                  <CheckCircle2 className="h-3 w-3 shrink-0" />
                )}
                <span>{diag.reason}</span>
              </span>
            </div>
          );
        },
      },
      {
        id: "actions",
        header: "快捷操作",
        enableSorting: false,
        meta: { align: "right" },
        cell: ({ row }) => {
          const s = row.original;
          const latestVersionId = `${s.strategy_id}:v${s.current_version}`;
          const latestBtRunId = s.latest_backtest_metrics?.run_id;
          return (
            <div
              className="inline-flex items-center justify-end gap-1.5"
              onClick={(e) => e.stopPropagation()}
            >
              {latestBtRunId && (
                <Link href={`/reports/${encodeURIComponent(latestBtRunId)}`}>
                  <Button size="sm" variant="secondary" title="查看最近回测 K 线买卖点与收益报告">
                    <LineChart className="h-3.5 w-3.5 text-sky-400" />
                    <span>看回测K线</span>
                  </Button>
                </Link>
              )}
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
                <span>跑回测</span>
              </BacktestActionButton>
              <Link href={`/strategies/${encodeURIComponent(s.strategy_id)}`}>
                <Button size="sm" variant="outline">
                  <span>管理</span>
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
    <div className="space-y-4">
      {/* Top Header: Clean Title + Simple Creation Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">我的策略</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            创建、回测并管理您的量化策略。先跑历史回测查看 K 线买卖点，满意后再开启模拟盘或实盘。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="default"
            size="default"
            onClick={() => {
              setCreateDefaultMode("nl");
              setCreateOpen(true);
            }}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>一句话新建策略</span>
          </Button>
          <Button
            variant="outline"
            size="default"
            onClick={() => {
              setCreateDefaultMode("python");
              setCreateOpen(true);
            }}
          >
            <Plus className="h-4 w-4" />
            <span>编写 Python 策略</span>
          </Button>
        </div>
      </div>

      {/* Dismissible 3-Step First-Time User Quickstart Guide */}
      {showGuide && (
        <div className="relative rounded-lg border border-blue-500/30 bg-blue-950/20 p-3.5 text-xs">
          <button
            type="button"
            onClick={() => setShowGuide(false)}
            className="absolute right-3 top-3 rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-white"
            title="不再显示新手引导"
          >
            <X className="h-3.5 w-3.5" />
          </button>
          <div className="mb-2 font-semibold text-blue-200">
            新手三步快速上手：
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
            <div className="rounded border border-slate-800/90 bg-slate-950/70 p-2.5">
              <div className="font-semibold text-white">① 新建策略</div>
              <p className="mt-0.5 text-[11px] text-slate-300">
                点击右上角「一句话新建策略」，直接用中文输入买卖想法（无需手写代码）。
              </p>
            </div>
            <div className="rounded border border-slate-800/90 bg-slate-950/70 p-2.5">
              <div className="font-semibold text-white">② 跑回测看 K 线买卖点</div>
              <p className="mt-0.5 text-[11px] text-slate-300">
                点击列表右侧「跑回测」验证历史收益，再点「看回测K线」检查每笔买点与卖点。
              </p>
            </div>
            <div className="rounded border border-slate-800/90 bg-slate-950/70 p-2.5">
              <div className="font-semibold text-white">③ 模拟盘验证 → 开启实盘</div>
              <p className="mt-0.5 text-[11px] text-slate-300">
                点击「管理」审核通过后，可先开启「模拟盘」（真实行情不花真钱），稳健后再上实盘。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* First-Glance KPI Layer (4 Clean Cards) */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiMetricCard
          label="策略总数"
          timeWindow="当前账号"
          benchmarkLabel={`一句话生成 ${summary.nlCount} · 代码编写 ${summary.pythonCount}`}
        >
          <span className="font-mono">{summary.total}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="待处理策略"
          timeWindow="待回测 / 待审核"
          alertTone={summary.pendingOrActionCount > 0 ? "warning" : "none"}
          benchmarkLabel="点击筛选待处理项"
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
          label="实盘运行中"
          timeWindow="真金白银交易"
          benchmarkLabel="点击筛选实盘策略"
          onClick={() =>
            setPatrolFilter((f) => (f === "LIVE_ACTIVE" ? "ALL" : "LIVE_ACTIVE"))
          }
        >
          <span className="font-mono text-rose-300">{summary.liveActiveCount}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="今日实盘盈亏"
          timeWindow="近 24 小时"
          benchmarkLabel="已实现净收益"
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
          title="正在加载策略列表..."
          currentStageLabel="读取策略与近期回测收益"
          progressPct={60}
        />
      ) : strategiesQuery.isError ? (
        <ActionableErrorState
          title="加载策略列表失败"
          reasonCode={apiErr?.reasonCode ?? "STRATEGIES_FETCH_FAILED"}
          message={apiErr?.message ?? String(strategiesQuery.error)}
          remediation="请刷新页面或检查网络连接后重试。"
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
          title="还没有任何策略，花 10 秒创建第一个吧"
          description="您可以用一句中文描述买卖规则自动生成策略，也可以直接粘贴 Python 代码。"
          icon={<Code2 className="h-5 w-5 text-slate-400" />}
          primaryAction={{
            label: "一句话生成第一个策略",
            onClick: () => {
              setCreateDefaultMode("nl");
              setCreateOpen(true);
            },
          }}
          secondaryAction={{
            label: "编写 Python 策略",
            variant: "outline",
            onClick: () => {
              setCreateDefaultMode("python");
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
                  ["NEEDS_ACTION", `待处理 (${summary.pendingOrActionCount})`],
                  ["APPROVED_READY", "已审核"],
                  ["LIVE_ACTIVE", `实盘 (${summary.liveActiveCount})`],
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
                <div className="text-slate-400">策略说明</div>
                <div className="mt-0.5 text-slate-200">{s.description || "无备注"}</div>
              </div>
              <div>
                <div className="text-slate-400">策略 ID / 最近更新</div>
                <div className="mt-0.5 font-mono text-slate-200">
                  {s.strategy_id} · {formatUtcTimestamp(s.updated_at_ms, false)}
                </div>
              </div>
              <div>
                <div className="text-slate-400">最近回测报告</div>
                <div className="mt-0.5 text-sky-300">
                  {s.latest_backtest_metrics ? (
                    <Link
                      href={`/reports/${encodeURIComponent(s.latest_backtest_metrics.run_id)}`}
                      className="hover:underline"
                    >
                      点击打开 K 线买卖点与收益报告 →
                    </Link>
                  ) : (
                    <span className="text-slate-500">暂无回测记录</span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-end">
                <Link href={`/strategies/${encodeURIComponent(s.strategy_id)}`}>
                  <Button size="sm" variant="secondary">
                    调整参数 / 开启模拟盘或实盘 →
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
        <div className="p-6 text-xs text-muted-foreground">
          正在加载我的策略...
        </div>
      }
    >
      <StrategiesWorkbenchInner />
    </Suspense>
  );
}
