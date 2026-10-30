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
          "inline-flex items-center gap-1.5 rounded border border-rose-500/50 bg-rose-500/15 px-2 py-0.5 text-[11px] font-bold text-rose-300",
          className
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-rose-400",
            pulse && "animate-pulse"
          )}
        />
        实盘交易
      </span>
    );
  }
  if (normalized === "shadow") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 text-[11px] font-bold text-amber-300",
          className
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-amber-400",
            pulse && "animate-pulse"
          )}
        />
        模拟盘
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border border-blue-500/45 bg-blue-500/15 px-2 py-0.5 text-[11px] font-bold text-blue-300",
        className
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
      历史回测
    </span>
  );
}

export function ApprovalBadge({ status }: { status: string }) {
  const up = status.toUpperCase();
  if (up === "APPROVED") {
    return (
      <span
        className="inline-flex items-center rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300"
        title="已通过人工确认，允许用于模拟盘和实盘"
      >
        已审核
      </span>
    );
  }
  if (up === "REJECTED") {
    return (
      <span className="inline-flex items-center rounded border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300">
        已退回
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-300"
      title="可直接跑历史回测；上模拟盘或实盘前需点击审核通过"
    >
      待审核
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
  const labels: Record<string, string> = {
    CRITICAL: "严重",
    HIGH: "高风险",
    MEDIUM: "中等",
    LOW: "轻微",
    NONE: "正常",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px]",
        styles[up] ?? styles.LOW
      )}
    >
      {labels[up] ?? up}
    </span>
  );
}

export function RunStatusBadge({ status }: { status: string }) {
  const up = status.toUpperCase();
  const statusMap: Record<string, string> = {
    RUNNING: "运行中",
    COMPLETED: "已完成",
    FILLED: "已成交",
    PARTIALLY_FILLED: "部分成交",
    NEW: "挂单中",
    CANCELED: "已取消",
    STOPPED: "已停止",
    KILLED: "紧急停机",
    FAILED: "失败",
    REJECTED: "已拒单",
  };
  const label = statusMap[up] ?? up;
  if (up === "RUNNING") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded border border-sky-500/45 bg-sky-500/15 px-2 py-0.5 text-[11px] font-semibold text-sky-300">
        <span className="h-1.5 w-1.5 rounded-full bg-sky-400 animate-ping" />
        {label}
      </span>
    );
  }
  if (up === "COMPLETED" || up === "FILLED") {
    return (
      <span className="inline-flex items-center rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
        {label}
      </span>
    );
  }
  if (up === "FAILED" || up === "REJECTED") {
    return (
      <span className="inline-flex items-center rounded border border-rose-500/45 bg-rose-500/15 px-2 py-0.5 text-[11px] font-semibold text-rose-300">
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded border border-slate-600 bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
      {label}
    </span>
  );
}
