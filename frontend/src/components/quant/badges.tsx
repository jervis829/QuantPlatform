import React from "react";
import { cn } from "../../lib/utils";

export function EnvBadge({
  env,
  pulse = false,
  className,
}: {
  env: string;
  pulse?: boolean;
  className?: string;
}) {
  const normalized = env.toLowerCase();
  if (normalized === "live") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded border border-rose-500/50 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-rose-300",
          className
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-rose-400",
            pulse && "animate-pulse"
          )}
        />
        LIVE · 实盘
      </span>
    );
  }
  if (normalized === "shadow") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-amber-300",
          className
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-amber-400",
            pulse && "animate-pulse"
          )}
        />
        SHADOW · 影子
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border border-blue-500/45 bg-blue-500/15 px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-blue-300",
        className
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
      BACKTEST · 回测
    </span>
  );
}

export function ApprovalBadge({ status }: { status: string }) {
  const up = status.toUpperCase();
  if (up === "APPROVED") {
    return (
      <span className="inline-flex items-center rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase text-emerald-300">
        APPROVED · 已审批
      </span>
    );
  }
  if (up === "REJECTED") {
    return (
      <span className="inline-flex items-center rounded border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase text-rose-300">
        REJECTED · 已驳回
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase text-amber-300">
      PENDING · 待人工审批
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const up = severity.toUpperCase();
  const styles: Record<string, string> = {
    CRITICAL: "border-rose-500/60 bg-rose-500/20 text-rose-200 font-bold",
    HIGH: "border-orange-500/55 bg-orange-500/20 text-orange-200 font-semibold",
    MEDIUM: "border-amber-500/45 bg-amber-500/15 text-amber-200",
    LOW: "border-sky-500/40 bg-sky-500/15 text-sky-300",
    NONE: "border-slate-600/50 bg-slate-800/60 text-slate-400",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        styles[up] ?? styles.LOW
      )}
    >
      {up}
    </span>
  );
}

export function RunStatusBadge({ status }: { status: string }) {
  const up = status.toUpperCase();
  if (up === "RUNNING") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded border border-sky-500/45 bg-sky-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-sky-300">
        <span className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-ping" />
        RUNNING
      </span>
    );
  }
  if (up === "COMPLETED" || up === "FILLED") {
    return (
      <span className="inline-flex items-center rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-emerald-300">
        {up}
      </span>
    );
  }
  if (up === "FAILED" || up === "REJECTED") {
    return (
      <span className="inline-flex items-center rounded border border-rose-500/45 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-rose-300">
        {up}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-slate-600 bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-300">
      {up}
    </span>
  );
}
