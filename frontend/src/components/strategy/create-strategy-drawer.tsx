"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Code2, Sparkles } from "lucide-react";
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

export interface CreateStrategyModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultMode?: "python" | "nl";
}

export function CreateStrategyModal({
  open,
  onOpenChange,
  defaultMode = "nl",
}: CreateStrategyModalProps) {
  const router = useRouter();
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<"python" | "nl">(defaultMode);
  const [name, setName] = useState("BTC 双均线趋势策略");
  const [description, setDescription] = useState("均线金叉买入，死叉平仓");
  const [customId, setCustomId] = useState("");
  const [showAdvancedId, setShowAdvancedId] = useState(false);
  const [code, setCode] = useState(DEFAULT_PYTHON_TEMPLATE);
  const [prompt, setPrompt] = useState(
    "当 BTC/USDT:USDT 5周期均线上穿 14周期均线时买入 0.05 BTC，下穿时平仓"
  );

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
        return quantApi.uploadStrategy(apiKey, {
          strategy_id: resolvedStrategyId,
          name: name.trim(),
          description: description.trim(),
          code,
        });
      }
      return quantApi.generateNLStrategy(apiKey, {
        strategy_id: resolvedStrategyId,
        name: name.trim(),
        description: description.trim(),
        prompt,
      });
    },
    onSuccess: (ver) => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      onOpenChange(false);
      router.push(`/strategies/${encodeURIComponent(ver.strategy_id)}`);
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
            支持直接用中文描述买卖规则自动生成策略，也支持直接粘贴 Python 策略代码。
          </DialogDescription>
        </DialogHeader>

        {/* Mode Switcher — Plain Language, NL first */}
        <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-slate-950 p-1">
          <button
            type="button"
            onClick={() => setMode("nl")}
            className={`flex items-center justify-center gap-2 rounded py-2 text-xs font-medium ${
              mode === "nl"
                ? "bg-violet-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>一句话生成策略（新手推荐）</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("python")}
            className={`flex items-center justify-center gap-2 rounded py-2 text-xs font-medium ${
              mode === "python"
                ? "bg-blue-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Code2 className="h-3.5 w-3.5" />
            <span>编写 Python 代码（进阶）</span>
          </button>
        </div>

        {createMutation.isPending ? (
          <RunLoadingState
            title={
              mode === "python"
                ? "正在安全检查并保存您的策略代码..."
                : "正在根据您的描述自动编写策略并执行回测验证..."
            }
            currentStageLabel="自动检查代码安全性与交易规则有效性"
            progressPct={65}
          />
        ) : (
          <div className="space-y-3 text-xs">
            {createMutation.isError && (
              <ActionableErrorState
                title="策略保存未通过"
                reasonCode={apiErr?.reasonCode ?? "STRATEGY_VALIDATION_FAILED"}
                message={apiErr?.message ?? String(createMutation.error)}
                remediation="请检查买卖规则描述是否清晰，或检查代码语法是否正确。"
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
                  placeholder="例如：BTC 双均线趋势策略"
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

            {mode === "nl" ? (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-1">
                  <label className="text-slate-200 font-medium">
                    用一句话描述您的买卖规则 *
                  </label>
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="text-slate-400">快速填入示例:</span>
                    <button
                      type="button"
                      onClick={() => {
                        setName("BTC 双均线趋势策略");
                        setPrompt(
                          "当 BTC/USDT:USDT 5周期均线上穿 14周期均线时买入 0.05 BTC，下穿时平仓"
                        );
                      }}
                      className="rounded border border-violet-500/40 bg-violet-950/30 px-2 py-0.5 text-violet-200 hover:bg-violet-900/40"
                    >
                      双均线金叉买入
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setName("ETH RSI 超卖反弹策略");
                        setPrompt(
                          "当 ETH/USDT:USDT 7周期 RSI 低于 32 时买入 0.8 ETH，高于 68 时平仓"
                        );
                      }}
                      className="rounded border border-violet-500/40 bg-violet-950/30 px-2 py-0.5 text-violet-200 hover:bg-violet-900/40"
                    >
                      RSI 超卖抄底
                    </button>
                  </div>
                </div>
                <textarea
                  rows={4}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="例如：当 BTC/USDT:USDT 5周期均线上穿 14周期均线时买入 0.05 BTC，下穿时平仓"
                  className="w-full rounded border border-input bg-[#060912] p-3 text-xs text-slate-100 focus:border-violet-500 focus:outline-none"
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
                {showAdvancedId ? "▾ 收起高级选项" : "▸ 高级选项（自定义策略英文 ID，默认自动生成）"}
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
            disabled={createMutation.isPending || !name.trim()}
            onClick={() => createMutation.mutate()}
          >
            {mode === "nl" ? "立即生成策略" : "保存策略"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
