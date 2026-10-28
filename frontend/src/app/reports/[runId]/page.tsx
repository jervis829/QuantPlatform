"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  GitCompare,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { EnvBadge } from "../../../components/quant/badges";
import { FinancialChartContainer } from "../../../components/quant/chart-container";
import { QuantDataTable } from "../../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../../components/quant/state-views";
import { MonthlyReturnMatrix } from "../../../components/report/monthly-matrix";
import { ResearchRobustnessPanels } from "../../../components/report/research-panels";
import { SideBySideRunComparison } from "../../../components/report/run-comparison";
import { Button } from "../../../components/ui/button";
import { QuantApiError, quantApi } from "../../../lib/apiClient";
import {
  formatCurrency,
  formatDurationMs,
  formatPercent,
  formatPriceBySpec,
  formatQtyBySpec,
  formatUtcTimestamp,
} from "../../../lib/formatters";
import { usePreferences } from "../../../lib/preferences-context";
import type { ChartTradeMarker } from "../../../types/openapi.generated";

interface ClosedTradeRow {
  exchange: string;
  symbol: string;
  side: string;
  quantity: string;
  entry_price: string;
  exit_price: string;
  entry_ts_ms: number;
  exit_ts_ms: number;
  holding_ms: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
}

interface DailyAttributionRow {
  date: string;
  start_equity: string;
  end_equity: string;
  daily_return: number;
  realized_pnl: string;
  fees: string;
  funding: string;
}

