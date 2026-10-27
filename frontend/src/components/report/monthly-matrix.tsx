"use client";

import React from "react";
import { formatPercent } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";

const MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];

export function MonthlyReturnMatrix({
  matrix,
}: {
  matrix: Record<string, Record<string, number>>;
}) {
  const { pnlPolarity } = usePreferences();
  const years = Object.keys(matrix).sort((a, b) => b.localeCompare(a));

  if (years.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-xs text-muted-foreground">
        当前回测区间不足以跨月聚合月度收益矩阵。
      </div>
    );
  }

  const getCellTone = (val: number | undefined) => {
    if (val === undefined || val === null) return "bg-slate-950/50 text-slate-600";
    if (Math.abs(val) < 1e-8) return "bg-slate-900 text-slate-400";
    const isPos = val > 0;
    const upGreen = pnlPolarity === "green-up";
    if ((isPos && upGreen) || (!isPos && !upGreen)) {
      return Math.abs(val) > 0.05
        ? "bg-emerald-600/35 text-emerald-200 font-semibold"
        : "bg-emerald-950/55 text-emerald-300";
    }
    return Math.abs(val) > 0.05
      ? "bg-rose-600/35 text-rose-200 font-semibold"
      : "bg-rose-950/55 text-rose-300";
  };

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="border-b border-border px-3.5 py-2.5 text-xs font-semibold text-slate-200">
        月度收益矩阵 (Monthly Return Matrix · UTC)
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-right font-mono text-[11px] tabular-nums-strict">
          <thead className="bg-slate-900/90 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">年份</th>
              {MONTHS.map((m) => (
                <th key={m} className="px-2 py-2">
                  {Number(m)}月
                </th>
              ))}
              <th className="px-3 py-2 font-bold text-slate-200">YTD 合计</th>
            </tr>
          </thead>
          <tbody>
            {years.map((yr) => {
              const row = matrix[yr] || {};
              return (
                <tr key={yr} className="border-t border-slate-800">
                  <td className="px-3 py-2 text-left font-bold text-white">{yr}</td>
                  {MONTHS.map((m) => {
                    const v = row[m];
                    return (
                      <td key={m} className={cn("px-2 py-2 border-l border-slate-800/60", getCellTone(v))}>
                        {v !== undefined
                          ? formatPercent(v, { inputIsRatio: true, decimals: 2, showPlusSign: true })
                          : "—"}
                      </td>
                    );
                  })}
                  <td
                    className={cn(
                      "px-3 py-2 border-l-2 border-slate-700 font-bold",
                      getCellTone(row.ytd)
                    )}
                  >
                    {row.ytd !== undefined
                      ? formatPercent(row.ytd, {
                          inputIsRatio: true,
                          decimals: 2,
                          showPlusSign: true,
                        })
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
