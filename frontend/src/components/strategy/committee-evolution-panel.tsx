"use client";

import React, { useState } from "react";
import {
  Award,
  Bot,
  ChevronDown,
  ChevronUp,
  Code2,
  ExternalLink,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { cn } from "../../lib/utils";
import type {
  CommitteeDirectorThesis,
  CommitteeMarketProfile,
  CommitteeRoundSummary,
} from "../../types/openapi.generated";

const MUTATION_LABELS_ZH: Record<string, string> = {
  BASELINE_THESIS: "首轮基准原型",
  WIDEN_HORIZON_COOLDOWN: "拉宽信号周期 + 冷却防磨损",
  ADD_MACRO_TREND_FILTER: "注入宏观 EMA 顺大势过滤",
  ADD_STOP_AND_TRAILING: "注入硬止损 + 峰值移动止盈",
  WIDEN_PROFIT_TARGET: "放宽右侧趋势盈亏比空间",
};

const REGIME_BADGES: Record<string, { label: string; cls: string }> = {
  STRONG_TREND: {
    label: "强趋势扩张体制",
    cls: "border-emerald-500/40 bg-emerald-950/40 text-emerald-300",
  },
  VOLATILE_SWING: {
    label: "高波动宽幅波段体制",
    cls: "border-amber-500/40 bg-amber-950/40 text-amber-300",
  },
  MEAN_REVERTING_CHOP: {
    label: "箱体震荡均值回归体制",
    cls: "border-sky-500/40 bg-sky-950/40 text-sky-300",
  },
};

const VERDICT_BADGES: Record<string, { label: string; cls: string }> = {
  PASS_ELITE: {
    label: "风控达标优选",
    cls: "border-emerald-500/40 bg-emerald-950/40 text-emerald-300",
  },
  NEEDS_REFINEMENT: {
    label: "建议继续精调",
    cls: "border-sky-500/40 bg-sky-950/40 text-sky-300",
  },
  HIGH_RISK_WARNING: {
    label: "高风险打回重塑",
    cls: "border-rose-500/40 bg-rose-950/40 text-rose-300",
  },
};

export interface CommitteeEvolutionPanelProps {
  backtestGate: Record<string, unknown> | null | undefined;
  onSelectRoundVersion?: (versionId: string) => void;
}

export function CommitteeEvolutionPanel({
  backtestGate,
  onSelectRoundVersion,
}: CommitteeEvolutionPanelProps) {
  const [expanded, setExpanded] = useState(true);

  if (!backtestGate || backtestGate.mode !== "multi_agent_committee") {
    return null;
  }

  const profile = backtestGate.market_profile as CommitteeMarketProfile | undefined;
  const thesis = backtestGate.director_thesis as CommitteeDirectorThesis | undefined;
  const rounds = (backtestGate.rounds as CommitteeRoundSummary[] | undefined) ?? [];
  const championRound = Number(backtestGate.champion_round ?? 1);
  const llmMode = String(backtestGate.llm_mode ?? "deterministic_specialist");

  if (!profile || !thesis || rounds.length === 0) {
    return null;
  }

  const regimeBadge = REGIME_BADGES[profile.regime_label] ?? {
    label: profile.regime_label,
    cls: "border-slate-700 bg-slate-900 text-slate-300",
  };

  return (
    <section className="rounded-lg border border-violet-500/35 bg-gradient-to-b from-violet-950/20 via-slate-950/80 to-slate-950 p-4 space-y-3.5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-violet-500/50 bg-violet-950/60 px-2.5 py-1 text-xs font-semibold text-violet-200">
            <Bot className="h-3.5 w-3.5 text-violet-300" />
            <span>多智能体投研委员会 · 真实回测闭环演化报告</span>
          </span>
          <span
            className={cn(
              "rounded border px-2 py-0.5 text-[11px] font-medium",
              regimeBadge.cls
            )}
          >
            {regimeBadge.label}
          </span>
          <span className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[11px] text-slate-300">
            {llmMode === "openai_compatible_hybrid"
              ? "外部大模型 + 量化规则双核"
              : "本地确定性量化专家引擎"}
          </span>
          <span className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-amber-950/35 px-2 py-0.5 text-[11px] font-semibold text-amber-200">
            <Award className="h-3 w-3 text-amber-300" />
            <span>胜出定版：第 {championRound} 轮</span>
          </span>
        </div>

        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-slate-800 bg-slate-900/90 px-2.5 py-1 text-xs text-slate-300 hover:border-slate-700 hover:text-white"
        >
          <span>{expanded ? "收起演化详情" : "展开演化详情"}</span>
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      {expanded && (
        <div className="space-y-3.5">
          {/* Tier 1: Director Thesis & Market Empirical Profile */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
            <div className="rounded-md border border-slate-800 bg-slate-900/60 p-3 space-y-1.5 lg:col-span-5">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-sky-300">
                <TrendingUp className="h-3.5 w-3.5" />
                <span>总监立意官 (Director Agent) · 实证盘面诊断</span>
              </div>
              <p className="text-xs leading-relaxed text-slate-300">
                {profile.regime_summary_zh}
              </p>
              <div className="flex flex-wrap gap-3 pt-1 font-mono text-[11px] text-slate-400">
                <span>
                  样本: <strong className="text-white">{profile.bar_count}</strong> 根{" "}
                  {profile.interval}
                </span>
                <span>
                  基准涨跌:{" "}
                  <strong className="text-white">
                    {profile.buy_and_hold_return_pct >= 0 ? "+" : ""}
                    {profile.buy_and_hold_return_pct.toFixed(1)}%
                  </strong>
                </span>
                <span>
                  年化波动:{" "}
                  <strong className="text-white">
                    {profile.annualized_volatility_pct.toFixed(1)}%
                  </strong>
                </span>
                <span>
                  效率系数:{" "}
                  <strong className="text-white">
                    {profile.efficiency_ratio.toFixed(2)}
                  </strong>
                </span>
              </div>
            </div>

            <div className="rounded-md border border-slate-800 bg-slate-900/60 p-3 space-y-1.5 lg:col-span-7">
              <div className="text-xs font-semibold text-violet-300">
                立意架构：{thesis.thesis_title}
              </div>
              <div className="grid grid-cols-1 gap-1.5 text-xs text-slate-300 sm:grid-cols-2">
                <div>
                  <span className="text-slate-400">开仓逻辑：</span>
                  <span>{thesis.entry_logic_zh}</span>
                </div>
                <div>
                  <span className="text-slate-400">离场逻辑：</span>
                  <span>{thesis.exit_logic_zh}</span>
                </div>
              </div>
              <div className="text-[11px] text-slate-400">
                风控预案：{thesis.risk_plan_zh}
              </div>
            </div>
          </div>

          {/* Tier 2: Round-by-Round Empirical Backtest & Risk Critic Evolution Table */}
          <div className="space-y-2">
            {rounds.map((r) => {
              const isChampion = r.round_index === championRound;
              const verdictMeta = VERDICT_BADGES[r.critic_verdict] ?? {
                label: r.critic_verdict,
                cls: "border-slate-700 bg-slate-900 text-slate-300",
              };
              const retPct = r.cumulative_return * 100;
              const ddPct = Math.abs(r.max_drawdown) * 100;

              return (
                <div
                  key={r.round_index}
                  className={cn(
                    "rounded-md border p-3 transition-colors",
                    isChampion
                      ? "border-emerald-500/50 bg-emerald-950/15"
                      : "border-slate-800 bg-slate-900/45"
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs font-bold text-white">
                        第 {r.round_index} 轮 · v{r.version}
                      </span>
                      {isChampion && (
                        <span className="inline-flex items-center gap-1 rounded border border-emerald-500/50 bg-emerald-900/40 px-2 py-0.5 text-[11px] font-semibold text-emerald-200">
                          <Award className="h-3 w-3 text-emerald-300" />
                          <span>最优胜出版本</span>
                        </span>
                      )}
                      <span
                        className={cn(
                          "rounded border px-2 py-0.5 text-[11px]",
                          verdictMeta.cls
                        )}
                      >
                        {verdictMeta.label}
                      </span>
                      {(r.mutation_tags ?? []).map((tag) => (
                        <span
                          key={tag}
                          className="rounded border border-violet-500/30 bg-violet-950/35 px-2 py-0.5 text-[11px] text-violet-200"
                        >
                          {MUTATION_LABELS_ZH[tag] ?? tag}
                        </span>
                      ))}
                    </div>

                    <div className="flex items-center gap-2">
                      {onSelectRoundVersion && (
                        <button
                          type="button"
                          onClick={() => onSelectRoundVersion(r.version_id)}
                          className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-200 hover:border-blue-500/60 hover:text-white"
                        >
                          <Code2 className="h-3 w-3 text-sky-400" />
                          <span>对比此轮代码</span>
                        </button>
                      )}
                      <Link
                        href={`/reports/${encodeURIComponent(r.run_id)}`}
                        className="inline-flex items-center gap-1 rounded border border-blue-500/40 bg-blue-950/40 px-2.5 py-1 text-[11px] font-medium text-blue-200 hover:bg-blue-900/50"
                      >
                        <span>查看 K 线买卖点</span>
                        <ExternalLink className="h-3 w-3" />
                      </Link>
                    </div>
                  </div>

                  {/* Metrics Row */}
                  <div className="mt-2.5 grid grid-cols-2 gap-2 rounded bg-slate-950/75 px-3 py-2 font-mono text-xs sm:grid-cols-6">
                    <div>
                      <div className="text-[10px] text-slate-400">累计收益率</div>
                      <div
                        className={cn(
                          "font-semibold",
                          retPct >= 0 ? "text-emerald-400" : "text-rose-400"
                        )}
                      >
                        {retPct >= 0 ? "+" : ""}
                        {retPct.toFixed(2)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-400">最大回撤</div>
                      <div className="font-semibold text-rose-300">
                        -{ddPct.toFixed(2)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-400">夏普比率</div>
                      <div className="font-semibold text-white">
                        {r.sharpe.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-400">胜率 / 盈亏比</div>
                      <div className="font-semibold text-slate-200">
                        {(r.win_rate * 100).toFixed(1)}% / {r.payoff_ratio.toFixed(2)}x
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-400">闭环笔数 / 换手</div>
                      <div className="font-semibold text-slate-200">
                        {r.closed_trade_count} 笔 / {r.turnover_rate.toFixed(1)}x
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-400">风控综合评分</div>
                      <div className="font-semibold text-amber-300">
                        {r.composite_score.toFixed(1)} / 100
                      </div>
                    </div>
                  </div>

                  {/* Coder Summary & Risk Critic Findings */}
                  <div className="mt-2 space-y-1 text-xs">
                    <div className="text-slate-300">
                      <span className="font-medium text-sky-300">
                        策略工程师 (Quant Coder)：
                      </span>
                      <span>{r.coder_summary}</span>
                    </div>
                    {(r.critic_findings ?? []).map((finding, idx) => (
                      <div
                        key={idx}
                        className="flex items-start gap-1.5 text-slate-300"
                      >
                        {r.critic_verdict === "PASS_ELITE" ? (
                          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                        ) : (
                          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                        )}
                        <span>
                          <strong className="text-amber-200">
                            风控评审官 (Risk Critic)：
                          </strong>
                          {finding}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
