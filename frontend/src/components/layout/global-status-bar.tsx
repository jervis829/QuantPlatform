"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  Clock,
  GitCompareArrows,
  Search,
  ShieldAlert,
  Wifi,
  WifiOff,
} from "lucide-react";
import Link from "next/link";
import { quantApi } from "../../lib/apiClient";
import { formatCompactNumber } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";
import { NumericDisplay } from "../quant/numeric-display";
import { Button } from "../ui/button";

export function GlobalStatusBar() {
  const {
    apiKey,
    wsConnected,
    setAlertDrawerOpen,
    setCommandPaletteOpen,
    setKillSwitchModalOpen,
  } = usePreferences();

  const overviewQuery = useQuery({
    queryKey: ["system-overview", apiKey],
    queryFn: () => quantApi.systemOverview(apiKey),
    refetchInterval: 8000,
  });

  const ov = overviewQuery.data;
  const backendAllHealthy =
    ov?.exchange_connections.every((c) => c.healthy) ?? true;
  const connHealthy = wsConnected && backendAllHealthy && !overviewQuery.isError;
  const skewMs = ov?.exchange_connections[0]?.clock_skew_ms ?? 0;
  const skewAbnormal = Math.abs(skewMs) >= 500;
  const skewCritical = Math.abs(skewMs) >= 1000;

  const reconCount = ov?.reconciliation_summary.unresolved_count ?? 0;
  const reconCrit = (ov?.reconciliation_summary.critical_or_high_count ?? 0) > 0;
  const unackedAlerts = ov?.unacknowledged_alert_count ?? 0;

  return (
    <header className="sticky top-0 z-40 flex h-12 w-full items-center justify-between gap-2 border-b border-border bg-slate-950/95 px-3 backdrop-blur">
      {/* Left: Running Strategies by Env + Today's PnL */}
      <div className="flex items-center gap-2.5 overflow-x-auto no-scrollbar">
        <Link
          href="/strategies"
          className="flex items-center gap-1.5 rounded border border-border bg-slate-900/90 px-2.5 py-1 text-[11px] hover:border-slate-600"
          title="查看各环境运行中的策略"
        >
          <Activity className="h-3.5 w-3.5 text-sky-400 shrink-0" />
          <span className="text-slate-400 hidden xl:inline">运行中:</span>
          <span className="font-mono font-semibold text-rose-300">
            LIVE {ov?.running_counts.live ?? 0}
          </span>
          <span className="text-slate-600">·</span>
          <span className="font-mono font-semibold text-amber-300">
            SH {ov?.running_counts.shadow ?? 0}
          </span>
          <span className="text-slate-600">·</span>
          <span className="font-mono font-semibold text-blue-300">
            BT {ov?.running_counts.backtest ?? 0}
          </span>
        </Link>

        <Link
          href="/ledger?env=live"
          className="flex items-center gap-1.5 rounded border border-border bg-slate-900/90 px-2.5 py-1 text-[11px] hover:border-slate-600"
          title="查看今日实盘/影子已实现盈亏明细"
        >
          <span className="text-slate-400">今日盈亏:</span>
          <NumericDisplay
            value={ov?.today_pnl_usdt ?? "0"}
            mode="currency"
            coloredBySign
            showPlusSign
            className="font-semibold"
          />
          <span className="font-mono text-[10px] text-slate-500 hidden 2xl:inline">
            (昨日 {formatCompactNumber(ov?.yesterday_pnl_usdt ?? "0", { unit: "USDT" })})
          </span>
        </Link>
      </div>

      {/* Center: Persistent Health & Consistency Indicators (Never transient toasts!) */}
      <div className="flex items-center gap-2">
        {/* Exchange WS Connection */}
        <Link
          href="/system#connections"
          className={cn(
            "flex items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] font-medium transition-colors",
            connHealthy
              ? "border-slate-800 bg-slate-900/80 text-emerald-300 hover:border-slate-700"
              : "border-rose-500/70 bg-rose-950/60 text-rose-200 animate-pulse"
          )}
          title="点击下钻至连接诊断详情"
        >
          {connHealthy ? (
            <Wifi className="h-3.5 w-3.5 text-emerald-400" />
          ) : (
            <WifiOff className="h-3.5 w-3.5 text-rose-400" />
          )}
          <span>{connHealthy ? "交易所连接正常" : "连接异常 · 点击排查"}</span>
        </Link>

        {/* Server Clock Skew */}
        <Link
          href="/system#clock"
          className={cn(
            "flex items-center gap-1 rounded border px-2 py-1 font-mono text-[11px] tabular-nums-strict transition-colors",
            skewCritical
              ? "border-rose-500/70 bg-rose-950/60 text-rose-200 font-bold"
              : skewAbnormal
                ? "border-amber-500/60 bg-amber-950/50 text-amber-200 font-semibold"
                : "border-slate-800 bg-slate-900/80 text-slate-300 hover:border-slate-700"
          )}
          title="服务器与交易所时钟偏移 (超过 1000ms 自动阻断实盘发单)"
        >
          <Clock className="h-3.5 w-3.5 text-slate-400" />
          <span>偏移 {skewMs >= 0 ? `+${skewMs}` : skewMs}ms</span>
        </Link>

        {/* Reconciliation Discrepancies */}
        <Link
          href="/reconciliation"
          className={cn(
            "flex items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] transition-colors",
            reconCount === 0
              ? "border-slate-800 bg-slate-900/80 text-slate-300 hover:border-slate-700"
              : reconCrit
                ? "border-rose-500/70 bg-rose-950/60 text-rose-200 font-semibold"
                : "border-amber-500/60 bg-amber-950/50 text-amber-200 font-semibold"
          )}
          title="点击下钻至三层对账差异看板"
        >
          <GitCompareArrows className="h-3.5 w-3.5" />
          <span>对账差异:</span>
          <span className="font-mono font-bold tabular-nums-strict">{reconCount}</span>
          {reconCount > 0 && ov && (
            <span className="font-mono text-[10px]">
              (${ov.reconciliation_summary.total_abs_drift_usdt})
            </span>
          )}
        </Link>

        {/* Unacknowledged Risk Alerts */}
        <button
          type="button"
          onClick={() => setAlertDrawerOpen(true)}
          className={cn(
            "flex items-center gap-1.5 rounded border px-2.5 py-1 text-[11px] transition-colors",
            unackedAlerts > 0
              ? "border-amber-500/70 bg-amber-950/55 text-amber-200 font-semibold"
              : "border-slate-800 bg-slate-900/80 text-slate-300 hover:border-slate-700"
          )}
          title="打开常驻告警与通知中心"
        >
          <Bell className="h-3.5 w-3.5" />
          <span>未处理告警</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.2 font-mono text-[10px] font-bold tabular-nums-strict",
              unackedAlerts > 0
                ? "bg-amber-500 text-slate-950"
                : "bg-slate-800 text-slate-400"
            )}
          >
            {unackedAlerts}
          </span>
        </button>
      </div>

      {/* Right: Command Palette (Cmd+K) + Physically Isolated KILL SWITCH */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={() => setCommandPaletteOpen(true)}
          className="flex h-8 items-center gap-2 rounded border border-border bg-slate-900 px-2.5 text-xs text-slate-400 hover:border-slate-600 hover:text-slate-200"
          title="全局搜索策略、运行记录、订单 (Cmd/Ctrl + K)"
        >
          <Search className="h-3.5 w-3.5" />
          <span className="hidden lg:inline">跳转/搜索...</span>
          <kbd className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
            ⌘K
          </kbd>
        </button>

        {/* Safety Physical Isolation Divider before Kill Switch */}
        <div className="ml-1 border-l-2 border-rose-800/75 pl-2.5">
          <Button
            type="button"
            variant="killSwitch"
            size="default"
            onClick={() => setKillSwitchModalOpen(true)}
          >
            <ShieldAlert className="h-4 w-4" />
            <span>KILL SWITCH</span>
            {ov?.kill_switch_active && (
              <span className="ml-1 rounded bg-white/20 px-1 text-[9px]">已激活</span>
            )}
          </Button>
        </div>
      </div>
    </header>
  );
}
