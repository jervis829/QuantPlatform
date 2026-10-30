"use client";

import React, { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  GitCompareArrows,
  Layers,
  Palette,
  Rocket,
  ScrollText,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";

const NAV_ITEMS = [
  {
    href: "/strategies",
    label: "我的策略",
    sub: "创建 · 回测 · 实盘管理",
    icon: Layers,
  },
  {
    href: "/runs/new",
    label: "启动运行",
    sub: "回测 / 模拟盘 / 实盘",
    icon: Rocket,
  },
  {
    href: "/ledger",
    label: "订单与成交",
    sub: "委托记录 · 逐笔成交",
    icon: ScrollText,
  },
  {
    href: "/reconciliation",
    label: "账户对账",
    sub: "核对持仓与资金一致性",
    icon: GitCompareArrows,
  },
  {
    href: "/system",
    label: "风控与系统",
    sub: "连接状态 · 风险告警",
    icon: ServerCog,
  },
];

const ROLE_CN: Record<string, string> = {
  ADMIN: "管理员",
  OPERATOR: "交易员",
  RESEARCHER: "策略研究",
  VIEWER: "只读访客",
};

export function SidebarNav() {
  const pathname = usePathname();
  const { apiKey, setApiKey, roleLabel, pnlPolarity, setPnlPolarity } = usePreferences();
  const [showAdvancedPrefs, setShowAdvancedPrefs] = useState(false);

  return (
    <aside className="flex w-52 shrink-0 flex-col justify-between border-r border-border bg-slate-950/90 select-none">
      <div>
        {/* Brand Header */}
        <div className="border-b border-border px-3.5 py-3">
          <Link href="/strategies" className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600 font-mono text-xs font-bold text-white">
              QP
            </div>
            <div>
              <div className="text-xs font-bold tracking-wide text-slate-100">
                量化交易平台
              </div>
              <div className="text-[10px] text-slate-400">
                回测 · 模拟盘 · 实盘一体
              </div>
            </div>
          </Link>
        </div>

        {/* Navigation Links */}
        <nav className="space-y-1 p-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active =
              pathname === item.href ||
              (item.href === "/strategies" && pathname === "/") ||
              (item.href !== "/" && pathname.startsWith(`${item.href}/`));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 transition-colors",
                  active
                    ? "bg-blue-600/18 text-white border border-blue-500/40"
                    : "text-slate-300 hover:bg-slate-900 hover:text-white border border-transparent"
                )}
              >
                <Icon
                  className={cn(
                    "h-4 w-4 shrink-0",
                    active ? "text-blue-400" : "text-slate-400"
                  )}
                />
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium">{item.label}</div>
                  <div className="truncate text-[10px] text-slate-500">{item.sub}</div>
                </div>
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Bottom Preferences: Clean Polarity Toggle + Collapsible Advanced Settings */}
      <div className="space-y-2.5 border-t border-border p-3 text-xs">
        <div>
          <div className="mb-1 text-[11px] text-slate-400">涨跌颜色习惯</div>
          <div className="grid grid-cols-2 gap-1 rounded border border-border bg-slate-900 p-0.5">
            <button
              type="button"
              onClick={() => setPnlPolarity("green-up")}
              className={cn(
                "rounded py-1 text-[11px] font-medium transition-colors",
                pnlPolarity === "green-up"
                  ? "bg-emerald-600/30 text-emerald-200 border border-emerald-500/40"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              绿涨红跌
            </button>
            <button
              type="button"
              onClick={() => setPnlPolarity("red-up")}
              className={cn(
                "rounded py-1 text-[11px] font-medium transition-colors",
                pnlPolarity === "red-up"
                  ? "bg-rose-600/30 text-rose-200 border border-rose-500/40"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              红涨绿跌
            </button>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowAdvancedPrefs((v) => !v)}
          className="flex w-full items-center justify-between rounded px-1 py-1 text-[11px] text-slate-400 hover:text-slate-200"
        >
          <span className="inline-flex items-center gap-1">
            <ShieldCheck className="h-3.5 w-3.5 text-sky-400" />
            <span>权限: {ROLE_CN[roleLabel] ?? roleLabel}</span>
          </span>
          {showAdvancedPrefs ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>

        {showAdvancedPrefs && (
          <div className="space-y-2 rounded border border-slate-800 bg-slate-900/70 p-2">
            <div className="space-y-1">
              <div className="text-[10px] text-slate-400">切换账号角色</div>
              <select
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full rounded border border-border bg-slate-950 px-2 py-1 text-[11px] text-slate-200 focus:border-blue-500 focus:outline-none"
              >
                <option value="admin-dev-token">管理员 (全部权限)</option>
                <option value="operator-dev-token">交易员 (审核与实盘)</option>
                <option value="researcher-dev-token">研究员 (策略与回测)</option>
                <option value="viewer-dev-token">访客 (仅查看)</option>
              </select>
            </div>
            <Link
              href="/design-system"
              className="flex items-center gap-1.5 rounded px-1.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            >
              <Palette className="h-3.5 w-3.5" />
              <span>界面设计规范页</span>
            </Link>
          </div>
        )}
      </div>
    </aside>
  );
}
