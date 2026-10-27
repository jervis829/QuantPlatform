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
from pydantic import BaseModel, Field
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import sma


class SmaCrossParams(BaseModel):
    fast_window: int = Field(default=5, ge=2, le=100)
    slow_window: int = Field(default=12, ge=3, le=300)
    order_qty: Decimal = Field(default=Decimal("0.05"))


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
  defaultMode = "python",
}: CreateStrategyModalProps) {
  const router = useRouter();
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<"python" | "nl">(defaultMode);
  const [strategyId, setStrategyId] = useState("strat_btc_sma");
  const [name, setName] = useState("BTC USDT-M SMA Crossover");
  const [description, setDescription] = useState("双均线趋势跟踪策略（只输出声明式 Signal）");
  const [code, setCode] = useState(DEFAULT_PYTHON_TEMPLATE);
  const [prompt, setPrompt] = useState(
    "当 BTC/USDT:USDT 5周期均线上穿 12周期均线时买入 0.05 BTC，下穿时平仓"
  );

  const createMutation = useMutation({
    mutationFn: async () => {
      if (mode === "python") {
        return quantApi.uploadStrategy(apiKey, {
          strategy_id: strategyId.trim(),
          name: name.trim(),
          description: description.trim(),
          code,
        });
      }
      return quantApi.generateNLStrategy(apiKey, {
        strategy_id: strategyId.trim(),
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
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>注册新策略或提交新版本（AST + 隔离沙箱双重校验）</DialogTitle>
          <DialogDescription>
            Python 上传与自然语言生成共享完全一致的受限 AST 静态检查与无网络隔离沙箱；入库后默认 PENDING 待人工审批。
          </DialogDescription>
        </DialogHeader>

        {/* Mode Switcher */}
        <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-slate-950 p-1">
          <button
            type="button"
            onClick={() => setMode("python")}
            className={`flex items-center justify-center gap-2 rounded py-1.5 text-xs font-medium ${
              mode === "python"
                ? "bg-blue-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Code2 className="h-3.5 w-3.5" />
            <span>上传 Python 策略源码</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("nl");
              if (strategyId === "strat_btc_sma") setStrategyId("strat_btc_sma_nl");
            }}
            className={`flex items-center justify-center gap-2 rounded py-1.5 text-xs font-medium ${
              mode === "nl"
                ? "bg-violet-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>自然语言生成策略 (NL → LLM + 回测门禁)</span>
          </button>
        </div>

        {createMutation.isPending ? (
          <RunLoadingState
            title={
              mode === "python"
                ? "正在执行 AST 静态白名单检查与隔离沙箱试运行..."
                : "正在生成策略代码并执行隔离沙箱 + 自动历史回测门禁..."
            }
            currentStageLabel="校验禁止网络/文件/系统调用与前视偏差约束"
            progressPct={65}
          />
        ) : (
          <div className="space-y-3 text-xs">
            {createMutation.isError && (
              <ActionableErrorState
                title="策略校验或注册未通过"
                reasonCode={apiErr?.reasonCode ?? "STRATEGY_VALIDATION_FAILED"}
                message={apiErr?.message ?? String(createMutation.error)}
                remediation="请移除任何禁止导入（os/socket/requests/random/datetime.now）并确保继承 Strategy 基类。"
                rawDetail={apiErr?.rawDetail}
              />
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">策略唯一标识 (strategy_id) *</label>
                <input
                  type="text"
                  value={strategyId}
                  onChange={(e) => setStrategyId(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">策略显示名称 *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 text-white"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">简短描述</label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 text-white"
                />
              </div>
            </div>

            {mode === "python" ? (
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">
                  Python 策略源码（只允许输出声明式 Signal / TargetPosition）
                </label>
                <textarea
                  rows={12}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  spellCheck={false}
                  className="w-full rounded border border-input bg-[#060912] p-3 font-mono text-xs text-slate-100 focus:border-blue-500 focus:outline-none"
                />
              </div>
            ) : (
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">
                  自然语言策略逻辑描述（将自动执行 AST 检查 + 沙箱测试 + 回测门禁）
                </label>
                <textarea
                  rows={5}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  className="w-full rounded border border-input bg-[#060912] p-3 text-xs text-slate-100 focus:border-violet-500 focus:outline-none"
                />
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="default"
            disabled={
              createMutation.isPending || !strategyId.trim() || !name.trim()
            }
            onClick={() => createMutation.mutate()}
          >
            {mode === "python"
              ? "通过 AST+沙箱校验并注册策略"
              : "生成代码并执行沙箱+回测门禁"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
