"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Pause, Play, Search, Trash2 } from "lucide-react";
import { quantApi } from "../../lib/apiClient";
import { formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

export interface LogEntry {
  id: string;
  ts_ms: number;
  category: "EXECUTION" | "RISK" | "AUDIT" | "WS";
  level: "INFO" | "WARN" | "ERROR";
  summary: string;
  raw?: string;
}

export interface SearchableLogPanelProps {
  strategyId: string;
  runs: Array<Record<string, unknown>>;
}

export function SearchableLogPanel({ strategyId, runs }: SearchableLogPanelProps) {
  const { apiKey } = usePreferences();
  const [searchText, setSearchText] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("ALL");
  const [autoScroll, setAutoScroll] = useState(true);
  const [wsEntries, setWsEntries] = useState<LogEntry[]>([]);
  const [clearedBeforeMs, setClearedBeforeMs] = useState<number>(0);

  const scrollBoxRef = useRef<HTMLDivElement | null>(null);

  // Pull real risk events & audit logs from backend to populate historical logs for this strategy
  const riskQuery = useQuery({
    queryKey: ["risk-events", apiKey],
    queryFn: () => quantApi.listRiskEvents(apiKey),
  });

  const auditQuery = useQuery({
    queryKey: ["audit-logs", apiKey],
    queryFn: () => quantApi.listAuditLogs(apiKey),
  });

  // Subscribe to real-time WebSocket stream
  useEffect(() => {
    const ws = quantApi.openEventSocket((ev) => {
      const payloadStr = JSON.stringify(ev);
      // Include global events or events mentioning this strategyId
      if (
        payloadStr.includes(strategyId) ||
        ev.type === "KILL_SWITCH" ||
        ev.type === "RUN_COMPLETED" ||
        ev.type === "RISK_EVENT"
      ) {
        const entry: LogEntry = {
          id: `ws_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          ts_ms: Number(ev.ts_ms || Date.now()),
          category: "WS",
          level: String(ev.type || "").includes("KILL") || String(ev.type || "").includes("RISK")
            ? "WARN"
            : "INFO",
          summary: `[实时流 ${String(ev.type || "EVENT")}] ${payloadStr}`,
          raw: payloadStr,
        };
        setWsEntries((prev) => [...prev.slice(-299), entry]);
      }
    });
    return () => {
      if (ws) ws.close();
    };
  }, [strategyId]);

  const mergedLogs = useMemo<LogEntry[]>(() => {
    const list: LogEntry[] = [];

    // 1. Run lifecycle entries
    runs.forEach((r) => {
      const rid = String(r.run_id || "");
      const env = String(r.env || "").toUpperCase();
      const status = String(r.status || "");
      const started = Number(r.started_at_ms || r.created_at_ms || 0);
      if (started > 0) {
        list.push({
          id: `run_${rid}`,
          ts_ms: started,
          category: "EXECUTION",
          level: status === "FAILED" ? "ERROR" : "INFO",
          summary: `[${env}] run_id=${rid} 状态=${status} 版本=${String(r.strategy_version_id || "-")}`,
          raw: JSON.stringify(r),
        });
      }
    });

    // 2. Risk events for this strategy
    (riskQuery.data?.items ?? [])
      .filter((ev) => ev.strategy_id === strategyId || ev.strategy_id === "GLOBAL")
      .forEach((ev) => {
        list.push({
          id: `risk_${ev.event_id}`,
          ts_ms: ev.ts_ms,
          category: "RISK",
          level: ev.severity === "CRITICAL" || ev.severity === "HIGH" ? "ERROR" : "WARN",
          summary: `[风控 ${ev.rule_code}] (${ev.env.toUpperCase()}) ${ev.reason}`,
          raw: JSON.stringify(ev),
        });
      });

    // 3. Audit trail entries touching this strategy
    (auditQuery.data?.items ?? []).forEach((a, idx) => {
      const targetId = String(a.target_id || "");
      const detailsStr = JSON.stringify(a.details ?? {});
      if (targetId.includes(strategyId) || detailsStr.includes(strategyId)) {
        list.push({
          id: `audit_${String(a.id ?? idx)}`,
          ts_ms: Number(a.ts_ms || 0),
          category: "AUDIT",
          level: "INFO",
          summary: `[审计 ${String(a.action)}] 操作人=${String(a.actor)} 目标=${targetId} Hash=${String(a.entry_hash || "").slice(0, 12)}`,
          raw: JSON.stringify(a),
        });
      }
    });

    // 4. Live WS stream entries
    list.push(...wsEntries);

    return list
      .filter((item) => item.ts_ms >= clearedBeforeMs)
      .sort((a, b) => a.ts_ms - b.ts_ms);
  }, [auditQuery.data?.items, clearedBeforeMs, riskQuery.data?.items, runs, strategyId, wsEntries]);

  const filteredLogs = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    return mergedLogs.filter((entry) => {
      if (categoryFilter !== "ALL" && entry.category !== categoryFilter) return false;
      if (!q) return true;
      return (
        entry.summary.toLowerCase().includes(q) ||
        (entry.raw ? entry.raw.toLowerCase().includes(q) : false)
      );
    });
  }, [categoryFilter, mergedLogs, searchText]);

  // Auto-scroll when new logs arrive unless paused
  useEffect(() => {
    if (!autoScroll || !scrollBoxRef.current) return;
    scrollBoxRef.current.scrollTop = scrollBoxRef.current.scrollHeight;
  }, [autoScroll, filteredLogs.length]);

  const handleManualScroll = () => {
    const el = scrollBoxRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom > 48 && autoScroll) {
      setAutoScroll(false);
    }
  };

  const handleExportLogs = () => {
    const txt = filteredLogs
      .map(
        (l) =>
          `${formatUtcTimestamp(l.ts_ms, true)} [${l.category}/${l.level}] ${l.summary}`
      )
      .join("\n");
    const blob = new Blob([txt], { type: "text/plain;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${strategyId}_logs.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Toolbar: Search, Category Pills, Pause/Resume Auto-scroll, Clear, Export */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3.5 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="搜索日志关键词 / run_id / 规则码..."
              className="h-7 w-60 rounded border border-input bg-slate-950 pl-8 pr-2.5 text-xs text-white placeholder:text-slate-500 focus:border-blue-500 focus:outline-none"
            />
          </div>

          <div className="inline-flex rounded border border-border bg-slate-950 p-0.5">
            {(
              [
                ["ALL", "全部"],
                ["EXECUTION", "运行"],
                ["RISK", "风控"],
                ["AUDIT", "审计"],
                ["WS", "实时流"],
              ] as const
            ).map(([cat, label]) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategoryFilter(cat)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11px] transition-colors",
                  categoryFilter === cat
                    ? "bg-blue-600 text-white font-semibold"
                    : "text-slate-400 hover:text-slate-200"
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant={autoScroll ? "secondary" : "shadow"}
            onClick={() => setAutoScroll((v) => !v)}
          >
            {autoScroll ? (
              <>
                <Pause className="h-3.5 w-3.5 text-emerald-400" />
                <span>自动滚动中（点击暂停）</span>
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" />
                <span>已暂停滚动（点击恢复）</span>
              </>
            )}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setClearedBeforeMs(Date.now())}
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span>清屏</span>
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={handleExportLogs}>
            <Download className="h-3.5 w-3.5" />
            <span>导出</span>
          </Button>
        </div>
      </div>

      {/* Scrollable Monospace Log Stream */}
      <div
        ref={scrollBoxRef}
        onScroll={handleManualScroll}
        className="h-80 overflow-y-auto bg-[#060912] p-3 font-mono text-[11px] leading-relaxed"
      >
        {filteredLogs.length === 0 ? (
          <div className="py-12 text-center text-slate-500">
            暂无匹配的策略运行、风控或实时事件日志。
          </div>
        ) : (
          filteredLogs.map((entry) => (
            <div
              key={entry.id}
              className={cn(
                "flex items-start gap-2.5 rounded px-2 py-1 hover:bg-slate-900/80",
                entry.level === "ERROR"
                  ? "text-rose-300 bg-rose-950/20"
                  : entry.level === "WARN"
                    ? "text-amber-200 bg-amber-950/15"
                    : "text-slate-300"
              )}
            >
              <span className="shrink-0 text-slate-500">
                {formatUtcTimestamp(entry.ts_ms, true)}
              </span>
              <span
                className={cn(
                  "w-16 shrink-0 rounded px-1 text-center text-[10px] font-bold",
                  entry.category === "RISK"
                    ? "bg-rose-500/20 text-rose-300"
                    : entry.category === "AUDIT"
                      ? "bg-violet-500/20 text-violet-300"
                      : entry.category === "WS"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-sky-500/20 text-sky-300"
                )}
              >
                {entry.category}
              </span>
              <span className="break-all">{entry.summary}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
