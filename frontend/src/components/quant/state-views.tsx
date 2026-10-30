"use client";

import React, { useState } from "react";
import {
  AlertOctagon,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Inbox,
  Loader2,
  XCircle,
} from "lucide-react";
import { formatDurationMs } from "../../lib/formatters";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

/**
 * 1. Empty State: Must provide clear context + guided next-step CTA actions (never a blank box).
 */
export interface EmptyStateAction {
  label: string;
  onClick: () => void;
  variant?: "default" | "outline" | "secondary";
}

export function EmptyState({
  title,
  description,
  icon,
  primaryAction,
  secondaryAction,
  className,
}: {
  title: string;
  description: string;
  icon?: React.ReactNode;
  primaryAction?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700/80 bg-slate-900/40 px-6 py-10 text-center",
        className
      )}
    >
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-slate-700 bg-slate-800/80 text-slate-300">
        {icon ?? <Inbox className="h-5 w-5" />}
      </div>
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">
        {description}
      </p>
      {(primaryAction || secondaryAction) && (
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2.5">
          {primaryAction && (
            <Button
              variant={primaryAction.variant ?? "default"}
              onClick={primaryAction.onClick}
            >
              {primaryAction.label}
            </Button>
          )}
          {secondaryAction && (
            <Button
              variant={secondaryAction.variant ?? "outline"}
              onClick={secondaryAction.onClick}
            >
              {secondaryAction.label}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 2. Loading State: Quant backtests / NL pipelines can run seconds to minutes.
 * Must display stage stepper, determinate progress %, processed/total bars, ETA, and Cancel CTA.
 */
export interface LoadingStageItem {
  id: string;
  label: string;
  status: "done" | "active" | "pending";
}

export function RunLoadingState({
  title,
  currentStageLabel,
  progressPct,
  processedBars,
  totalBars,
  elapsedMs,
  estimatedRemainingMs,
  stages,
  onCancel,
  canceling = false,
  className,
}: {
  title: string;
  currentStageLabel: string;
  progressPct: number;
  processedBars?: number;
  totalBars?: number;
  elapsedMs?: number;
  estimatedRemainingMs?: number | null;
  stages?: LoadingStageItem[];
  onCancel?: () => void;
  canceling?: boolean;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(100, progressPct));

  return (
    <div
      className={cn(
        "rounded-lg border border-blue-500/35 bg-slate-900/75 p-4",
        className
      )}
    >
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2.5">
          <Loader2 className="h-4 w-4 animate-spin text-blue-400 shrink-0" />
          <div>
            <div className="text-sm font-semibold text-slate-100">{title}</div>
            <div className="text-xs text-blue-300 mt-0.5">
              当前阶段：{currentStageLabel}
            </div>
          </div>
        </div>
        {onCancel && (
          <Button
            variant="outline"
            size="sm"
            disabled={canceling}
            onClick={onCancel}
            className="border-rose-500/40 text-rose-300 hover:bg-rose-950/40"
          >
            <XCircle className="h-3.5 w-3.5" />
            <span>{canceling ? "正在中止..." : "取消运行"}</span>
          </Button>
        )}
      </div>

      {/* Progress Bar */}
      <div className="mt-3.5">
        <div className="flex items-center justify-between text-[11px] font-mono text-slate-300 mb-1">
          <span>
            进度 <strong className="text-white">{clamped.toFixed(1)}%</strong>
            {processedBars !== undefined && totalBars !== undefined && (
              <span className="ml-2 text-slate-400">
                ({processedBars.toLocaleString()} / {totalBars.toLocaleString()} Bars)
              </span>
            )}
          </span>
          <span className="text-slate-400">
            已耗时 {formatDurationMs(elapsedMs ?? 0)} · 预计剩余{" "}
            <strong className="text-slate-200">
              {estimatedRemainingMs !== null && estimatedRemainingMs !== undefined
                ? formatDurationMs(estimatedRemainingMs)
                : "计算中..."}
            </strong>
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-blue-500 transition-all duration-300"
            style={{ width: `${clamped}%` }}
          />
        </div>
      </div>

      {/* Stage Stepper */}
      {stages && stages.length > 0 && (
        <div className="mt-3.5 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {stages.map((st) => (
            <div
              key={st.id}
              className={cn(
                "flex items-center gap-1.5 rounded border px-2.5 py-1.5 text-[11px]",
                st.status === "done"
                  ? "border-emerald-500/35 bg-emerald-950/20 text-emerald-300"
                  : st.status === "active"
                    ? "border-blue-500/50 bg-blue-950/30 text-blue-200 font-semibold"
                    : "border-slate-800 bg-slate-900/50 text-slate-500"
              )}
            >
              {st.status === "done" ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
              ) : st.status === "active" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400 shrink-0" />
              ) : (
                <span className="h-2 w-2 rounded-full bg-slate-600 shrink-0" />
              )}
              <span className="truncate">{st.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 3. Error State: Must surface explicit Reason Code + clear diagnostic + executable next-step CTA(s).
 */
export interface ErrorNextStepAction {
  label: string;
  onClick: () => void;
  variant?: "default" | "outline" | "secondary";
}

export function ActionableErrorState({
  title,
  reasonCode,
  message,
  remediation,
  rawDetail,
  actions,
  className,
}: {
  title: string;
  reasonCode: string;
  message: string;
  remediation: string;
  rawDetail?: string;
  actions?: ErrorNextStepAction[];
  className?: string;
}) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div
      className={cn(
        "rounded-lg border border-rose-500/50 bg-rose-950/20 p-4",
        className
      )}
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-2.5">
          <AlertOctagon className="h-4 w-4 text-rose-400 mt-0.5 shrink-0" />
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-rose-100">{title}</span>
              <span className="rounded border border-rose-400/50 bg-rose-500/20 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-rose-200">
                CODE: {reasonCode}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-200">{message}</p>
            <p className="mt-1 text-xs text-amber-200/90">
              <strong>可执行下一步：</strong>
              {remediation}
            </p>
          </div>
        </div>

        {actions && actions.length > 0 && (
          <div className="flex items-center gap-2 shrink-0">
            {actions.map((act, idx) => (
              <Button
                key={idx}
                size="sm"
                variant={act.variant ?? (idx === 0 ? "default" : "outline")}
                onClick={act.onClick}
              >
                {act.label}
              </Button>
            ))}
          </div>
        )}
      </div>

      {rawDetail && (
        <div className="mt-3 border-t border-rose-500/20 pt-2">
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
          >
            {showRaw ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            <span>原始错误载荷 / 技术诊断</span>
          </button>
          {showRaw && (
            <pre className="mt-1.5 max-h-36 overflow-auto rounded bg-slate-950 p-2.5 font-mono text-[11px] text-rose-200/90">
              {rawDetail}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
