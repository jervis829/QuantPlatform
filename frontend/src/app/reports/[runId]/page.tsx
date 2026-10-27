"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { EnvBadge } from "../../../components/quant/badges";
import { FinancialChartContainer } from "../../../components/quant/chart-container";
import { KpiMetricCard, NumericDisplay } from "../../../components/quant/numeric-display";
import {
  ActionableErrorState,
  RunLoadingState,
} from "../../../components/quant/state-views";
import { QuantApiError, quantApi } from "../../../lib/apiClient";
import { usePreferences } from "../../../lib/preferences-context";

export default function BacktestReportBridgePage() {
  const params = useParams<{ runId: string }>();
  const runId = decodeURIComponent(String(params?.runId ?? ""));
  const { apiKey } = usePreferences();

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

  if (reportQuery.isLoading || chartQuery.isLoading) {
    return (
      <RunLoadingState
        title={`正在加载回测报告 (${runId.slice(0, 20)}...)`}
        currentStageLabel="读取绩效指标与 K 线买卖复盘序列"
        progressPct={70}
      />
    );
  }

  if (reportQuery.isError) {
    const err =
      reportQuery.error instanceof QuantApiError ? reportQuery.error : null;
    return (
      <ActionableErrorState
        title="加载回测报告失败"
        reasonCode={err?.reasonCode ?? "REPORT_NOT_FOUND"}
        message={err?.message ?? String(reportQuery.error)}
        remediation="请确认该回测 Run ID 存在且已执行完毕，或返回策略工作台重新发起回测。"
      />
    );
  }

  const summary = ((reportQuery.data?.summary_metrics ?? {}) as Record<string, number>);

  return (
    <div className="space-y-4 pb-10">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-1">
          <Link
            href="/strategies"
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>返回策略工作台</span>
          </Link>
          <div className="flex items-center gap-2">
            <EnvBadge env="backtest" />
            <h1 className="text-lg font-semibold text-white">回测报告概览</h1>
            <code className="font-mono text-xs text-slate-400">{runId}</code>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiMetricCard label="累计收益率" timeWindow="全回测周期">
          <NumericDisplay
            value={summary.cumulative_return ?? 0}
            mode="ratioPercent"
            coloredBySign
            showPlusSign
          />
        </KpiMetricCard>
        <KpiMetricCard label="夏普比率 (Sharpe)" timeWindow="年化">
          <NumericDisplay value={summary.sharpe_ratio ?? 0} mode="decimal" decimals={2} />
        </KpiMetricCard>
        <KpiMetricCard label="最大回撤" timeWindow="峰值至谷值">
          <NumericDisplay
            value={-Math.abs(summary.max_drawdown ?? 0)}
            mode="ratioPercent"
            coloredBySign
          />
        </KpiMetricCard>
        <KpiMetricCard label="胜率 (Win Rate)" timeWindow="逐笔闭合">
          <NumericDisplay
            value={summary.win_rate ?? 0}
            mode="ratioPercent"
          />
        </KpiMetricCard>
      </div>

      {chartQuery.data && (
        <FinancialChartContainer
          title="回测复盘 K 线与净值曲线"
          symbol={chartQuery.data.symbol}
          symbolSpec={chartQuery.data.symbol_spec}
          bars={chartQuery.data.bars}
          tradeMarkers={chartQuery.data.trade_markers}
          equitySeries={chartQuery.data.equity_series}
        />
      )}
    </div>
  );
}
