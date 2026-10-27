"use client";

import React from "react";
import {
  classifySign,
  formatCompactNumber,
  formatCurrency,
  formatFixedDecimal,
  formatPercent,
  formatPriceBySpec,
  formatQtyBySpec,
} from "../../lib/formatters";
import { cn } from "../../lib/utils";

export interface NumericDisplayProps {
  value: string | number | null | undefined;
  mode?: "currency" | "percent" | "ratioPercent" | "price" | "qty" | "compact" | "decimal";
  currency?: string;
  decimals?: number;
  tickSize?: string | number;
  stepSize?: string | number;
  coloredBySign?: boolean;
  showPlusSign?: boolean;
  align?: "left" | "right";
  className?: string;
}

export function NumericDisplay({
  value,
  mode = "decimal",
  currency = "USDT",
  decimals = 2,
  tickSize,
  stepSize,
  coloredBySign = false,
  showPlusSign = false,
  align = "right",
  className,
}: NumericDisplayProps) {
  const sign = classifySign(value);

  let text = "—";
  if (mode === "currency") {
    text = formatCurrency(value, currency, { decimals, showPlusSign });
  } else if (mode === "percent") {
    text = formatPercent(value, { inputIsRatio: false, decimals, showPlusSign });
  } else if (mode === "ratioPercent") {
    text = formatPercent(value, { inputIsRatio: true, decimals, showPlusSign });
  } else if (mode === "price") {
    text = formatPriceBySpec(value, tickSize ?? "0.01", currency || undefined);
  } else if (mode === "qty") {
    text = formatQtyBySpec(value, stepSize ?? "0.001", currency || undefined);
  } else if (mode === "compact") {
    text = formatCompactNumber(value, { unit: currency, showPlusSign });
  } else {
    text = formatFixedDecimal(value, { decimals, showPlusSign, unit: currency || undefined });
  }

  const colorClass = !coloredBySign
    ? sign === "zero"
      ? "text-slate-400"
      : "text-slate-100"
    : sign === "positive"
      ? "text-pnl-up"
      : sign === "negative"
        ? "text-pnl-down"
        : "text-pnl-zero";

  return (
    <span
      className={cn(
        "font-mono tabular-nums-strict inline-block",
        align === "right" ? "text-right" : "text-left",
        colorClass,
        className
      )}
    >
      {text}
    </span>
  );
}

/**
 * Principle 3: First-glance KPI Card with required context (benchmark / time window / delta).
 */
export function KpiMetricCard({
  label,
  timeWindow,
  children,
  benchmarkLabel,
  deltaNode,
  onClick,
  alertTone,
}: {
  label: string;
  timeWindow?: string;
  children: React.ReactNode;
  benchmarkLabel?: string;
  deltaNode?: React.ReactNode;
  onClick?: () => void;
  alertTone?: "none" | "warning" | "critical";
}) {
  const borderClass =
    alertTone === "critical"
      ? "border-rose-500/60 bg-rose-950/20"
      : alertTone === "warning"
        ? "border-amber-500/50 bg-amber-950/15"
        : "border-border bg-card";

  return (
    <div
      onClick={onClick}
      className={cn(
        "rounded-lg border p-3.5 transition-colors",
        borderClass,
        onClick && "cursor-pointer hover:border-slate-500"
      )}
    >
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-slate-300">{label}</span>
        {timeWindow && (
          <span className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
            {timeWindow}
          </span>
        )}
      </div>
      <div className="mt-2 text-xl font-semibold tracking-tight">{children}</div>
      {(benchmarkLabel || deltaNode) && (
        <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>{benchmarkLabel}</span>
          {deltaNode}
        </div>
      )}
    </div>
  );
}
