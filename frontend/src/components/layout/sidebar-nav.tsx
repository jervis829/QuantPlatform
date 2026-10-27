"use client";

import React from "react";
import {
  BookOpenCheck,
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
    label: "策略工作台",
    sub: "巡检 · 审批 · 详情",
    icon: Layers,
  },
  {
    href: "/runs/new",
    label: "分级启动中心",
    sub: "回测 · 影子 · 实盘向导",
    icon: Rocket,
  },
  {
    href: "/ledger",
    label: "统一交易记录",
    sub: "组合筛选 · 订单展开",
    icon: ScrollText,
  },
  {
    href: "/reconciliation",
    label: "三层对账看板",
    sub: "严重度排序 · 差异下钻",
    icon: GitCompareArrows,
  },
  {
    href: "/system",
    label: "系统状态与风控",
    sub: "连接 · 限频 · 审计链",
    icon: ServerCog,
  },
  {
    href: "/design-system",
    label: "设计基础与组件库",
    sub: "Token · 三态 · 规范页",
    icon: Palette,
  },
];

export function SidebarNav() {
  const pathname = usePathname();
  const { apiKey, setApiKey, roleLabel, pnlPolarity, setPnlPolarity } = usePreferences();

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
                QuantPlatform
              </div>
              <div className="font-mono text-[10px] text-slate-400">
                BT · SHADOW · LIVE
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

      {/* Bottom Preferences: Polarity & RBAC Role */}
      <div className="space-y-3 border-t border-border p-3 text-xs">
        <div>
          <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
            <span>盈亏涨跌配色</span>
            <BookOpenCheck className="h-3 w-3" />
          </div>
          <div className="grid grid-cols-2 gap-1 rounded border border-border bg-slate-900 p-0.5">
            <button
              type="button"
              onClick={() => setPnlPolarity("green-up")}
              className={cn(
                "rounded py-1 text-[10px] font-medium transition-colors",
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
                "rounded py-1 text-[10px] font-medium transition-colors",
                pnlPolarity === "red-up"
                  ? "bg-rose-600/30 text-rose-200 border border-rose-500/40"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              红涨绿跌
            </button>
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
            <span>当前角色 ({roleLabel})</span>
            <ShieldCheck className="h-3.5 w-3.5 text-sky-400" />
          </div>
          <select
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full rounded border border-border bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-slate-200 focus:border-blue-500 focus:outline-none"
          >
            <option value="admin-dev-token">ADMIN (全权限)</option>
            <option value="operator-dev-token">OPERATOR (审批/实盘)</option>
            <option value="researcher-dev-token">RESEARCHER (投研/回测)</option>
            <option value="viewer-dev-token">VIEWER (只读巡检)</option>
          </select>
        </div>
      </div>
    </aside>
  );
}
