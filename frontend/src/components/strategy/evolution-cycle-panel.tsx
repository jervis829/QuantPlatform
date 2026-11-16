"use client";

import React, { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronUp,
  Loader2,
  Play,
  Radio,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Square,
} from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { usePreferences } from "../../lib/preferences-context";
import { formatUtcTimestamp } from "../../lib/formatters";
import type {
  EvolutionCycleStatusResponse,
  RealtimeShadowSessionResponse,
  ShadowAnalysisResponse,
} from "../../types/openapi.generated";

const DIAGNOSIS_LABELS_ZH: Record<string, string> = {
  HEALTHY: "运行健康",
  NO_CLOSED_TRADES: "未完成闭环交易",
  OVER_TRADING: "交易频率偏高",
  FEE_DRAG_HIGH: "手续费磨损偏高",
  DRAWDOWN_BREACH: "回撤超红线",
  WIN_RATE_LOW: "胜率偏低",
  PROFIT_FACTOR_LOW: "盈亏因子偏低",
  HOLD_TOO_LONG: "持仓时长过长",
};

const DIAGNOSIS_BADGE_CLS: Record<string, string> = {
  HEALTHY: "border-emerald-500/40 bg-emerald-950/40 text-emerald-300",
  NO_CLOSED_TRADES: "border-sky-500/40 bg-sky-950/40 text-sky-300",
  OVER_TRADING: "border-amber-500/40 bg-amber-950/40 text-amber-300",
  FEE_DRAG_HIGH: "border-amber-500/40 bg-amber-950/40 text-amber-300",
  DRAWDOWN_BREACH: "border-rose-500/40 bg-rose-950/40 text-rose-300",
  WIN_RATE_LOW: "border-amber-500/40 bg-amber-950/40 text-amber-300",
  PROFIT_FACTOR_LOW: "border-amber-500/40 bg-amber-950/40 text-amber-300",
  HOLD_TOO_LONG: "border-sky-500/40 bg-sky-950/40 text-sky-300",
};

const MUTATION_LABELS_ZH: Record<string, string> = {
  WIDEN_HORIZON_COOLDOWN: "拉宽信号周期 + 冷却防磨损",
  ADD_MACRO_TREND_FILTER: "注入宏观 EMA 顺大势过滤",
  ADD_STOP_AND_TRAILING: "注入硬止损 + 峰值移动止盈",
  WIDEN_PROFIT_TARGET: "放宽右侧趋势盈亏比空间",
};

export interface EvolutionCyclePanelProps {
  strategyId: string;
  latestVersionId: string;
  latestShadowRunId: string | null;
  onEvolved?: (versionId: string) => void;
}

