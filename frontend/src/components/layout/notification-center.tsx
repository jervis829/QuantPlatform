"use client";

import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { quantApi } from "../../lib/apiClient";
import { formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { EnvBadge, SeverityBadge } from "../quant/badges";
import { Button } from "../ui/button";

export function NotificationCenter() {
  const { apiKey, alertDrawerOpen, setAlertDrawerOpen } = usePreferences();
  const queryClient = useQueryClient();

  const eventsQuery = useQuery({
    queryKey: ["risk-events", apiKey],
    queryFn: () => quantApi.listRiskEvents(apiKey),
    enabled: alertDrawerOpen,
  });

  const ackMutation = useMutation({
    mutationFn: (eventId: string) => quantApi.acknowledgeRiskEvent(apiKey, eventId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["risk-events"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  if (!alertDrawerOpen) return null;

  const items = eventsQuery.data?.items ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-[1px]">
      <div className="flex h-full w-full max-w-md flex-col border-l border-border bg-slate-950 shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <BellRing className="h-4 w-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-white">常驻告警与风控通知中心</h2>
          </div>
          <button
            type="button"
            onClick={() => setAlertDrawerOpen(false)}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-2.5 overflow-y-auto p-3.5">
          {items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-800 p-8 text-center text-xs text-muted-foreground">
              当前无风控拦截或事后异常告警记录。
              <div className="mt-2">
                <Link
                  href="/system"
                  onClick={() => setAlertDrawerOpen(false)}
                  className="text-blue-400 hover:underline"
                >
                  前往系统状态页查看完整基础设施健康指标 →
                </Link>
              </div>
            </div>
          ) : (
            items.map((ev) => (
              <div
                key={ev.event_id}
                className={`rounded-lg border p-3 text-xs ${
                  ev.acknowledged
                    ? "border-slate-800 bg-slate-900/40 opacity-70"
                    : "border-amber-500/45 bg-slate-900/90"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <SeverityBadge severity={ev.severity} />
                    <EnvBadge env={ev.env} />
                    <code className="font-mono text-[11px] font-bold text-slate-200">
                      {ev.rule_code}
                    </code>
                  </div>
                  <span className="font-mono text-[10px] text-slate-400">
                    {formatUtcTimestamp(ev.ts_ms, false)}
                  </span>
                </div>

                <p className="mt-1.5 text-slate-200">{ev.reason}</p>

                <div className="mt-2 flex items-center justify-between gap-2">
                  <Link
                    href={`/ledger?env=${ev.env}&runId=${ev.run_id}`}
                    onClick={() => setAlertDrawerOpen(false)}
                    className="inline-flex items-center gap-1 text-[11px] text-blue-400 hover:underline"
                  >
                    <span>下钻关联运行 ({ev.run_id.slice(0, 14)}...)</span>
                    <ExternalLink className="h-3 w-3" />
                  </Link>

                  {!ev.acknowledged ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={ackMutation.isPending}
                      onClick={() => ackMutation.mutate(ev.event_id)}
                    >
                      <Check className="h-3 w-3" />
                      <span>确认已读</span>
                    </Button>
                  ) : (
                    <span className="font-mono text-[10px] text-emerald-400">
                      已由 {ev.acked_by} 确认
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
