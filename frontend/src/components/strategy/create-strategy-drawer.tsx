"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Code2, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { usePreferences } from "../../lib/preferences-context";
import { ActionableErrorState, RunLoadingState } from "../quant/state-views";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

const DEFAULT_PYTHON_TEMPLATE = `from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import sma
from quant_platform.strategy.ui_meta import ui_param


class SmaCrossParams(BaseModel):
    fast_window: int = ui_param(
        5,
        ge=2,
        le=100,
        title="快线均线周期",
        ui_group="signal",
        unit="Bars (K线根数)",
        danger_level="safe",
    )
    slow_window: int = ui_param(
        12,
        ge=3,
        le=300,
        title="慢线均线周期",
        ui_group="signal",
        unit="Bars (K线根数)",
        danger_level="safe",
    )
    order_qty: Decimal = ui_param(
        Decimal("0.05"),
        title="单笔下单数量",
        ui_group="risk_sizing",
        unit="基础资产数量",
        danger_level="high",
        danger_hint="放大下单数量会直接提高单笔名义敞口与保证金占用。",
    )


SmaCrossParams.model_rebuild(_types_namespace={"Decimal": Decimal})


class SmaCrossStrategy(Strategy):
    ParamModel = SmaCrossParams

    def on_start(self):
        self._closes = []
        self._prev_fast = None
        self._prev_slow = None
        return None

    def on_bar(self, bar: Bar):
        self._closes.append(bar.close)
        if len(self._closes) < self.params.slow_window:
            return None
        f_val = sma(self._closes, self.params.fast_window)
        s_val = sma(self._closes, self.params.slow_window)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        cur_qty = pos.quantity if pos is not None else Decimal("0")
        out = None
        if self._prev_fast is not None and self._prev_slow is not None:
            if self._prev_fast <= self._prev_slow and f_val > s_val and cur_qty == Decimal("0"):
                out = [Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.params.order_qty,
                    reason="sma_cross_up",
                    ts_ms=self.ctx.now_ms,
                )]
            elif self._prev_fast >= self._prev_slow and f_val < s_val and cur_qty > Decimal("0"):
                out = [Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=cur_qty,
                    reduce_only=True,
                    reason="sma_cross_down",
                    ts_ms=self.ctx.now_ms,
                )]
        self._prev_fast = f_val
        self._prev_slow = s_val
        return out
`;

export type CreateStrategyMode = "committee" | "nl" | "python";

export interface CreateStrategyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultMode?: CreateStrategyMode;
}

