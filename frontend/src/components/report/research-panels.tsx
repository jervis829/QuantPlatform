"use client";

import React, { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Grid3X3, Play, SplitSquareHorizontal } from "lucide-react";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { formatPercent, formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";
import type {
  SensitivityResponse,
  WalkForwardResponse,
} from "../../types/openapi.generated";
import { NumericDisplay } from "../quant/numeric-display";
import { ActionableErrorState, RunLoadingState } from "../quant/state-views";
import { Button } from "../ui/button";

function buildParamVariations(val: unknown): unknown[] {
  const num = Number(val);
  if (Number.isInteger(num) && num >= 2) {
    const low = Math.max(2, Math.floor(num * 0.7));
    const high = Math.max(num + 1, Math.ceil(num * 1.3));
    return Array.from(new Set([low, num, high]));
  }
  if (Number.isFinite(num) && num > 0) {
    return [
      Number((num * 0.8).toPrecision(4)),
      num,
      Number((num * 1.2).toPrecision(4)),
    ];
  }
  return [val];
}

export interface ResearchRobustnessPanelsProps {
  runId: string;
  versionParams: Record<string, unknown>;
}

export function ResearchRobustnessPanels({
  runId,
  versionParams,
}: ResearchRobustnessPanelsProps) {
  const { apiKey, pnlPolarity } = usePreferences();
  const paramKeys = useMemo(() => Object.keys(versionParams), [versionParams]);

  const [xParam, setXParam] = useState<string>(paramKeys[0] ?? "");
  const [yParam, setYParam] = useState<string>(paramKeys[1] ?? paramKeys[0] ?? "");
  const [trainSize, setTrainSize] = useState<number>(20);
  const [testSize, setTestSize] = useState<number>(10);

  const [heatmapResult, setHeatmapResult] = useState<SensitivityResponse | null>(null);
  const [wfResult, setWfResult] = useState<WalkForwardResponse | null>(null);

  const sensMutation = useMutation({
    mutationFn: () => {
      const px = xParam || paramKeys[0];
      const py = yParam || paramKeys[1] || paramKeys[0];
      return quantApi.runSensitivity(apiKey, runId, {
        param_x_name: px,
        param_x_values: buildParamVariations(versionParams[px]),
        param_y_name: py,
        param_y_values: buildParamVariations(versionParams[py]),
      });
    },
    onSuccess: (res) => setHeatmapResult(res),
  });

  const wfMutation = useMutation({
    mutationFn: () => {
      const candidates: Array<Record<string, unknown>> = [versionParams];
      if (paramKeys.length > 0) {
        const k0 = paramKeys[0];
        buildParamVariations(versionParams[k0]).forEach((v) => {
          candidates.push({ ...versionParams, [k0]: v });
        });
      }
      return quantApi.runWalkForward(apiKey, runId, {
        param_candidates: candidates.slice(0, 4),
        train_size: trainSize,
        test_size: testSize,
      });
    },
    onSuccess: (res) => setWfResult(res),
  });

  const sensErr =
    sensMutation.error instanceof QuantApiError ? sensMutation.error : null;
  const wfErr =
    wfMutation.error instanceof QuantApiError ? wfMutation.error : null;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* Left: 2D Parameter Sensitivity Heatmap */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2.5">
          <div className="flex items-center gap-2">
            <Grid3X3 className="h-4 w-4 text-sky-400" />
            <div>
              <h3 className="text-xs font-semibold text-white">
                参数敏感性热力图 (过拟合高原检验)
              </h3>
              <p className="text-[11px] text-muted-foreground">
                检验基准参数邻域是否处于稳定盈利高原而非孤立过拟合尖峰
              </p>
            </div>
          </div>

          <Button
            size="sm"
            variant="default"
            disabled={sensMutation.isPending || paramKeys.length === 0}
            onClick={() => sensMutation.mutate()}
          >
            <Play className="h-3 w-3 fill-current" />
            <span>{sensMutation.isPending ? "计算中..." : "生成热力图"}</span>
          </Button>
        </div>

        {paramKeys.length > 0 && (
          <div className="grid grid-cols-2 gap-2.5 text-xs">
            <div className="space-y-1">
              <label className="text-slate-400">横轴参数 (X)</label>
              <select
                value={xParam}
                onChange={(e) => setXParam(e.target.value)}
                className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
              >
                {paramKeys.map((k) => (
                  <option key={k} value={k}>
                    {k} (当前={String(versionParams[k])})
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-slate-400">纵轴参数 (Y)</label>
              <select
                value={yParam}
                onChange={(e) => setYParam(e.target.value)}
                className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
              >
                {paramKeys.map((k) => (
                  <option key={k} value={k}>
                    {k} (当前={String(versionParams[k])})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {sensMutation.isPending && (
          <RunLoadingState
            title="正在扫描二维参数网格回测..."
            currentStageLabel="并行评估各参数组合夏普比率与累计收益率"
            progressPct={65}
          />
        )}

        {sensMutation.isError && (
          <ActionableErrorState
            title="敏感性网格计算失败"
            reasonCode={sensErr?.reasonCode ?? "SENSITIVITY_ERROR"}
            message={sensErr?.message ?? String(sensMutation.error)}
            remediation="请选择数值型周期或阈值参数（确保慢线周期大于快线周期）后重试。"
          />
        )}

        {heatmapResult && (
          <div className="overflow-x-auto rounded border border-slate-800">
            <table className="w-full border-collapse text-center font-mono text-[11px] tabular-nums-strict">
              <thead className="bg-slate-900 text-slate-400">
                <tr>
                  <th className="p-2 text-left">
                    {heatmapResult.param_y_name} \ {heatmapResult.param_x_name}
                  </th>
                  {heatmapResult.x_values.map((xv, idx) => (
                    <th key={idx} className="p-2">
                      {String(xv)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmapResult.y_values.map((yv, yi) => (
                  <tr key={yi} className="border-t border-slate-800">
                    <td className="bg-slate-900/70 p-2 text-left font-bold text-white">
                      {String(yv)}
                    </td>
                    {heatmapResult.x_values.map((xv, xi) => {
                      const cell = heatmapResult.cells.find(
                        (c) => String(c.x) === String(xv) && String(c.y) === String(yv)
                      );
                      if (!cell) {
                        return (
                          <td key={xi} className="p-2 text-slate-600">
                            —
                          </td>
                        );
                      }
                      const isPos = cell.cumulative_return >= 0;
                      const goodTone =
                        (isPos && pnlPolarity === "green-up") ||
                        (!isPos && pnlPolarity === "red-up");
                      return (
                        <td
                          key={xi}
                          className={cn(
                            "border-l border-slate-800 p-2",
                            cell.cumulative_return === 0
                              ? "bg-slate-950 text-slate-400"
                              : goodTone
                                ? "bg-emerald-950/60 text-emerald-200"
                                : "bg-rose-950/60 text-rose-200"
                          )}
                        >
                          <div className="font-bold">
                            {formatPercent(cell.cumulative_return, {
                              inputIsRatio: true,
                              showPlusSign: true,
                            })}
                          </div>
                          <div className="text-[10px] opacity-80">
                            SR {cell.sharpe.toFixed(2)} · DD{" "}
                            {(Math.abs(cell.max_drawdown) * -100).toFixed(1)}%
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Right: Walk-Forward Out-of-Sample Rolling Test */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2.5">
          <div className="flex items-center gap-2">
            <SplitSquareHorizontal className="h-4 w-4 text-violet-400" />
            <div>
              <h3 className="text-xs font-semibold text-white">
                Walk-Forward 滚动样本外检验 (IS → OOS)
              </h3>
              <p className="text-[11px] text-muted-foreground">
                滚动样本内寻优并在紧邻未知样本外窗口验证真实泛化能力
              </p>
            </div>
          </div>

          <Button
            size="sm"
            variant="secondary"
            disabled={wfMutation.isPending}
            onClick={() => wfMutation.mutate()}
          >
            <Play className="h-3 w-3 fill-current" />
            <span>{wfMutation.isPending ? "滚动回放中..." : "运行 Walk-Forward"}</span>
          </Button>
        </div>

        <div className="grid grid-cols-2 gap-2.5 text-xs">
          <div className="space-y-1">
            <label className="text-slate-400">训练窗口长度 (train_size Bars)</label>
            <input
              type="number"
              min={10}
              value={trainSize}
              onChange={(e) => setTrainSize(Number(e.target.value) || 15)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
            />
          </div>
          <div className="space-y-1">
            <label className="text-slate-400">样本外测试长度 (test_size Bars)</label>
            <input
              type="number"
              min={5}
              value={testSize}
              onChange={(e) => setTestSize(Number(e.target.value) || 8)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
            />
          </div>
        </div>

        {wfMutation.isPending && (
          <RunLoadingState
            title="正在执行 Walk-Forward 滚动窗口验证..."
            currentStageLabel="切分 IS/OOS 窗口并汇总样本外真实表现"
            progressPct={70}
          />
        )}

        {wfMutation.isError && (
          <ActionableErrorState
            title="Walk-Forward 检验失败"
            reasonCode={wfErr?.reasonCode ?? "WALK_FORWARD_ERROR"}
            message={wfErr?.message ?? String(wfMutation.error)}
            remediation="请适当调小 train_size 或 test_size 以匹配当前数据集 Bar 总长度。"
          />
        )}

        {wfResult && (
          <div className="overflow-x-auto rounded border border-slate-800">
            <table className="w-full text-left font-mono text-[11px] tabular-nums-strict">
              <thead className="bg-slate-900 text-slate-400">
                <tr>
                  <th className="px-2.5 py-2">窗口</th>
                  <th className="px-2.5 py-2">OOS 测试区间</th>
                  <th className="px-2.5 py-2 text-right">IS 评分</th>
                  <th className="px-2.5 py-2 text-right">OOS 收益</th>
                  <th className="px-2.5 py-2 text-right">OOS 夏普</th>
                  <th className="px-2.5 py-2 text-right">OOS 回撤</th>
                </tr>
              </thead>
              <tbody>
                {wfResult.windows.map((w) => (
                  <tr key={w.window_index} className="border-t border-slate-800">
                    <td className="px-2.5 py-1.5 font-bold text-white">
                      #{w.window_index + 1}
                    </td>
                    <td className="px-2.5 py-1.5 text-slate-400">
                      {formatUtcTimestamp(w.test_start_ms, false).slice(5, 16)} →{" "}
                      {formatUtcTimestamp(w.test_end_ms, false).slice(5, 16)}
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-slate-300">
                      {w.in_sample_score.toFixed(2)}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      <NumericDisplay
                        value={w.oos_return}
                        mode="ratioPercent"
                        coloredBySign
                        showPlusSign
                      />
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-slate-200">
                      {w.oos_sharpe.toFixed(2)}
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-rose-300">
                      {(Math.abs(w.oos_max_drawdown) * -100).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