function KpiCell({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "plain" }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div
        className={cn(
          "mt-0.5 font-mono text-sm font-semibold",
          tone === "pos" && "text-emerald-400",
          tone === "neg" && "text-rose-400",
          (!tone || tone === "plain") && "text-zinc-100"
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function EvolutionCyclePanel({
  strategyId,
  latestVersionId,
  latestShadowRunId,
  onEvolved,
}: EvolutionCyclePanelProps) {
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(true);
  const [evolving, setEvolving] = useState(false);
  const [evolveError, setEvolveError] = useState<string | null>(null);
  const [evolveNote, setEvolveNote] = useState<string | null>(null);
  const [intervalDays, setIntervalDays] = useState(3);
  const [savingCycle, setSavingCycle] = useState(false);
  const [startingRt, setStartingRt] = useState(false);
  const [stoppingRt, setStoppingRt] = useState<string | null>(null);

  const cycleQuery = useQuery({
    queryKey: ["evolution-cycle", strategyId],
    queryFn: () => quantApi.getEvolutionCycle(apiKey, strategyId),
    retry: false,
  });

  const shadowQuery = useQuery({
    queryKey: ["shadow-analysis", latestShadowRunId],
    queryFn: () => quantApi.getShadowAnalysis(apiKey, latestShadowRunId as string),
    enabled: !!latestShadowRunId,
    retry: false,
  });

  const rtQuery = useQuery({
    queryKey: ["realtime-shadow-active"],
    queryFn: () => quantApi.listRealtimeShadow(apiKey),
    refetchInterval: 5000,
    retry: false,
  });
  const rtSessions: RealtimeShadowSessionResponse[] = rtQuery.data ?? [];

  const cycle: EvolutionCycleStatusResponse | undefined = cycleQuery.data;
  const shadow: ShadowAnalysisResponse | undefined = shadowQuery.data;

  const handleStartRt = useCallback(async () => {
    setStartingRt(true);
    try {
      await quantApi.startRealtimeShadow(apiKey, {
        strategy_id: strategyId,
        version_id: latestVersionId,
        exchange: cycle?.exchange ?? "binance",
        symbol: cycle?.symbol ?? "ETH/USDT:USDT",
        interval: "1h",
        warmup_bars: 500,
      });
      queryClient.invalidateQueries({ queryKey: ["realtime-shadow-active"] });
    } finally {
      setStartingRt(false);
    }
  }, [apiKey, strategyId, latestVersionId, cycle, queryClient]);

  const handleStopRt = useCallback(
    async (runId: string) => {
      setStoppingRt(runId);
      try {
        await quantApi.stopRealtimeShadow(apiKey, runId);
        queryClient.invalidateQueries({ queryKey: ["realtime-shadow-active"] });
      } finally {
        setStoppingRt(null);
      }
    },
    [apiKey, queryClient]
  );

  const handleEvolve = useCallback(async () => {
    setEvolving(true);
    setEvolveError(null);
    setEvolveNote(null);
    try {
      const res = await quantApi.evolveExistingStrategy(apiKey, strategyId, {
        base_version_id: latestVersionId,
        shadow_run_id: latestShadowRunId ?? undefined,
        prompt: "基于模拟盘实证诊断继续优化",
        symbol: cycle?.symbol ?? "ETH/USDT:USDT",
        exchange: cycle?.exchange ?? "binance",
        max_rounds: cycle?.max_rounds ?? 2,
        target_max_drawdown_pct: cycle?.target_max_drawdown_pct ?? "0.25",
        lookback_bars: cycle?.lookback_bars ?? 8760,
      });
      setEvolveNote(
        res.baseline_won
          ? `演化完成：基线版本 ${res.base_version_id} 仍为最优，未生成新版本（演化不降级）。`
          : `演化完成：新胜出版本 ${res.champion_version.version_id}（第 ${res.champion_round} 轮），待人工审批后可用于下一轮模拟盘。`
      );
      queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      queryClient.invalidateQueries({ queryKey: ["evolution-cycle", strategyId] });
      onEvolved?.(res.champion_version.version_id);
    } catch (err) {
      if (err instanceof QuantApiError) {
        setEvolveError(`${err.reasonCode}: ${err.message}`);
      } else {
        setEvolveError(err instanceof Error ? err.message : "演化失败，请稍后重试");
      }
    } finally {
      setEvolving(false);
    }
  }, [apiKey, strategyId, latestVersionId, latestShadowRunId, cycle, queryClient, onEvolved]);

  const handleSaveCycle = useCallback(async () => {
    setSavingCycle(true);
    try {
      await quantApi.configureEvolutionCycle(apiKey, strategyId, {
        enabled: !(cycle?.enabled ?? false),
        interval_days: intervalDays,
        exchange: cycle?.exchange ?? "binance",
        symbol: cycle?.symbol ?? "ETH/USDT:USDT",
        max_rounds: cycle?.max_rounds ?? 2,
        target_max_drawdown_pct: cycle?.target_max_drawdown_pct ?? "0.25",
        lookback_bars: cycle?.lookback_bars ?? 8760,
      });
      queryClient.invalidateQueries({ queryKey: ["evolution-cycle", strategyId] });
    } finally {
      setSavingCycle(false);
    }
  }, [apiKey, strategyId, cycle, intervalDays, queryClient]);

  const cycleEnabled = cycle?.enabled ?? false;

  return (
    <section className="rounded-xl border border-emerald-500/25 bg-zinc-950/80">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400">
            <RefreshCw className="h-4 w-4" />
          </span>
          <div>
            <div className="text-sm font-semibold text-zinc-100">持续进化闭环 · 模拟盘 → 分析 → 再演化</div>
            <div className="text-xs text-zinc-500">
              {cycleEnabled
                ? `每 ${cycle?.interval_days ?? 3} 天自动分析模拟盘并演化新版本（新版本仍需人工审批）`
                : "手动触发：基于最新模拟盘表现再演化一轮"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[11px]",
              cycleEnabled
                ? "border-emerald-500/40 bg-emerald-950/40 text-emerald-300"
                : "border-zinc-700 bg-zinc-900 text-zinc-400"
            )}
          >
            {cycleEnabled ? "自动循环已开启" : "自动循环未开启"}
          </span>
          {expanded ? <ChevronUp className="h-4 w-4 text-zinc-500" /> : <ChevronDown className="h-4 w-4 text-zinc-500" />}
        </div>
      </button>

      {expanded && (
        <div className="space-y-4 border-t border-zinc-800/80 px-4 py-4">
          {/* Cycle status + actions */}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              className="bg-emerald-600 hover:bg-emerald-500"
              onClick={handleEvolve}
              disabled={evolving}
            >
              {evolving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bot className="h-3.5 w-3.5" />}
              <span>{evolving ? "演化中（真实回测）…" : "基于模拟盘表现再演化一轮"}</span>
            </Button>

            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <CalendarClock className="h-3.5 w-3.5" />
              <span>自动循环间隔</span>
              <input
                type="number"
                min={1}
                max={30}
                value={intervalDays}
                onChange={(e) => setIntervalDays(Math.max(1, Math.min(30, Number(e.target.value) || 3)))}
                className="w-14 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-center font-mono text-zinc-100"
              />
              <span>天</span>
              <Button size="sm" variant="outline" onClick={handleSaveCycle} disabled={savingCycle}>
                {savingCycle ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                <span>{cycleEnabled ? "暂停自动循环" : "开启自动循环"}</span>
              </Button>
            </div>

            {cycle && (
              <div className="text-xs text-zinc-500">
                上次演化：{cycle.last_evolved_ms ? formatUtcTimestamp(cycle.last_evolved_ms) : "从未"}
                {cycle.next_evolution_due_ms ? ` · 下次预计 ${formatUtcTimestamp(cycle.next_evolution_due_ms)}` : ""}
                {cycle.last_status !== "IDLE" && ` · 状态 ${cycle.last_status}`}
                {cycle.last_error && <span className="text-rose-400"> · {cycle.last_error}</span>}
              </div>
            )}
          </div>

          {evolveError && (
            <div className="rounded-md border border-rose-500/40 bg-rose-950/30 px-3 py-2 text-xs text-rose-300">
              {evolveError}
            </div>
          )}
          {evolveNote && (
            <div className="rounded-md border border-emerald-500/40 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300">
              {evolveNote}
            </div>
          )}

          {/* Real-time shadow (live WS klines, paper fills) */}
          <div className="space-y-2 rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Radio className="h-3.5 w-3.5 text-emerald-400" />
              <span className="text-xs font-medium text-zinc-300">实时模拟盘（币安实时 K 线 · 纸面成交）</span>
              <Button size="sm" variant="outline" onClick={handleStartRt} disabled={startingRt}>
                {startingRt ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                <span>{startingRt ? "启动中…" : "启动实时模拟盘"}</span>
              </Button>
              <span className="text-[11px] text-zinc-500">
                需当前版本已批准；启动后实时接收币安 K 线，成交为纸面模拟
              </span>
            </div>
            {rtSessions.length > 0 && (
              <div className="space-y-1.5">
                {rtSessions.map((s) => (
                  <div
                    key={s.run_id}
                    className="flex flex-wrap items-center gap-2 rounded border border-emerald-500/25 bg-emerald-950/20 px-2.5 py-1.5 text-xs"
                  >
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
                    </span>
                    <span className="font-mono text-zinc-200">{s.run_id}</span>
                    <span className="text-zinc-400">
                      {s.symbol} · {s.interval} · 已处理 {s.bars_processed} 根 K 线（实时 {s.live_bars_received} 根）
                    </span>
                    <span className="rounded-full border border-emerald-500/40 px-1.5 py-0.5 text-[10px] text-emerald-300">
                      {s.status}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      className="ml-auto"
                      onClick={() => handleStopRt(s.run_id)}
                      disabled={stoppingRt === s.run_id}
                    >
                      {stoppingRt === s.run_id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Square className="h-3 w-3" />
                      )}
                      <span>停止</span>
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Shadow analysis */}
          {latestShadowRunId ? (
            shadowQuery.isLoading ? (
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在分析模拟盘成交记录…
              </div>
            ) : shadow ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Activity className="h-3.5 w-3.5 text-zinc-400" />
                  <span className="text-xs font-medium text-zinc-300">
                    最新模拟盘分析 · {shadow.run_id}（观察 {shadow.observation_days} 天）
                  </span>
                  {(shadow.diagnosis_tags ?? []).map((tag) => (
                    <span
                      key={tag}
                      className={cn(
                        "rounded-full border px-2 py-0.5 text-[11px]",
                        DIAGNOSIS_BADGE_CLS[tag] ?? "border-zinc-700 bg-zinc-900 text-zinc-300"
                      )}
                    >
                      {DIAGNOSIS_LABELS_ZH[tag] ?? tag}
                    </span>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
                  <KpiCell
                    label="已实现盈亏"
                    value={`$${Number(shadow.total_realized_pnl_usdt).toLocaleString(undefined, { maximumFractionDigits: 1 })}`}
                    tone={Number(shadow.total_realized_pnl_usdt) >= 0 ? "pos" : "neg"}
                  />
                  <KpiCell label="闭环交易" value={`${shadow.closed_trade_count ?? 0} 笔`} />
                  <KpiCell label="胜率" value={`${((shadow.win_rate ?? 0) * 100).toFixed(1)}%`} />
                  <KpiCell label="盈亏因子" value={`${(shadow.profit_factor ?? 0).toFixed(2)}x`} />
                  <KpiCell label="最大回撤" value={`${((shadow.max_drawdown_pct ?? 0) * 100).toFixed(1)}%`} tone="neg" />
                  <KpiCell label="手续费磨损" value={`${((shadow.fee_drag_ratio ?? 0) * 100).toFixed(1)}%`} />
                  <KpiCell label="交易频率" value={`${(shadow.trades_per_day ?? 0).toFixed(1)} 笔/天`} />
                  <KpiCell label="平均持仓" value={`${(shadow.avg_holding_hours ?? 0).toFixed(0)} 小时`} />
                  <KpiCell label="期望值" value={`$${(shadow.expectancy_usdt ?? 0).toFixed(1)}`} tone={(shadow.expectancy_usdt ?? 0) >= 0 ? "pos" : "neg"} />
                  <KpiCell label="平均盈利" value={`$${(shadow.avg_win_usdt ?? 0).toFixed(1)}`} tone="pos" />
                  <KpiCell label="平均亏损" value={`$${(shadow.avg_loss_usdt ?? 0).toFixed(1)}`} tone="neg" />
                  <KpiCell label="成交笔数" value={`${shadow.fill_count}`} />
                </div>

                {shadow.diagnosis_summary_zh && (
                  <div className="rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-xs leading-relaxed text-zinc-300">
                    {shadow.diagnosis_summary_zh}
                  </div>
                )}

                {(shadow.recommended_mutations ?? []).length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 text-xs">
                    <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
                    <span className="text-zinc-400">下一轮演化优先改造：</span>
                    {(shadow.recommended_mutations ?? []).map((m) => (
                      <span key={m} className="rounded border border-amber-500/30 bg-amber-950/30 px-1.5 py-0.5 text-amber-300">
                        {MUTATION_LABELS_ZH[m] ?? m}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-xs text-zinc-500">模拟盘分析暂不可用（该运行可能尚无成交记录）。</div>
            )
          ) : (
            <div className="flex items-start gap-2 rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2.5 text-xs leading-relaxed text-zinc-400">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
              <span>
                尚未运行模拟盘。流程：批准当前版本 → 启动模拟盘运行 → 积累几天交易记录后点击上方
                「再演化一轮」，系统会用真实成交诊断驱动下一轮结构进化；或开启自动循环后由后台调度器按间隔自动执行。
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
