"use client";

import React from "react";
import { GitCompare } from "lucide-react";
import { formatCurrency, formatPercent } from "../../lib/formatters";
import { NumericDisplay } from "../quant/numeric-display";

export interface SideBySideRunComparisonProps {
  primaryRunId: string;
  primaryReport: Record<string, unknown>;
  compareRunId: string;
  compareReport: Record<string, unknown>;
}

export function SideBySideRunComparison({
  primaryRunId,
  primaryReport,
  compareRunId,
  compareReport,
}: SideBySideRunComparisonProps) {
  const mA = (primaryReport.summary_metrics ?? {}) as Record<string, unknown>;
  const mB = (compareReport.summary_metrics ?? {}) as Record<string, unknown>;
  const tA = (primaryReport.trade_metrics ?? {}) as Record<string, unknown>;
  const tB = (compareReport.trade_metrics ?? {}) as Record<string, unknown>;
  const cA = (primaryReport.cost_breakdown ?? {}) as Record<string, unknown>;
  const cB = (compareReport.cost_breakdown ?? {}) as Record<string, unknown>;
  const linA = (primaryReport.lineage ?? {}) as Record<string, unknown>;
  const linB = (compareReport.lineage ?? {}) as Record<string, unknown>;

  const rows = [
    {
      label: "策略版本 (Version)",
      valA: String(linA.strategy_version_id ?? "-"),
      valB: String(linB.strategy_version_id ?? "-"),
      delta: null,
    },
    {
      label: "累计收益率",
      valA: formatPercent(Number(mA.cumulative_return ?? 0), {
        inputIsRatio: true,
        showPlusSign: true,
      }),
      valB: formatPercent(Number(mB.cumulative_return ?? 0), {
        inputIsRatio: true,
        showPlusSign: true,
      }),
      delta: Number(mA.cumulative_return ?? 0) - Number(mB.cumulative_return ?? 0),
      deltaMode: "ratioPercent" as const,
    },
    {
      label: "最大回撤",
      valA: formatPercent(-Math.abs(Number(mA.max_drawdown ?? 0)), {
        inputIsRatio: true,
      }),
      valB: formatPercent(-Math.abs(Number(mB.max_drawdown ?? 0)), {
        inputIsRatio: true,
      }),
      delta:
        -Math.abs(Number(mA.max_drawdown ?? 0)) -
        -Math.abs(Number(mB.max_drawdown ?? 0)),
      deltaMode: "ratioPercent" as const,
    },
    {
      label: "夏普比率 (Sharpe)",
      valA: Number(mA.sharpe ?? 0).toFixed(2),
      valB: Number(mB.sharpe ?? 0).toFixed(2),
      delta: Number(mA.sharpe ?? 0) - Number(mB.sharpe ?? 0),
      deltaMode: "decimal" as const,
    },
    {
      label: "胜率 (Win Rate)",
      valA: formatPercent(Number(tA.win_rate ?? 0), { inputIsRatio: true }),
      valB: formatPercent(Number(tB.win_rate ?? 0), { inputIsRatio: true }),
      delta: Number(tA.win_rate ?? 0) - Number(tB.win_rate ?? 0),
      deltaMode: "ratioPercent" as const,
    },
    {
      label: "闭合交易笔数",
      valA: String(tA.closed_trade_count ?? 0),
      valB: String(tB.closed_trade_count ?? 0),
      delta: null,
    },
    {
      label: "累计手续费支出",
      valA: formatCurrency(String(cA.total_fees ?? "0")),
      valB: formatCurrency(String(cB.total_fees ?? "0")),
      delta: null,
    },
  ];

  return (
    <div className="rounded-lg border border-blue-500/40 bg-blue-950/15 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-blue-500/30 bg-slate-900/80 px-4 py-2.5 text-xs font-semibold text-white">
        <GitCompare className="h-4 w-4 text-sky-400" />
        <span>两次运行 / 版本并排对比 (A vs B)</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs tabular-nums-strict">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              <th className="px-3.5 py-2">对比维度</th>
              <th className="px-3.5 py-2 text-right text-sky-300">
                当前报告 A ({primaryRunId.slice(0, 14)}...)
              </th>
              <th className="px-3.5 py-2 text-right text-violet-300">
                对比基准 B ({compareRunId.slice(0, 14)}...)
              </th>
              <th className="px-3.5 py-2 text-right">差值 (A - B)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-t border-slate-800/80">
                <td className="px-3.5 py-2 font-sans text-slate-200">{r.label}</td>
                <td className="px-3.5 py-2 text-right font-semibold text-white">
                  {r.valA}
                </td>
                <td className="px-3.5 py-2 text-right text-slate-300">{r.valB}</td>
                <td className="px-3.5 py-2 text-right">
                  {r.delta !== null && r.deltaMode ? (
                    <NumericDisplay
                      value={r.delta}
                      mode={r.deltaMode}
                      coloredBySign
                      showPlusSign
                    />
                  ) : (
                    <span className="text-slate-500">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