export function CreateStrategyModal({
  open,
  onOpenChange,
  defaultMode = "committee",
}: CreateStrategyModalProps) {
  const router = useRouter();
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<CreateStrategyMode>(defaultMode);
  const [name, setName] = useState("ETH 投研委员会突破演化策略");
  const [description, setDescription] = useState(
    "多智能体协同立意、真实币安 1H 回测检验与风控迭代优化"
  );
  const [customId, setCustomId] = useState("");
  const [showAdvancedId, setShowAdvancedId] = useState(false);
  const [code, setCode] = useState(DEFAULT_PYTHON_TEMPLATE);
  const [prompt, setPrompt] = useState(
    "围绕 ETH/USDT:USDT 设计一套顺大势通道突破策略，控制回撤在 25% 以内，避免震荡市频繁止损磨损"
  );

  // Committee evolution options
  const [symbol, setSymbol] = useState("ETH/USDT:USDT");
  const [maxRounds, setMaxRounds] = useState<number>(3);
  const [lookbackBars, setLookbackBars] = useState<number>(8760);
  const [targetMaxDd, setTargetMaxDd] = useState<string>("0.25");

  React.useEffect(() => {
    if (open) {
      setMode(defaultMode);
    }
  }, [defaultMode, open]);

  const resolvedStrategyId =
    customId.trim() ||
    `strat_${
      name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "") || "custom"
    }_${Math.floor((Date.now() / 1000) % 100000)}`;

  const createMutation = useMutation({
    mutationFn: async () => {
      if (mode === "python") {
        const ver = await quantApi.uploadStrategy(apiKey, {
          strategy_id: resolvedStrategyId,
          name: name.trim(),
          description: description.trim(),
          code,
        });
        return ver.strategy_id;
      }
      if (mode === "committee") {
        const evo = await quantApi.evolveStrategyCommittee(apiKey, {
          strategy_id: resolvedStrategyId,
          name: name.trim(),
          description: description.trim(),
          prompt,
          symbol,
          exchange: "binance",
          max_rounds: maxRounds,
          target_max_drawdown_pct: targetMaxDd,
          initial_usdt: "10000",
          leverage: "2",
          lookback_bars: lookbackBars,
        });
        return evo.strategy_id;
      }
      const ver = await quantApi.generateNLStrategy(apiKey, {
        strategy_id: resolvedStrategyId,
        name: name.trim(),
        description: description.trim(),
        prompt,
      });
      return ver.strategy_id;
    },
    onSuccess: (targetStrategyId) => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      onOpenChange(false);
      router.push(`/strategies/${encodeURIComponent(targetStrategyId)}`);
    },
  });

  const apiErr =
    createMutation.error instanceof QuantApiError ? createMutation.error : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建交易策略</DialogTitle>
          <DialogDescription>
            支持多智能体闭环自动演化（总监立意 → 编写代码 → 真实币安 1H 回测 → 风控挑刺迭代）、一句话极速生成，或直接粘贴 Python 策略代码。
          </DialogDescription>
        </DialogHeader>

        {/* 3-Mode Switcher */}
        <div className="grid grid-cols-3 gap-1.5 rounded-md border border-border bg-slate-950 p-1">
          <button
            type="button"
            onClick={() => setMode("committee")}
            className={`flex items-center justify-center gap-1.5 rounded py-2 text-xs font-medium ${
              mode === "committee"
                ? "bg-emerald-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Bot className="h-3.5 w-3.5" />
            <span>多智能体自动演化（推荐）</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("nl")}
            className={`flex items-center justify-center gap-1.5 rounded py-2 text-xs font-medium ${
              mode === "nl"
                ? "bg-violet-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>一句话极速生成</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("python")}
            className={`flex items-center justify-center gap-1.5 rounded py-2 text-xs font-medium ${
              mode === "python"
                ? "bg-blue-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Code2 className="h-3.5 w-3.5" />
            <span>编写 Python 代码</span>
          </button>
        </div>

        {createMutation.isPending ? (
          <RunLoadingState
            title={
              mode === "committee"
                ? `投研委员会正在执行 ${maxRounds} 轮「编码 → 币安真实 1H 回测 → 风控诊断迭代」...`
                : mode === "python"
                  ? "正在安全检查并保存您的策略代码..."
                  : "正在根据您的描述自动编写策略并执行回测验证..."
            }
            currentStageLabel={
              mode === "committee"
                ? `总监立意官分析 ${symbol} 市场体制 → 策略工程师生成 AST 安全代码 → 跑真实历史回测并由风控官逐轮优选`
                : "自动检查代码安全性与交易规则有效性"
            }
            progressPct={mode === "committee" ? 72 : 65}
          />
        ) : (
          <div className="space-y-3 text-xs">
            {createMutation.isError && (
              <ActionableErrorState
                title="策略生成或保存未通过"
                reasonCode={apiErr?.reasonCode ?? "STRATEGY_VALIDATION_FAILED"}
                message={apiErr?.message ?? String(createMutation.error)}
                remediation="请检查买卖规则描述是否清晰，或检查代码语法是否符合安全沙箱规范。"
                rawDetail={apiErr?.rawDetail}
              />
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-slate-200 font-medium">策略名称 *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="例如：ETH 投研委员会突破演化策略"
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 text-white"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">备注说明（选填）</label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="简单记录策略思路"
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 text-white"
                />
              </div>
            </div>

            {mode === "committee" && (
              <div className="rounded-md border border-emerald-500/30 bg-emerald-950/15 p-3 space-y-2.5">
                <div className="text-[11px] text-emerald-200 font-medium">
                  闭环流程：① 总监立意官诊断实证波动率 → ② 策略工程师合成纯 Decimal 源码 → ③ 币安真实 1H K线+资金费回测 → ④ 首席风控官挑刺并自动进化下一代（保留人工审批门禁）
                </div>
                <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                  <div className="space-y-1">
                    <label className="text-[11px] text-slate-300">回测标的</label>
                    <select
                      value={symbol}
                      onChange={(e) => setSymbol(e.target.value)}
                      className="h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white"
                    >
                      <option value="ETH/USDT:USDT">ETH/USDT 永续</option>
                      <option value="BTC/USDT:USDT">BTC/USDT 永续</option>
                      <option value="SOL/USDT:USDT">SOL/USDT 永续</option>
                      <option value="BNB/USDT:USDT">BNB/USDT 永续</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-[11px] text-slate-300">自动进化轮数</label>
                    <select
                      value={maxRounds}
                      onChange={(e) => setMaxRounds(Number(e.target.value))}
                      className="h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white"
                    >
                      <option value={2}>2 轮迭代</option>
                      <option value={3}>3 轮迭代（推荐）</option>
                      <option value={4}>4 轮深度迭代</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-[11px] text-slate-300">真实历史样本</label>
                    <select
                      value={lookbackBars}
                      onChange={(e) => setLookbackBars(Number(e.target.value))}
                      className="h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white"
                    >
                      <option value={8760}>近 1 年 1H (8,760根)</option>
                      <option value={23760}>全量 2.7 年 1H (23,760根)</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <label className="text-[11px] text-slate-300">最大回撤红线</label>
                    <select
                      value={targetMaxDd}
                      onChange={(e) => setTargetMaxDd(e.target.value)}
                      className="h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 text-xs text-white"
                    >
                      <option value="0.20">控制在 20% 内</option>
                      <option value="0.25">控制在 25% 内</option>
                      <option value="0.30">控制在 30% 内</option>
                    </select>
                  </div>
                </div>
              </div>
            )}

            {mode !== "python" ? (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-1">
                  <label className="text-slate-200 font-medium">
                    {mode === "committee"
                      ? "输入您的投研目标或交易思路 *"
                      : "用一句话描述您的买卖规则 *"}
                  </label>
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="text-slate-400">一键填入模板:</span>
                    <button
                      type="button"
                      onClick={() => {
                        setSymbol("ETH/USDT:USDT");
                        setName("ETH 顺大势通道突破演化策略");
                        setPrompt(
                          "围绕 ETH/USDT:USDT 设计顺大势通道突破策略，控制回撤在 25% 以内，避免震荡频繁止损"
                        );
                      }}
                      className="rounded border border-emerald-500/40 bg-emerald-950/30 px-2 py-0.5 text-emerald-200 hover:bg-emerald-900/40"
                    >
                      通道突破演化
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSymbol("BTC/USDT:USDT");
                        setName("BTC EMA 宏观趋势演化策略");
                        setPrompt(
                          "当 BTC/USDT:USDT 8周期 EMA 上穿 24周期 EMA 时顺势做多，下穿时平仓并抑制高频换手磨损"
                        );
                      }}
                      className="rounded border border-violet-500/40 bg-violet-950/30 px-2 py-0.5 text-violet-200 hover:bg-violet-900/40"
                    >
                      EMA 趋势抗磨损
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSymbol("SOL/USDT:USDT");
                        setName("SOL RSI 超卖回归演化策略");
                        setPrompt(
                          "当 SOL/USDT:USDT RSI 跌入超卖区反弹买入，回到强势区止盈，并加入趋势过滤与移动止损"
                        );
                      }}
                      className="rounded border border-sky-500/40 bg-sky-950/30 px-2 py-0.5 text-sky-200 hover:bg-sky-900/40"
                    >
                      RSI 超卖回归
                    </button>
                  </div>
                </div>
                <textarea
                  rows={4}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="例如：围绕 ETH/USDT:USDT 设计一套顺大势通道突破策略，控制回撤在 25% 以内..."
                  className="w-full rounded border border-input bg-[#060912] p-3 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
                />
              </div>
            ) : (
              <div className="space-y-1">
                <label className="text-slate-200 font-medium">Python 策略代码</label>
                <textarea
                  rows={11}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  spellCheck={false}
                  className="w-full rounded border border-input bg-[#060912] p-3 font-mono text-xs text-slate-100 focus:border-blue-500 focus:outline-none"
                />
              </div>
            )}

            <div>
              <button
                type="button"
                onClick={() => setShowAdvancedId((v) => !v)}
                className="text-[11px] text-slate-400 hover:text-slate-200"
              >
                {showAdvancedId
                  ? "▾ 收起高级选项"
                  : "▸ 高级选项（自定义策略英文 ID，默认自动生成）"}
              </button>
              {showAdvancedId && (
                <div className="mt-1.5 space-y-1">
                  <input
                    type="text"
                    value={customId}
                    onChange={(e) => setCustomId(e.target.value)}
                    placeholder={`留空将自动使用: ${resolvedStrategyId}`}
                    className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
                  />
                </div>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="default"
            className={
              mode === "committee" ? "bg-emerald-600 hover:bg-emerald-500" : undefined
            }
            disabled={createMutation.isPending || !name.trim()}
            onClick={() => createMutation.mutate()}
          >
            {mode === "committee"
              ? `启动 ${maxRounds} 轮自动演化与真实回测`
              : mode === "nl"
                ? "立即生成策略"
                : "保存策略"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
