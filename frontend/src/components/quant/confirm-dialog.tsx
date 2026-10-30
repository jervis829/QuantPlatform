"use client";

import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { quantApi } from "../../lib/apiClient";
import { formatCurrency } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { NumericDisplay } from "./numeric-display";

/**
 * Generic multi-tier confirmation dialog.
 * When `confirmPhrase` is provided (e.g. strategy_id for LIVE deployment),
 * user must type the exact phrase before the confirm button unlocks.
 */
export function ConfirmActionDialog({
  open,
  onOpenChange,
  title,
  description,
  dangerLevel = "caution",
  confirmPhrase,
  confirmLabel = "确认执行",
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  dangerLevel?: "safe" | "caution" | "danger";
  confirmPhrase?: string;
  confirmLabel?: string;
  onConfirm: () => Promise<void> | void;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);

  const unlocked = !confirmPhrase || typed.trim() === confirmPhrase;

  const handleConfirm = async () => {
    if (!unlocked) return;
    setSubmitting(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={
          dangerLevel === "danger"
            ? "border-rose-500/60"
            : dangerLevel === "caution"
              ? "border-amber-500/50"
              : "border-border"
        }
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {dangerLevel === "danger" && (
              <AlertTriangle className="h-4 w-4 text-rose-400" />
            )}
            <span>{title}</span>
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {children && <div className="mt-3 text-xs">{children}</div>}

        {confirmPhrase && (
          <div className="mt-4 rounded-md border border-rose-500/35 bg-rose-950/20 p-3">
            <label className="block text-xs text-slate-200">
              防误触校验：请输入确认词{" "}
              <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono font-bold text-rose-300">
                {confirmPhrase}
              </code>{" "}
              以解锁提交
            </label>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={`输入 ${confirmPhrase}`}
              className="mt-2 w-full rounded border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-xs text-white focus:border-rose-400 focus:outline-none"
            />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            取消
          </Button>
          <Button
            variant={
              dangerLevel === "danger"
                ? "liveDanger"
                : dangerLevel === "caution"
                  ? "shadow"
                  : "default"
            }
            disabled={!unlocked || submitting}
            onClick={() => void handleConfirm()}
          >
            {submitting ? "执行中..." : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Principle 1: Persistent top-right Kill Switch two-step confirmation modal.
 * Fetches live `/api/v1/risk/kill-switch/preview` and displays exact open orders count & positions to be flattened.
 */
export function KillSwitchModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();
  const [closePositions, setClosePositions] = useState(true);
  const [reason, setReason] = useState("manual_emergency_halt");
  const [lastResult, setLastResult] = useState<{
    canceled: number;
    flattened: number;
    elapsedMs: number;
  } | null>(null);

  useEffect(() => {
    if (open) {
      setLastResult(null);
    }
  }, [open]);

  const previewQuery = useQuery({
    queryKey: ["kill-switch-preview", apiKey, open],
    queryFn: () => quantApi.previewKillSwitch(apiKey),
    enabled: open,
    refetchInterval: open ? 3000 : false,
  });

  const killMutation = useMutation({
    mutationFn: () =>
      quantApi.triggerKillSwitch(apiKey, {
        close_positions: closePositions,
        reason: reason.trim() || "manual_emergency_halt",
      }),
    onSuccess: (res) => {
      setLastResult({
        canceled: res.canceled_orders,
        flattened: res.flattened_positions,
        elapsedMs: res.elapsed_ms,
      });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
      void queryClient.invalidateQueries({ queryKey: ["risk-events"] });
      void queryClient.invalidateQueries({ queryKey: ["kill-switch-preview"] });
    },
  });

  const preview = previewQuery.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl border-2 border-rose-500/70 bg-slate-950">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-rose-300">
            <ShieldAlert className="h-5 w-5 text-rose-400" />
            <span>紧急停机熔断确认 (EMERGENCY KILL SWITCH)</span>
          </DialogTitle>
          <DialogDescription>
            此操作将立即阻断后续实盘委托，按所选策略撤销挂单并平掉敞口，全过程写入防篡改哈希链审计日志。
          </DialogDescription>
        </DialogHeader>

        {/* Impact Summary */}
        <div className="mt-3 grid grid-cols-2 gap-3 rounded-lg border border-rose-500/35 bg-rose-950/20 p-3.5">
          <div>
            <div className="text-[11px] text-slate-400">将被撤销的活动挂单</div>
            <div className="mt-1 font-mono text-lg font-bold text-white tabular-nums-strict">
              {preview ? `${preview.open_orders_count} 笔` : "读取中..."}
            </div>
            <div className="text-[11px] font-mono text-slate-400">
              挂单名义额：{preview ? formatCurrency(preview.open_orders_notional_usdt) : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] text-slate-400">将被市价平掉的实盘持仓</div>
            <div className="mt-1 font-mono text-lg font-bold text-rose-300 tabular-nums-strict">
              {preview
                ? closePositions
                  ? `${preview.open_positions_count} 个仓位`
                  : "0 个 (仅撤单保留持仓)"
                : "读取中..."}
            </div>
            <div className="text-[11px] font-mono text-slate-400">
              总敞口：{preview ? formatCurrency(preview.gross_position_notional_usdt) : "—"}
            </div>
          </div>
        </div>

        {/* Position / Order Breakdown */}
        {preview && preview.positions.length > 0 && (
          <div className="mt-2.5 max-h-32 overflow-y-auto rounded border border-slate-800 bg-slate-900/70 p-2 text-xs">
            <div className="mb-1 text-[11px] font-semibold text-slate-300">当前待平持仓明细：</div>
            {preview.positions.map((p, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between py-1 font-mono text-[11px] border-b border-slate-800/60 last:border-none"
              >
                <span>
                  {p.symbol} <strong className="text-amber-300">{p.position_side}</strong> qty={p.quantity}
                </span>
                <NumericDisplay
                  value={p.unrealized_pnl_usdt}
                  mode="currency"
                  coloredBySign
                  showPlusSign
                />
              </div>
            ))}
          </div>
        )}

        {/* Controls */}
        <div className="mt-3 space-y-2.5 text-xs">
          <label className="flex items-center gap-2 rounded border border-slate-800 bg-slate-900/60 p-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={closePositions}
              onChange={(e) => setClosePositions(e.target.checked)}
              className="h-4 w-4 accent-rose-500"
            />
            <span className="text-slate-200 font-medium">
              全撤挂单同时以 <code className="text-rose-300">Reduce-Only MARKET</code> 平掉所有实盘持仓
            </span>
          </label>

          <div>
            <label className="block text-[11px] text-slate-400 mb-1">触发原因（记录至审计链）：</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-1.5 font-mono text-xs text-white focus:border-rose-400 focus:outline-none"
            />
          </div>
        </div>

        {killMutation.isError && (
          <div className="mt-3 rounded border border-rose-500/50 bg-rose-950/40 p-2.5 text-xs text-rose-200">
            执行失败：{String(killMutation.error)}
          </div>
        )}

        {lastResult && (
          <div className="mt-3 rounded border border-emerald-500/40 bg-emerald-950/30 p-2.5 text-xs text-emerald-200 font-mono">
            已执行紧急熔断：撤单 {lastResult.canceled} 笔 · 平仓 {lastResult.flattened} 个 · 耗时{" "}
            {lastResult.elapsedMs.toFixed(2)}ms
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          <Button
            variant="killSwitch"
            disabled={killMutation.isPending}
            onClick={() => killMutation.mutate()}
          >
            {killMutation.isPending ? "正在执行紧急熔断..." : "确认立即触发 KILL SWITCH"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
