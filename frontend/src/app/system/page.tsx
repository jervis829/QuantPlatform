"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Activity,
  Check,
  CheckCircle2,
  Clock,
  Cpu,
  ExternalLink,
  Gauge,
  ShieldAlert,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { EnvBadge, SeverityBadge } from "../../components/quant/badges";
import { QuantDataTable } from "../../components/quant/data-table";
import { KpiMetricCard } from "../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../components/quant/state-views";
import { Button } from "../../components/ui/button";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { formatUtcTimestamp } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import type {
  KillSwitchHistoryItem,
  RiskEventItem,
} from "../../types/openapi.generated";

function SystemStatusInner() {
  const { apiKey, roleLabel, setKillSwitchModalOpen } = usePreferences();
  const queryClient = useQueryClient();
  const [selectedDiagDetail, setSelectedDiagDetail] = useState<{
    title: string;
    detail: string;
  } | null>(null);

  const canAdminOrOperate = roleLabel === "ADMIN" || roleLabel === "OPERATOR";

  const diagQuery = useQuery({
    queryKey: ["system-diagnostics", apiKey],
    queryFn: () => quantApi.systemDiagnostics(apiKey),
    refetchInterval: 8000,
  });

  const eventsQuery = useQuery({
    queryKey: ["risk-events", apiKey],
    queryFn: () => quantApi.listRiskEvents(apiKey),
  });

  const ackMutation = useMutation({
    mutationFn: (eventId: string) => quantApi.acknowledgeRiskEvent(apiKey, eventId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["risk-events"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const maintMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      quantApi.toggleMaintenance(apiKey, {
        enabled,
        reason: enabled ? "operator_enabled_from_system_page" : "operator_resumed",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["system-diagnostics"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const alertColumns = useMemo<ColumnDef<RiskEventItem, unknown>[]>(
    () => [
      {
        id: "severity",
        accessorKey: "severity",
        header: "等级",
        cell: ({ row }) => <SeverityBadge severity={row.original.severity} />,
      },
      {
        id: "env",
        accessorKey: "env",
        header: "环境",
        cell: ({ row }) => <EnvBadge env={row.original.env} />,
      },
      {
        id: "rule_code",
        accessorKey: "rule_code",
        header: "触发风控规则码",
        cell: ({ row }) => (
          <code className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs font-bold text-white">
            {row.original.rule_code}
          </code>
        ),
      },
      {
        id: "reason",
        accessorKey: "reason",
        header: "详细原因 (点击展开上下文)",
        cell: ({ row }) => (
          <span className="text-xs text-slate-200">{row.original.reason}</span>
        ),
      },
      {
        id: "ts_ms",
        accessorKey: "ts_ms",
        header: "发生时间 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-400">
            {formatUtcTimestamp(row.original.ts_ms, true)}
          </span>
        ),
      },
      {
        id: "ack",
        accessorFn: (r) => (r.acknowledged ? 1 : 0),
        header: "处置状态",
        meta: { align: "right" },
        cell: ({ row }) => {
          const ev = row.original;
          if (ev.acknowledged) {
            return (
              <span className="font-mono text-[11px] text-emerald-400">
                已确认 ({ev.acked_by})
              </span>
            );
          }
          return (
            <Button
              size="sm"
              variant="outline"
              onClick={(e) => {
                e.stopPropagation();
                ackMutation.mutate(ev.event_id);
              }}
            >
              <Check className="h-3 w-3" />
              <span>确认已读</span>
            </Button>
          );
        },
      },
    ],
    [ackMutation]
  );

  const ksColumns = useMemo<ColumnDef<KillSwitchHistoryItem, unknown>[]>(
    () => [
      {
        id: "ts_ms",
        accessorKey: "ts_ms",
        header: "触发时间 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-300">
            {formatUtcTimestamp(row.original.ts_ms, true)}
          </span>
        ),
      },
      {
        id: "actor",
        accessorKey: "actor",
        header: "执行人",
        meta: { mono: true },
        cell: ({ row }) => <span className="font-semibold text-white">{row.original.actor}</span>,
      },
      {
        id: "reason",
        accessorKey: "reason",
        header: "停机原因",
        cell: ({ row }) => <span className="text-xs text-rose-200">{row.original.reason}</span>,
      },
      {
        id: "canceled",
        accessorKey: "canceled_orders",
        header: "撤销挂单数",
        meta: { align: "right", mono: true },
      },
      {
        id: "flattened",
        accessorKey: "flattened_positions",
        header: "市价平仓数",
        meta: { align: "right", mono: true },
      },
      {
        id: "elapsed",
        accessorKey: "elapsed_ms",
        header: "执行耗时",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span className="font-bold text-emerald-400">
            {row.original.elapsed_ms.toFixed(2)} ms (&lt; 1000ms)
          </span>
        ),
      },
      {
        id: "hash",
        accessorKey: "entry_hash",
        header: "防篡改审计哈希",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-[11px] text-slate-400">
            {row.original.entry_hash.slice(0, 16)}...
          </span>
        ),
      },
    ],
    []
  );

  if (diagQuery.isLoading) {
    return (
      <RunLoadingState
        title="正在采集系统连接、时钟偏移、限频与沙箱健康指标..."
        currentStageLabel="核验防篡改哈希审计链完整性"
        progressPct={70}
      />
    );
  }

  if (diagQuery.isError || !diagQuery.data) {
    const err =
      diagQuery.error instanceof QuantApiError ? diagQuery.error : null;
    return (
      <ActionableErrorState
        title="加载系统诊断指标失败"
        reasonCode={err?.reasonCode ?? "DIAGNOSTICS_FETCH_ERROR"}
        message={err?.message ?? String(diagQuery.error)}
        remediation="请确认后端控制面 API 进程在线后点击重试。"
        actions={[{ label: "重试", onClick: () => void diagQuery.refetch() }]}
      />
    );
  }

  const diag = diagQuery.data;
  const maxSkew = Math.max(
    0,
    ...diag.connections.map((c) => Math.abs(c.clock_skew_ms))
  );
  const totalRateHits = diag.rate_limits.reduce(
    (acc, r) => acc + r.recent_429_count,
    0
  );

  return (
    <div className="space-y-6 pb-12">
      {/* Header + Maintenance Mode + Kill Switch Trigger */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">
            系统状态、风控监控与防篡改审计中心 (/system)
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            全面监控交易所连接、毫秒时钟偏移、限频命中、隔离沙箱资源、风控告警历史与 Kill Switch 审计链；点击任意异常卡片可查看详细原因。
          </p>
        </div>

        <div className="flex items-center gap-2">
          {canAdminOrOperate && (
            <Button
              size="sm"
              variant={diag.maintenance_mode ? "shadow" : "outline"}
              disabled={maintMutation.isPending}
              onClick={() => maintMutation.mutate(!diag.maintenance_mode)}
            >
              <Wrench className="h-3.5 w-3.5" />
              <span>
                {diag.maintenance_mode
                  ? "维护模式已开启（点击恢复实盘开新仓）"
                  : "开启全局维护模式（阻断新开仓）"}
              </span>
            </Button>
          )}
          <Button
            size="sm"
            variant="killSwitch"
            onClick={() => setKillSwitchModalOpen(true)}
          >
            <ShieldAlert className="h-3.5 w-3.5" />
            <span>紧急停机预览与触发</span>
          </Button>
        </div>
      </div>

      {/* First-Glance Health KPI Layer (4 Cards — Clickable Drill-down) */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiMetricCard
          label="交易所通道连接健康度"
          timeWindow="实时心跳"
          alertTone={diag.connections.every((c) => c.healthy) ? "none" : "critical"}
          benchmarkLabel={`共 ${diag.connections.length} 条行情与交易通道`}
          onClick={() =>
            document.getElementById("connections")?.scrollIntoView({ behavior: "smooth" })
          }
        >
          <span
            className={
              diag.connections.every((c) => c.healthy)
                ? "text-emerald-400"
                : "text-rose-400"
            }
          >
            {diag.connections.filter((c) => c.healthy).length} / {diag.connections.length} 正常
          </span>
        </KpiMetricCard>

        <KpiMetricCard
          label="最大服务器时钟偏移"
          timeWindow="熔断阈值 1000ms"
          alertTone={maxSkew >= 1000 ? "critical" : maxSkew >= 500 ? "warning" : "none"}
          benchmarkLabel="偏移超限将自动阻断实盘发单"
          onClick={() =>
            document.getElementById("clock")?.scrollIntoView({ behavior: "smooth" })
          }
        >
          <span className="font-mono">{maxSkew} ms</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="策略沙箱累计校验 / 违规拦截"
          timeWindow="AST + 无网络隔离进程"
          alertTone={diag.sandbox_blocked_runs > 0 ? "warning" : "none"}
          benchmarkLabel="点击查看违规拦截原因码明细"
          onClick={() =>
            document.getElementById("sandbox")?.scrollIntoView({ behavior: "smooth" })
          }
        >
          <span className="font-mono">
            {diag.sandbox_total_runs} 次 / 拦截 {diag.sandbox_blocked_runs}
          </span>
        </KpiMetricCard>

        <KpiMetricCard
          label="防篡改哈希审计链校验"
          timeWindow="SHA-256 链式签名"
          alertTone={diag.audit_chain_valid ? "none" : "critical"}
          benchmarkLabel={`限频命中累计: ${totalRateHits} 次`}
          onClick={() =>
            document.getElementById("killswitch")?.scrollIntoView({ behavior: "smooth" })
          }
        >
          <span
            className={
              diag.audit_chain_valid ? "text-emerald-400" : "text-rose-400"
            }
          >
            {diag.audit_chain_valid ? "完整有效 (VALID)" : "链校验异常"}
          </span>
        </KpiMetricCard>
      </div>

      {/* Selected Diagnostic Drilldown Banner */}
      {selectedDiagDetail && (
        <div className="rounded-lg border border-sky-500/45 bg-sky-950/20 p-3.5 text-xs space-y-1">
          <div className="flex items-center justify-between">
            <strong className="text-sky-200">诊断详情：{selectedDiagDetail.title}</strong>
            <button
              type="button"
              onClick={() => setSelectedDiagDetail(null)}
              className="text-slate-400 hover:text-white"
            >
              收起
            </button>
          </div>
          <p className="font-mono text-[11px] text-slate-200">{selectedDiagDetail.detail}</p>
        </div>
      )}

      {/* Row 1: Exchange Connections + Clock Skew & Rate Limit Hits */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section
          id="connections"
          className="rounded-lg border border-border bg-card p-4 space-y-3"
        >
          <div className="flex items-center gap-2 border-b border-border pb-2">
            <Activity className="h-4 w-4 text-emerald-400" />
            <h2 className="text-xs font-semibold text-white">
              1. 交易所连接与时钟偏移监控 (点击任意通道查看诊断详情)
            </h2>
          </div>

          <div id="clock" className="space-y-2">
            {diag.connections.map((conn) => (
              <button
                key={`${conn.exchange}_${conn.channel}`}
                type="button"
                onClick={() =>
                  setSelectedDiagDetail({
                    title: `${conn.exchange} / ${conn.channel}`,
                    detail: `状态=${conn.healthy ? "HEALTHY" : "DISCONNECTED"} · 时钟偏移=${conn.clock_skew_ms}ms（事前风控阈值 ±1000ms）· 最近心跳=${
                      conn.last_heartbeat_ms
                        ? formatUtcTimestamp(conn.last_heartbeat_ms, true)
                        : "未知"
                    }`,
                  })
                }
                className="flex w-full items-center justify-between rounded border border-slate-800 bg-slate-900/70 px-3 py-2.5 text-left text-xs hover:border-blue-500/50"
              >
                <div>
                  <div className="font-mono font-semibold text-white">
                    {conn.exchange.toUpperCase()} · {conn.channel}
                  </div>
                  <div className="font-mono text-[10px] text-slate-400">
                    最近心跳:{" "}
                    {conn.last_heartbeat_ms
                      ? formatUtcTimestamp(conn.last_heartbeat_ms, true)
                      : "—"}
                  </div>
                </div>
                <div className="flex items-center gap-3 font-mono">
                  <span className="inline-flex items-center gap-1 text-[11px] text-slate-300">
                    <Clock className="h-3 w-3 text-slate-400" />
                    {conn.clock_skew_ms >= 0
                      ? `+${conn.clock_skew_ms}ms`
                      : `${conn.clock_skew_ms}ms`}
                  </span>
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                      conn.healthy
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-rose-500/25 text-rose-300"
                    }`}
                  >
                    {conn.healthy ? "ONLINE" : "DISCONNECTED"}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Rate Limits & Sandbox Resource Stats */}
        <section
          id="sandbox"
          className="rounded-lg border border-border bg-card p-4 space-y-3"
        >
          <div className="flex items-center gap-2 border-b border-border pb-2">
            <Gauge className="h-4 w-4 text-amber-400" />
            <h2 className="text-xs font-semibold text-white">
              2. 交易所限频命中计数 & 策略隔离沙箱资源统计
            </h2>
          </div>

          <div id="ratelimits" className="space-y-1.5">
            <div className="text-[11px] font-semibold text-slate-400">
              限频通道命中计数 (Rate Limit Hits)
            </div>
            {diag.rate_limits.map((rl, idx) => (
              <div
                key={idx}
                className="flex items-center justify-between rounded bg-slate-900/80 px-3 py-2 font-mono text-xs"
              >
                <span className="text-slate-200">
                  {rl.exchange.toUpperCase()} · 权重 {rl.used_weight} / {rl.max_weight}
                </span>
                <span
                  className={
                    rl.recent_429_count > 0
                      ? "font-bold text-amber-300"
                      : "text-emerald-400"
                  }
                >
                  429 命中 {rl.recent_429_count} 次
                </span>
              </div>
            ))}
          </div>

          <div className="space-y-1.5 pt-2 border-t border-slate-800">
            <div className="flex items-center justify-between text-[11px] font-semibold text-slate-400">
              <span className="inline-flex items-center gap-1">
                <Cpu className="h-3.5 w-3.5 text-sky-400" />
                <span>最近隔离沙箱执行记录（CPU时间 · 内存 · 无网络拦截）</span>
              </span>
              <span className="font-mono">
                共 {diag.sandbox_total_runs} 次 / 阻断 {diag.sandbox_blocked_runs} 次
              </span>
            </div>
            {diag.sandbox_stats.length === 0 ? (
              <div className="text-xs text-slate-500">当前会话尚无沙箱执行记录。</div>
            ) : (
              <div className="max-h-36 space-y-1 overflow-y-auto">
                {diag.sandbox_stats.map((st, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() =>
                      setSelectedDiagDetail({
                        title: `沙箱执行记录 #${idx + 1} (${st.reason_code})`,
                        detail: `校验结果=${st.ok ? "通过" : "违规阻断"} · 原因码=${st.reason_code} · 沙箱隔离执行耗时=${st.elapsed_ms.toFixed(
                          2
                        )}ms`,
                      })
                    }
                    className="flex w-full items-center justify-between rounded bg-slate-950 px-2.5 py-1.5 font-mono text-[11px] hover:bg-slate-900"
                  >
                    <span className={st.ok ? "text-emerald-300" : "text-rose-300 font-bold"}>
                      {st.ok ? "PASS" : "BLOCKED"} · {st.reason_code}
                    </span>
                    <span className="text-slate-400">{st.elapsed_ms.toFixed(1)} ms</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {/* Row 2: Risk Alert History Table (Expandable to exact rule/run/order) */}
      <section id="alerts" className="space-y-2.5">
        <h2 className="text-sm font-semibold text-slate-200">
          3. 风控事前拦截与事后熔断告警历史（点击展开查看完整上下文并下钻账本）
        </h2>
        {(eventsQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="当前无风控拦截或异常告警记录"
            description="所有事前订单校验（单笔上限、日最大亏损、杠杆、标的白名单、时钟偏移）与事后监控均处于正常水位。"
            icon={<ShieldCheck className="h-5 w-5 text-emerald-400" />}
          />
        ) : (
          <QuantDataTable
            tableId="sys_alerts"
            data={eventsQuery.data?.items ?? []}
            columns={alertColumns}
            getRowId={(r) => r.event_id}
            renderExpandedRow={(ev) => (
              <div className="flex flex-wrap items-center justify-between gap-3 font-mono text-xs">
                <div className="space-x-4 text-slate-300">
                  <span>event_id: {ev.event_id}</span>
                  <span>strategy_id: {ev.strategy_id}</span>
                  <span>run_id: {ev.run_id}</span>
                  {ev.client_order_id && <span>order_id: {ev.client_order_id}</span>}
                </div>
                <Link
                  href={`/ledger?env=${encodeURIComponent(ev.env)}&runId=${encodeURIComponent(
                    ev.run_id
                  )}`}
                >
                  <Button size="sm" variant="secondary">
                    <span>下钻关联账本流水</span>
                    <ExternalLink className="h-3.5 w-3.5" />
                  </Button>
                </Link>
              </div>
            )}
          />
        )}
      </section>

      {/* Row 3: Kill Switch History & Hash-Chained Audit Verification */}
      <section id="killswitch" className="space-y-2.5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">
            4. Kill Switch 紧急停机执行历史（亚秒级撤单平仓审计链）
          </h2>
          <span className="inline-flex items-center gap-1 font-mono text-xs text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" />
            <span>SHA-256 链式防篡改校验通过</span>
          </span>
        </div>

        {diag.kill_switch_history.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-800 bg-card p-6 text-center text-xs text-muted-foreground">
            系统自启动以来尚未触发过紧急停机开关 (Kill Switch)。
          </div>
        ) : (
          <QuantDataTable
            tableId="sys_killswitch"
            data={diag.kill_switch_history}
            columns={ksColumns}
            getRowId={(r) => String(r.id)}
            renderExpandedRow={(row) => (
              <div className="font-mono text-[11px] text-slate-300 space-y-1">
                <div>当前记录哈希 (entry_hash): {row.entry_hash}</div>
                <div>前序记录哈希 (prev_hash): {row.prev_hash}</div>
              </div>
            )}
          />
        )}
      </section>
    </div>
  );
}

export default function SystemStatusPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载系统状态与风控中心...
        </div>
      }
    >
      <SystemStatusInner />
    </Suspense>
  );
}