function BacktestReportInner() {
  const params = useParams<{ runId: string }>();
  const runId = decodeURIComponent(String(params?.runId ?? ""));
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { apiKey } = usePreferences();

  const compareRunId = searchParams.get("compareRunId") || "";
  const [focusedTimeSec, setFocusedTimeSec] = useState<number | null>(null);
  const [attribTab, setAttribTab] = useState<"fills" | "trades" | "daily">("fills");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const reportQuery = useQuery({
    queryKey: ["backtest-report", runId, apiKey],
    queryFn: () => quantApi.getBacktestReport(apiKey, "backtest", runId),
    enabled: Boolean(runId),
  });

  const chartQuery = useQuery({
    queryKey: ["run-chart-data", "backtest", runId, apiKey],
    queryFn: () => quantApi.getRunChartData(apiKey, "backtest", runId),
    enabled: Boolean(runId),
  });

  const allBtRunsQuery = useQuery({
    queryKey: ["runs", "backtest-all", apiKey],
    queryFn: () => quantApi.listRuns(apiKey, { env: "backtest", limit: 100 }),
  });

  const compareReportQuery = useQuery({
    queryKey: ["backtest-report", compareRunId, apiKey],
    queryFn: () => quantApi.getBacktestReport(apiKey, "backtest", compareRunId),
    enabled: Boolean(compareRunId && compareRunId !== runId),
  });

  const report = reportQuery.data ?? null;
  const lineage = ((report?.lineage ?? {}) as Record<string, unknown>);
  const strategyId = String(lineage.strategy_id ?? "");
  const versionId = String(lineage.strategy_version_id ?? "");

  const strategyDetailQuery = useQuery({
    queryKey: ["strategy-detail", strategyId, apiKey],
    queryFn: () => quantApi.getStrategyDetail(apiKey, strategyId),
    enabled: Boolean(strategyId),
  });

  const versionParams = useMemo<Record<string, unknown>>(() => {
    if (!strategyDetailQuery.data) return {};
    const found = strategyDetailQuery.data.versions.find(
      (v) => v.version_id === versionId
    );
    return found?.params ?? strategyDetailQuery.data.latest_version.params ?? {};
  }, [strategyDetailQuery.data, versionId]);

  const setCompareParam = (nextCompareId: string) => {
    const sp = new URLSearchParams(window.location.search);
    if (nextCompareId) sp.set("compareRunId", nextCompareId);
    else sp.delete("compareRunId");
    const qs = sp.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  };

  const scrollToSection = (elementId: string) => {
    document.getElementById(elementId)?.scrollIntoView({ behavior: "smooth" });
  };

  const symbolSpec = chartQuery.data?.symbol_spec;
  const tickSize = symbolSpec?.tick_size ?? "0.1";
  const stepSize = symbolSpec?.step_size ?? "0.001";

  const tradeRows = useMemo(
    () => ((report?.per_trade_attribution ?? []) as ClosedTradeRow[]),
    [report?.per_trade_attribution]
  );

  const dailyRows = useMemo(
    () => ((report?.daily_attribution ?? []) as DailyAttributionRow[]),
    [report?.daily_attribution]
  );

  const markerRows = useMemo<ChartTradeMarker[]>(
    () => chartQuery.data?.trade_markers ?? [],
    [chartQuery.data?.trade_markers]
  );

  const markerColumns = useMemo<ColumnDef<ChartTradeMarker, unknown>[]>(
    () => [
      {
        id: "action",
        accessorFn: (r) => r.action_label || r.side,
        header: "买卖动作 (点击定位K线)",
        cell: ({ row }) => {
          const isBuy = row.original.side.toUpperCase() === "BUY";
          return (
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                isBuy
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-rose-500/20 text-rose-300"
              }`}
            >
              #{row.index + 1} {row.original.action_label || row.original.side}
            </span>
          );
        },
      },
      {
        id: "ts",
        accessorFn: (r) => r.ts_ms || r.time_sec * 1000,
        header: "成交时间 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-300">
            {formatUtcTimestamp(
              row.original.ts_ms || row.original.time_sec * 1000,
              true
            )}
          </span>
        ),
      },
      {
        id: "price",
        accessorFn: (r) => Number(r.price),
        header: "成交价",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="font-semibold text-white">
            {formatPriceBySpec(row.original.price, tickSize)}
          </span>
        ),
      },
      {
        id: "qty",
        accessorFn: (r) => Number(r.qty),
        header: "成交数量",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{formatQtyBySpec(row.original.qty, stepSize)}</span>,
      },
      {
        id: "quote_qty",
        accessorFn: (r) => Number(r.quote_qty || 0),
        header: "成交名义额",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span>{formatCurrency(row.original.quote_qty || "0")}</span>
        ),
      },
      {
        id: "slippage",
        accessorFn: (r) => Number(r.slippage_bps || 0),
        header: "滑点 (bps)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{row.original.slippage_bps || "0"}</span>,
      },
      {
        id: "fee",
        accessorFn: (r) => Number(r.fee || 0),
        header: "手续费",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="text-amber-300">{formatCurrency(row.original.fee)}</span>
        ),
      },
      {
        id: "realized_pnl",
        accessorFn: (r) => Number(r.realized_pnl || 0),
        header: "已实现盈亏",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.realized_pnl}
            mode="currency"
            coloredBySign
            showPlusSign
            className="font-bold"
          />
        ),
      },
      {
        id: "pos_after",
        accessorFn: (r) => Number(r.position_after || 0),
        header: "成交后净仓",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="text-sky-300">
            {formatQtyBySpec(row.original.position_after || "0", stepSize)}
          </span>
        ),
      },
      {
        id: "reason",
        accessorKey: "signal_reason",
        header: "策略信号原因",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-[11px] text-slate-400">
            {row.original.signal_reason || row.original.liquidity}
          </span>
        ),
      },
    ],
    [stepSize, tickSize]
  );

  const tradeColumns = useMemo<ColumnDef<ClosedTradeRow, unknown>[]>(
    () => [
      {
        id: "side",
        accessorKey: "side",
        header: "方向",
        cell: ({ row }) => (
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
              row.original.side === "LONG"
                ? "bg-emerald-500/20 text-emerald-300"
                : "bg-rose-500/20 text-rose-300"
            }`}
          >
            {row.original.side}
          </span>
        ),
      },
      {
        id: "entry_ts",
        accessorKey: "entry_ts_ms",
        header: "开仓 → 平仓时间 (点击联动K线)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-300">
            {formatUtcTimestamp(row.original.entry_ts_ms, false)} →{" "}
            {formatUtcTimestamp(row.original.exit_ts_ms, false).slice(11)}
          </span>
        ),
      },
      {
        id: "qty",
        accessorFn: (r) => Number(r.quantity),
        header: "数量",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{formatQtyBySpec(row.original.quantity, stepSize)}</span>,
      },
      {
        id: "prices",
        accessorFn: (r) => Number(r.entry_price),
        header: "开仓价 → 平仓价",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span>
            {formatPriceBySpec(row.original.entry_price, tickSize)} →{" "}
            {formatPriceBySpec(row.original.exit_price, tickSize)}
          </span>
        ),
      },
      {
        id: "holding",
        accessorKey: "holding_ms",
        header: "持仓时长",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{formatDurationMs(row.original.holding_ms)}</span>,
      },
      {
        id: "gross_pnl",
        accessorKey: "gross_pnl",
        header: "毛利 (USDT)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.gross_pnl}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        ),
      },
      {
        id: "fees",
        accessorKey: "fees",
        header: "双边手续费",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="text-amber-300">{formatCurrency(row.original.fees)}</span>
        ),
      },
      {
        id: "net_pnl",
        accessorKey: "net_pnl",
        header: "净盈亏 (USDT)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.net_pnl}
            mode="currency"
            coloredBySign
            showPlusSign
            className="font-bold"
          />
        ),
      },
    ],
    [stepSize, tickSize]
  );

  const dailyColumns = useMemo<ColumnDef<DailyAttributionRow, unknown>[]>(
    () => [
      {
        id: "date",
        accessorKey: "date",
        header: "UTC 日期",
        meta: { mono: true },
        cell: ({ row }) => <span className="font-bold text-white">{row.original.date}</span>,
      },
      {
        id: "start_equity",
        accessorFn: (r) => Number(r.start_equity),
        header: "日初净值",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{formatCurrency(row.original.start_equity)}</span>,
      },
      {
        id: "end_equity",
        accessorFn: (r) => Number(r.end_equity),
        header: "日末净值",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{formatCurrency(row.original.end_equity)}</span>,
      },
      {
        id: "daily_return",
        accessorKey: "daily_return",
        header: "当日收益率",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.daily_return}
            mode="ratioPercent"
            coloredBySign
            showPlusSign
          />
        ),
      },
      {
        id: "realized_pnl",
        accessorFn: (r) => Number(r.realized_pnl),
        header: "当日已实现盈亏",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.realized_pnl}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        ),
      },
      {
        id: "fees",
        accessorFn: (r) => Number(r.fees),
        header: "当日手续费",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="text-amber-300">{formatCurrency(row.original.fees)}</span>
        ),
      },
      {
        id: "funding",
        accessorFn: (r) => Number(r.funding),
        header: "资金费结算",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.funding}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        ),
      },
    ],
    []
  );

  if (reportQuery.isLoading || chartQuery.isLoading) {
    return (
      <RunLoadingState
        title={`正在加载回测深度归因报告 (${runId.slice(0, 20)}...)`}
        currentStageLabel="聚合 30+ 绩效指标、月度矩阵、费用拆解与 K 线买卖复盘点位"
        progressPct={72}
      />
    );
  }

  if (reportQuery.isError || !report) {
    const err =
      reportQuery.error instanceof QuantApiError ? reportQuery.error : null;
    return (
      <ActionableErrorState
        title="加载回测报告失败"
        reasonCode={err?.reasonCode ?? "REPORT_NOT_FOUND"}
        message={err?.message ?? String(reportQuery.error)}
        remediation="请确认该回测 Run ID 存在且已成功落库，或返回策略工作台重新发起回测。"
        actions={[
          { label: "返回策略工作台", onClick: () => router.push("/strategies") },
        ]}
      />
    );
  }

  const summary = (report.summary_metrics ?? {}) as Record<string, unknown>;
  const ddDetails = (report.drawdown_details ?? {}) as Record<string, unknown>;
  const tradeMetrics = (report.trade_metrics ?? {}) as Record<string, unknown>;
  const costs = (report.cost_breakdown ?? {}) as Record<string, unknown>;
  const bench = (report.benchmark_comparison ?? {}) as Record<string, unknown>;
  const holdDist = (report.holding_duration_distribution ?? {}) as Record<string, unknown>;
  const exposure = (report.exposure_and_concentration ?? {}) as Record<string, unknown>;
  const monthlyMatrix = (report.monthly_return_matrix ?? {}) as Record<
    string,
    Record<string, number>
  >;

  const cumRet = Number(summary.cumulative_return ?? 0);
  const maxDd = Number(summary.max_drawdown ?? 0);
  const sharpe = Number(summary.sharpe ?? 0);
  const closedCount = Number(tradeMetrics.closed_trade_count ?? 0);
  const feeRatio = Number(costs.fee_to_gross_pnl_ratio ?? 0);

  // Automated Weakness Diagnosis
  const weaknesses: string[] = [];
  if (closedCount < 3) {
    weaknesses.push(`闭合交易样本较少（仅 ${closedCount} 笔），统计显著性偏低，建议拉长回测区间`);
  }
  if (feeRatio > 0.35) {
    weaknesses.push(
      `手续费占毛利比重较高（${(feeRatio * 100).toFixed(1)}%），高频换手侵蚀明显，建议优先考虑限价 Maker 或拉大过滤阈值`
    );
  }
  if (Math.abs(maxDd) > 0.15) {
    weaknesses.push(
      `历史最大回撤达 ${(Math.abs(maxDd) * 100).toFixed(2)}%，建议收紧单笔仓位或补充止损规则`
    );
  }
  if (ddDetails.recovery_ts_ms === null && maxDd < -0.01) {
    weaknesses.push("期末最大回撤尚未完全创新高修复（水下运行状态）");
  }

  const handleExportReportJson = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${runId}_backtest_report.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportTradesCsv = () => {
    const hdr =
      "exchange,symbol,side,quantity,entry_price,exit_price,entry_ts_ms,exit_ts_ms,holding_ms,gross_pnl,fees,net_pnl\n";
    const rows = tradeRows
      .map(
        (t) =>
          `${t.exchange},${t.symbol},${t.side},${t.quantity},${t.entry_price},${t.exit_price},${t.entry_ts_ms},${t.exit_ts_ms},${t.holding_ms},${t.gross_pnl},${t.fees},${t.net_pnl}`
      )
      .join("\n");
    const blob = new Blob([hdr + rows], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${runId}_per_trade_attribution.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const eqArr = (report.equity_curve ?? []) as Array<Record<string, unknown>>;
  const windowLabel =
    eqArr.length >= 2
      ? `${formatUtcTimestamp(Number(eqArr[0].ts_ms), false).slice(0, 16)} → ${formatUtcTimestamp(Number(eqArr[eqArr.length - 1].ts_ms), false).slice(0, 16)}`
      : "回测全窗口";

  return (
    <div className="space-y-5 pb-12">
      {/* Top Header: Clean Breadcrumb, Title, Run Comparison & Export */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Link
              href={strategyId ? `/strategies/${encodeURIComponent(strategyId)}` : "/strategies"}
              className="inline-flex items-center gap-1 hover:text-white"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>返回策略管理 ({strategyId || "我的策略"})</span>
            </Link>
            <span>·</span>
            <span className="font-mono">{versionId}</span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <EnvBadge env="backtest" />
            <h1 className="text-lg font-bold text-white">历史回测报告与 K 线买卖点</h1>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Side-by-Side Comparison Picker */}
          <div className="flex items-center gap-1.5 rounded border border-border bg-slate-950 px-2.5 py-1 text-xs">
            <GitCompare className="h-3.5 w-3.5 text-sky-400" />
            <span className="text-slate-400">对比其它回测:</span>
            <select
              value={compareRunId}
              onChange={(e) => setCompareParam(e.target.value)}
              className="bg-transparent text-xs text-white focus:outline-none"
            >
              <option value="">不对比</option>
              {(allBtRunsQuery.data ?? [])
                .filter((r) => r.run_id !== runId)
                .map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.strategy_version_id} · {r.run_id.slice(0, 12)}...
                  </option>
                ))}
            </select>
          </div>

          <Button size="sm" variant="outline" onClick={handleExportTradesCsv}>
            <FileSpreadsheet className="h-3.5 w-3.5" />
            <span>导出交易明细</span>
          </Button>
          <Button size="sm" variant="outline" onClick={handleExportReportJson}>
            <Download className="h-3.5 w-3.5" />
            <span>导出完整报告</span>
          </Button>
        </div>
      </div>

      {/* Automated Strategy Diagnosis Banner */}
      <div
        className={`rounded-lg border p-3.5 text-xs ${
          weaknesses.length === 0 && cumRet > 0
            ? "border-emerald-500/40 bg-emerald-950/20 text-emerald-100"
            : "border-amber-500/45 bg-amber-950/20 text-amber-100"
        }`}
      >
        <div className="flex items-center gap-2 font-semibold">
          {weaknesses.length === 0 && cumRet > 0 ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-emerald-400" />
              <span>回测结论：当前区间整体盈利且风控指标稳健，建议结合下方 K 线买卖点复盘后再上模拟盘。</span>
            </>
          ) : (
            <>
              <AlertTriangle className="h-4 w-4 text-amber-400" />
              <span>风险提示（上模拟盘或实盘前请注意以下 {weaknesses.length || 1} 点）：</span>
            </>
          )}
        </div>
        {weaknesses.length > 0 && (
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[11px] text-amber-200/95">
            {weaknesses.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        )}
      </div>

      {/* Optional Side-by-Side Comparison Matrix */}
      {compareRunId && compareReportQuery.data && (
        <SideBySideRunComparison
          primaryRunId={runId}
          primaryReport={report}
          compareRunId={compareRunId}
          compareReport={compareReportQuery.data}
        />
      )}

      {/* First-Glance KPI Cards (5 Clean Cards) */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiMetricCard
          label="总收益率"
          timeWindow={windowLabel}
          benchmarkLabel={`跑赢基准: ${formatPercent(Number(bench.excess_return ?? 0), {
            inputIsRatio: true,
            showPlusSign: true,
          })}`}
          deltaNode={
            <NumericDisplay
              value={String(summary.net_pnl ?? "0")}
              mode="currency"
              coloredBySign
              showPlusSign
            />
          }
          onClick={() => {
            setShowAdvanced(true);
            setTimeout(() => scrollToSection("monthly-matrix-section"), 60);
          }}
        >
          <NumericDisplay
            value={cumRet}
            mode="ratioPercent"
            coloredBySign
            showPlusSign
          />
        </KpiMetricCard>

        <KpiMetricCard
          label="最大回撤"
          timeWindow={`持续 ${formatDurationMs(Number(ddDetails.duration_ms ?? 0))}`}
          benchmarkLabel="点击定位最大回撤 K 线"
          alertTone={Math.abs(maxDd) > 0.15 ? "warning" : "none"}
          onClick={() => {
            const troughMs = Number(ddDetails.trough_ts_ms ?? 0);
            if (troughMs > 0) {
              setFocusedTimeSec(Math.floor(troughMs / 1000));
              scrollToSection("chart-section");
            }
          }}
        >
          <NumericDisplay
            value={-Math.abs(maxDd)}
            mode="ratioPercent"
            coloredBySign
          />
        </KpiMetricCard>

        <KpiMetricCard
          label="夏普比率"
          timeWindow="风险收益比"
          benchmarkLabel={`索提诺 ${Number(summary.sortino ?? 0).toFixed(2)}`}
          onClick={() => {
            setAttribTab("daily");
            scrollToSection("attribution-section");
          }}
        >
          <NumericDisplay value={sharpe} mode="decimal" decimals={2} />
        </KpiMetricCard>

        <KpiMetricCard
          label="交易胜率"
          timeWindow={`共 ${closedCount} 笔开平仓`}
          benchmarkLabel={`盈亏比 ${Number(tradeMetrics.payoff_ratio ?? 0).toFixed(2)}`}
          onClick={() => {
            setAttribTab("trades");
            scrollToSection("attribution-section");
          }}
        >
          <NumericDisplay
            value={Number(tradeMetrics.win_rate ?? 0)}
            mode="ratioPercent"
          />
        </KpiMetricCard>

        <KpiMetricCard
          label="手续费占比"
          timeWindow={`共付 ${formatCurrency(String(costs.total_fees ?? "0"))}`}
          benchmarkLabel="占毛利比例（点击看拆解）"
          alertTone={feeRatio > 0.35 ? "warning" : "none"}
          onClick={() => {
            setShowAdvanced(true);
            setTimeout(() => scrollToSection("cost-breakdown-section"), 60);
          }}
        >
          <span className="font-mono text-amber-300">
            {(feeRatio * 100).toFixed(1)}%
          </span>
        </KpiMetricCard>
      </section>

      {/* Main Chart Container: K-Line + Buy/Sell Markers + Volume + Equity + Drawdown */}
      <section id="chart-section">
        {chartQuery.data ? (
          <FinancialChartContainer
            title="K 线买卖点复盘"
            symbol={chartQuery.data.symbol}
            symbolSpec={chartQuery.data.symbol_spec}
            bars={chartQuery.data.bars}
            tradeMarkers={chartQuery.data.trade_markers}
            roundTrips={chartQuery.data.round_trips}
            equitySeries={chartQuery.data.equity_series}
            focusedTimeSec={focusedTimeSec}
            heightPx={420}
          />
        ) : null}
      </section>

      {/* Per-Fill Buy/Sell Points, Round-Trip Trades & Daily Attribution Tables (Immediately below K-line!) */}
      <section id="attribution-section" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="inline-flex flex-wrap rounded-md border border-border bg-slate-950 p-1">
            <button
              type="button"
              onClick={() => setAttribTab("fills")}
              className={`rounded px-3 py-1 text-xs font-medium ${
                attribTab === "fills"
                  ? "bg-blue-600 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              买卖点明细 ({markerRows.length}) · 点击定位 K 线
            </button>
            <button
              type="button"
              onClick={() => setAttribTab("trades")}
              className={`rounded px-3 py-1 text-xs font-medium ${
                attribTab === "trades"
                  ? "bg-blue-600 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              每笔开平仓盈亏 ({tradeRows.length})
            </button>
            <button
              type="button"
              onClick={() => setAttribTab("daily")}
              className={`rounded px-3 py-1 text-xs font-medium ${
                attribTab === "daily"
                  ? "bg-blue-600 text-white"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              每日收益统计 ({dailyRows.length})
            </button>
          </div>
        </div>

        {attribTab === "fills" ? (
          markerRows.length === 0 ? (
            <EmptyState
              title="本次回测未产生买卖成交点"
              description="当前策略参数在回测区间内未触发买入或卖出信号。"
            />
          ) : (
            <QuantDataTable
              tableId={`rep_fills_${runId.slice(0, 10)}`}
              data={markerRows}
              columns={markerColumns}
              getRowId={(r) => r.fill_id}
              onRowClick={(fill) => {
                setFocusedTimeSec(fill.time_sec);
                scrollToSection("chart-section");
              }}
            />
          )
        ) : attribTab === "trades" ? (
          tradeRows.length === 0 ? (
            <EmptyState
              title="暂无已平仓的完整交易对"
              description="可能策略当前仅开仓尚未平仓，可切到左侧「买卖点明细」查看单边买点。"
            />
          ) : (
            <QuantDataTable
              tableId={`rep_trades_${runId.slice(0, 10)}`}
              data={tradeRows}
              columns={tradeColumns}
              getRowId={(r, i) => `${r.entry_ts_ms}_${r.exit_ts_ms}_${i}`}
              onRowClick={(trade) => {
                setFocusedTimeSec(Math.floor(trade.entry_ts_ms / 1000));
                scrollToSection("chart-section");
              }}
            />
          )
        ) : (
          <QuantDataTable
            tableId={`rep_daily_${runId.slice(0, 10)}`}
            data={dailyRows}
            columns={dailyColumns}
            getRowId={(r) => r.date}
          />
        )}
      </section>

      {/* Progressive Disclosure: Advanced Quant Research Analysis */}
      <section className="rounded-lg border border-border bg-card/50 p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-white">
              高级深度分析（月度收益表 · 成本与持仓拆解 · 参数敏感性检验）
            </h2>
            <p className="text-xs text-muted-foreground">
              想进一步检查策略是否过拟合、手续费损耗分布或各月份稳定性？展开查看完整投研分析。
            </p>
          </div>
          <Button
            type="button"
            variant={showAdvanced ? "outline" : "secondary"}
            size="sm"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "收起高级分析 ▴" : "展开高级深度分析 ▾"}
          </Button>
        </div>

        {showAdvanced && (
          <div className="space-y-5 border-t border-border pt-4">
            {/* Monthly Return Matrix */}
            <div id="monthly-matrix-section">
              <MonthlyReturnMatrix matrix={monthlyMatrix} />
            </div>

            {/* Cost Erosion & Holding Duration / Exposure Breakdown */}
            <div
              id="cost-breakdown-section"
              className="grid grid-cols-1 gap-4 lg:grid-cols-2"
            >
              <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                <h3 className="text-xs font-semibold text-white">
                  交易成本与资金费拆解
                </h3>
                <div className="grid grid-cols-2 gap-3 font-mono text-xs sm:grid-cols-3">
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">累计手续费</div>
                    <div className="mt-1 font-bold text-amber-300">
                      {formatCurrency(String(costs.total_fees ?? "0"))}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      占本金{" "}
                      {formatPercent(Number(costs.fee_to_initial_equity_ratio ?? 0), {
                        inputIsRatio: true,
                      })}
                    </div>
                  </div>
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">资金费净收支</div>
                    <div className="mt-1 font-bold">
                      <NumericDisplay
                        value={String(costs.total_funding ?? "0")}
                        mode="currency"
                        coloredBySign
                        showPlusSign
                      />
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      占毛利{" "}
                      {formatPercent(Number(costs.funding_to_gross_pnl_ratio ?? 0), {
                        inputIsRatio: true,
                      })}
                    </div>
                  </div>
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">借币利息</div>
                    <div className="mt-1 font-bold text-slate-200">
                      {formatCurrency(String(costs.total_interest ?? "0"))}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">按计息周期累计</div>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                <h3 className="text-xs font-semibold text-white">
                  持仓时长与多空仓位占比
                </h3>
                <div className="grid grid-cols-2 gap-3 font-mono text-xs sm:grid-cols-4">
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">中位持仓时长</div>
                    <div className="mt-1 font-bold text-white">
                      {formatDurationMs(Number(holdDist.median_ms ?? 0))}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      P25 {formatDurationMs(Number(holdDist.p25_ms ?? 0))}
                    </div>
                  </div>
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">最长持仓时长</div>
                    <div className="mt-1 font-bold text-white">
                      {formatDurationMs(Number(holdDist.max_ms ?? 0))}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      P75 {formatDurationMs(Number(holdDist.p75_ms ?? 0))}
                    </div>
                  </div>
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">做多时间占比</div>
                    <div className="mt-1 font-bold text-emerald-300">
                      {formatPercent(Number(exposure.long_time_ratio ?? 0), {
                        inputIsRatio: true,
                      })}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      做空{" "}
                      {formatPercent(Number(exposure.short_time_ratio ?? 0), {
                        inputIsRatio: true,
                      })}
                    </div>
                  </div>
                  <div className="rounded bg-slate-900 p-2.5">
                    <div className="text-[11px] font-sans text-slate-400">最大持仓金额</div>
                    <div className="mt-1 font-bold text-white">
                      {formatCurrency(Number(exposure.max_gross_exposure ?? 0))}
                    </div>
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      集中度 {Number(exposure.position_concentration_hhi ?? 0).toFixed(2)}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Parameter Sensitivity Heatmap & Walk-Forward Analysis */}
            <div id="robustness-section" className="space-y-2.5">
              <h3 className="text-xs font-semibold text-slate-200">
                参数敏感性热力图与 Walk-Forward 滚动验证（检验策略是否过拟合）
              </h3>
              <ResearchRobustnessPanels runId={runId} versionParams={versionParams} />
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default function BacktestReportPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载回测深度归因报告...
        </div>
      }
    >
      <BacktestReportInner />
    </Suspense>
  );
}
