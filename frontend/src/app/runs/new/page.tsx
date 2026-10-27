"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  Play,
  Rocket,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ApprovalBadge, EnvBadge } from "../../../components/quant/badges";
import { NumericDisplay } from "../../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../../components/quant/state-views";
import {
  BacktestActionButton,
  Button,
  IsolatedDangerZone,
  LiveDangerActionButton,
  ShadowActionButton,
} from "../../../components/ui/button";
import { QuantApiError, quantApi } from "../../../lib/apiClient";
import { formatCurrency } from "../../../lib/formatters";
import { usePreferences } from "../../../lib/preferences-context";
import { cn } from "../../../lib/utils";
import type { LaunchRunResponse } from "../../../types/openapi.generated";

type TargetEnvMode = "backtest" | "shadow" | "live";

function LaunchRunCenterInner() {
  const searchParams = useSearchParams();
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const initialEnv = (searchParams.get("env") as TargetEnvMode) || "backtest";
  const [envMode, setEnvMode] = useState<TargetEnvMode>(
    ["backtest", "shadow", "live"].includes(initialEnv) ? initialEnv : "backtest"
  );

  const [selectedStrategyId, setSelectedStrategyId] = useState(
    searchParams.get("strategyId") ?? ""
  );
  const [selectedVersionId, setSelectedVersionId] = useState(
    searchParams.get("versionId") ?? ""
  );
  const [symbol, setSymbol] = useState("BTC/USDT:USDT");
  const [initialUsdt, setInitialUsdt] = useState("10000");
  const [leverage, setLeverage] = useState("3");

  // Shadow caution checkbox
  const [shadowConfirmed, setShadowConfirmed] = useState(false);

  // Live 3-step state
  const [liveStep, setLiveStep] = useState<1 | 2 | 3>(1);
  const [liveAccountAck, setLiveAccountAck] = useState(false);
  const [liveTypedPhrase, setLiveTypedPhrase] = useState("");

  const [completedResult, setCompletedResult] = useState<LaunchRunResponse | null>(
    null
  );
  const [streamingRunId, setStreamingRunId] = useState<string | null>(null);
  const [canceledNotice, setCanceledNotice] = useState<string | null>(null);

  const strategiesQuery = useQuery({
    queryKey: ["strategies", apiKey],
    queryFn: () => quantApi.listStrategies(apiKey),
  });

  const specsQuery = useQuery({
    queryKey: ["symbol-specs", apiKey],
    queryFn: () => quantApi.listSpecs(apiKey),
  });

  useEffect(() => {
    if (!selectedStrategyId && (strategiesQuery.data?.length ?? 0) > 0) {
      setSelectedStrategyId(strategiesQuery.data![0].strategy_id);
    }
  }, [selectedStrategyId, strategiesQuery.data]);

  const detailQuery = useQuery({
    queryKey: ["strategy-detail", selectedStrategyId, apiKey],
    queryFn: () => quantApi.getStrategyDetail(apiKey, selectedStrategyId),
    enabled: Boolean(selectedStrategyId),
  });

  useEffect(() => {
    if (detailQuery.data) {
      const exists = detailQuery.data.versions.some(
        (v) => v.version_id === selectedVersionId
      );
      if (!exists) {
        setSelectedVersionId(detailQuery.data.latest_version.version_id);
      }
    }
  }, [detailQuery.data, selectedVersionId]);

  const selectedVersion = useMemo(
    () =>
      detailQuery.data?.versions.find((v) => v.version_id === selectedVersionId) ??
      detailQuery.data?.latest_version ??
      null,
    [detailQuery.data, selectedVersionId]
  );

  // Reset confirmation states whenever env or target strategy changes
  useEffect(() => {
    setShadowConfirmed(false);
    setLiveStep(1);
    setLiveAccountAck(false);
    setLiveTypedPhrase("");
    setCompletedResult(null);
    setStreamingRunId(null);
    setCanceledNotice(null);
  }, [envMode, selectedStrategyId, selectedVersionId]);

  const statusQuery = useQuery({
    queryKey: ["run-status", envMode, streamingRunId],
    queryFn: () => quantApi.getRunStatus(apiKey, envMode, streamingRunId!),
    enabled: Boolean(streamingRunId),
    refetchInterval: (q) => (q.state.data?.status === "RUNNING" ? 250 : false),
  });

  useEffect(() => {
    const st = statusQuery.data;
    if (!st || !streamingRunId) return;
    if (st.status === "COMPLETED") {
      setCompletedResult({
        run_id: st.run_id,
        env: st.env,
        status: st.status,
        strategy_id: st.strategy_id,
        version_id: st.version_id,
      });
      setStreamingRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    } else if (st.status === "STOPPED" || st.status === "CANCELED") {
      setCanceledNotice(
        `已通过 CancellationToken 中断运行 ${st.run_id}（已处理 ${st.processed_bars}/${st.total_bars} 根 K 线）`
      );
      setStreamingRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    }
  }, [queryClient, statusQuery.data, streamingRunId]);

  const preflightQuery = useQuery({
    queryKey: [
      "preflight",
      envMode,
      selectedStrategyId,
      selectedVersionId,
      symbol,
      initialUsdt,
      leverage,
      apiKey,
    ],
    queryFn: () =>
      quantApi.preflightRun(apiKey, {
        strategy_id: selectedStrategyId,
        version_id: selectedVersionId,
        env: envMode,
        exchange: "binance",
        symbol,
        initial_usdt: initialUsdt,
        leverage,
        margin_mode: "CROSS",
      }),
    enabled: Boolean(selectedStrategyId && selectedVersionId),
  });

  const launchMutation = useMutation({
    mutationFn: () => {
      setCanceledNotice(null);
      setCompletedResult(null);
      return quantApi.launchRun(apiKey, {
        strategy_id: selectedStrategyId,
        version_id: selectedVersionId,
        env: envMode,
        exchange: "binance",
        symbol,
        initial_usdt: initialUsdt,
        leverage,
        margin_mode: "CROSS",
        async_mode: envMode === "backtest",
        step_delay_ms: envMode === "backtest" ? 6 : 0,
      });
    },
    onSuccess: (res) => {
      if (res.status === "RUNNING") {
        setStreamingRunId(res.run_id);
      } else {
        setCompletedResult(res);
      }
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => quantApi.cancelRun(apiKey, envMode, streamingRunId!),
    onSuccess: (st) => {
      setCanceledNotice(
        `已主动取消运行 ${st.run_id}（状态: ${st.status}，已处理 ${st.processed_bars}/${st.total_bars} 根 K 线）`
      );
      setStreamingRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const pf = preflightQuery.data;
  const apiErr =
    launchMutation.error instanceof QuantApiError ? launchMutation.error : null;

  if ((strategiesQuery.data?.length ?? 0) === 0 && !strategiesQuery.isLoading) {
    return (
      <EmptyState
        title="暂无可启动的策略"
        description="请先前往策略巡检工作台上传 Python 策略或通过自然语言生成策略。"
        primaryAction={{
          label: "前往策略工作台创建策略",
          onClick: () => {
            window.location.href = "/strategies";
          },
        }}
      />
    );
  }

  return (
    <div className="space-y-5 pb-12">
      <div>
        <h1 className="text-lg font-semibold text-white">
          分级运行启动中心 (/runs/new · 三环境防呆隔离)
        </h1>
        <p className="mt-0.5 text-xs text-muted-foreground">
          回测（单屏安全快速确认）、影子（门禁与隔离确认）、实盘（三步账户/保证金风控预检/确认词解锁）按资金风险等级严格差异化设计。
        </p>
      </div>

      {/* Environment Tier Selector — Safe / Caution Positioned Separately from Live Capital */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:col-span-8">
          {/* Safe Tier: Backtest */}
          <button
            type="button"
            onClick={() => setEnvMode("backtest")}
            className={cn(
              "rounded-lg border p-3.5 text-left transition-all",
              envMode === "backtest"
                ? "border-blue-500 bg-blue-950/30 ring-1 ring-blue-500/40"
                : "border-border bg-card hover:border-slate-700"
            )}
          >
            <div className="flex items-center justify-between">
              <EnvBadge env="backtest" />
              <span className="font-mono text-[10px] text-blue-300">
                安全级 · 单屏直发
              </span>
            </div>
            <div className="mt-2 text-xs font-semibold text-white">
              历史回测 (BacktestRunner)
            </div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              离线历史数据回放与归因，不触碰任何实时通道或账户资金。
            </p>
          </button>

          {/* Caution Tier: Shadow */}
          <button
            type="button"
            onClick={() => setEnvMode("shadow")}
            className={cn(
              "rounded-lg border p-3.5 text-left transition-all",
              envMode === "shadow"
                ? "border-amber-500 bg-amber-950/25 ring-1 ring-amber-500/40"
                : "border-border bg-card hover:border-slate-700"
            )}
          >
            <div className="flex items-center justify-between">
              <EnvBadge env="shadow" pulse />
              <span className="font-mono text-[10px] text-amber-300">
                谨慎级 · 需人工审批版本
              </span>
            </div>
            <div className="mt-2 text-xs font-semibold text-white">
              影子模拟交易 (ShadowRunner)
            </div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              实时行情输入 + 本地模拟盘口撮合，实盘交易 API Key 物理隔离。
            </p>
          </button>
        </div>

        {/* Physically Separated Danger Tier: Live */}
        <div className="lg:col-span-4 lg:border-l-2 lg:border-rose-900/70 lg:pl-3">
          <button
            type="button"
            onClick={() => setEnvMode("live")}
            className={cn(
              "danger-zone-stripes w-full rounded-lg border-2 p-3.5 text-left transition-all",
              envMode === "live"
                ? "border-rose-500 ring-2 ring-rose-500/40"
                : "border-rose-800/60 hover:border-rose-600"
            )}
          >
            <div className="flex items-center justify-between">
              <EnvBadge env="live" pulse />
              <span className="font-mono text-[10px] font-bold text-rose-300">
                高危动钱 · 强制三步确认
              </span>
            </div>
            <div className="mt-2 text-xs font-bold text-rose-100">
              实盘交易部署 (LiveRunner)
            </div>
            <p className="mt-0.5 text-[11px] text-rose-200/80">
              连接真实交易所委托通道，必须通过风控预检并手动输入策略名解锁。
            </p>
          </button>
        </div>
      </div>

      {/* Shared Strategy & Version Target Selector */}
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5 text-xs">
          <div className="space-y-1">
            <label className="font-medium text-slate-300">目标策略</label>
            <select
              value={selectedStrategyId}
              onChange={(e) => setSelectedStrategyId(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              {(strategiesQuery.data ?? []).map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name} ({s.strategy_id})
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="font-medium text-slate-300">策略版本</label>
            <select
              value={selectedVersionId}
              onChange={(e) => setSelectedVersionId(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              {(detailQuery.data?.versions ?? []).map((v) => (
                <option key={v.version_id} value={v.version_id}>
                  v{v.version} [{v.approval_status}]
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="font-medium text-slate-300">交易对 (SymbolSpec)</label>
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              {(specsQuery.data ?? []).map((sp) => (
                <option key={`${sp.exchange}_${sp.symbol}`} value={sp.symbol}>
                  {sp.symbol} (tick={sp.tick_size})
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="font-medium text-slate-300">资金规模 (USDT)</label>
            <input
              type="text"
              value={initialUsdt}
              onChange={(e) => setInitialUsdt(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
            />
          </div>

          <div className="space-y-1">
            <label className="font-medium text-slate-300">杠杆倍数 (x)</label>
            <input
              type="text"
              value={leverage}
              onChange={(e) => setLeverage(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
            />
          </div>
        </div>
      </section>

      {/* Completion Banner */}
      {completedResult && (
        <div className="rounded-lg border border-emerald-500/45 bg-emerald-950/25 p-4 text-xs space-y-2.5">
          <div className="flex items-center gap-2 font-semibold text-emerald-300">
            <CheckCircle2 className="h-4 w-4" />
            <span>
              [{completedResult.env.toUpperCase()}] 运行已成功完成！run_id ={" "}
              <code className="font-mono text-white">{completedResult.run_id}</code>
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {completedResult.env === "backtest" && (
              <Link href={`/reports/${encodeURIComponent(completedResult.run_id)}`}>
                <Button size="sm" variant="default">
                  <span>查看深度回测报告</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </Button>
              </Link>
            )}
            <Link
              href={`/ledger?env=${encodeURIComponent(
                completedResult.env
              )}&runId=${encodeURIComponent(completedResult.run_id)}`}
            >
              <Button size="sm" variant="outline">
                查看统一交易账本流水
              </Button>
            </Link>
          </div>
        </div>
      )}

      {canceledNotice && (
        <div className="rounded-lg border border-amber-500/45 bg-amber-950/25 p-3.5 text-xs text-amber-200 font-mono">
          {canceledNotice}
        </div>
      )}

      {launchMutation.isPending || Boolean(streamingRunId) ? (
        <RunLoadingState
          title={`正在执行 ${envMode.toUpperCase()} 运行 (${selectedStrategyId})`}
          currentStageLabel={
            statusQuery.data
              ? `${statusQuery.data.stage} · 实时流式驱动与账本落库`
              : "同构策略上下文驱动与逐笔账本持久化"
          }
          progressPct={statusQuery.data?.progress_pct ?? 8}
          processedBars={statusQuery.data?.processed_bars}
          totalBars={statusQuery.data?.total_bars}
          elapsedMs={statusQuery.data?.elapsed_ms}
          estimatedRemainingMs={statusQuery.data?.estimated_remaining_ms}
          onCancel={streamingRunId ? () => cancelMutation.mutate() : undefined}
          canceling={cancelMutation.isPending}
        />
      ) : (
        <>
          {launchMutation.isError && (
            <ActionableErrorState
              title="运行启动失败"
              reasonCode={apiErr?.reasonCode ?? "RUN_LAUNCH_ERROR"}
              message={apiErr?.message ?? String(launchMutation.error)}
              remediation="请核查审批门禁、RBAC 权限及风控限制后重试。"
              rawDetail={apiErr?.rawDetail}
            />
          )}

          {/* =============================================================== */}
          {/* FLOW A: BACKTEST — 1-SCREEN SAFE FAST CONFIRM                   */}
          {/* =============================================================== */}
          {envMode === "backtest" && (
            <section className="rounded-lg border border-blue-500/40 bg-card p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-white">
                    回测单屏快速确认（安全操作 · 无需繁琐审批）
                  </h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    使用确定性哈希生成可复现 `run_id`，自动计算夏普、最大回撤、月度收益矩阵与手续费归因。
                  </p>
                </div>
                <BacktestActionButton
                  size="lg"
                  disabled={!selectedStrategyId || !selectedVersionId}
                  onClick={() => launchMutation.mutate()}
                >
                  <Play className="h-4 w-4 fill-current" />
                  <span>立即启动历史回测</span>
                </BacktestActionButton>
              </div>
            </section>
          )}

          {/* =============================================================== */}
          {/* FLOW B: SHADOW — CAUTION GATE & ISOLATION CONFIRM               */}
          {/* =============================================================== */}
          {envMode === "shadow" && (
            <section className="rounded-lg border border-amber-500/45 bg-amber-950/10 p-5 space-y-4 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <EnvBadge env="shadow" pulse />
                  <h2 className="text-sm font-semibold text-amber-200">
                    影子模拟交易确认（需版本人工审批通过 + 模拟撮合隔离核对）
                  </h2>
                </div>
                {selectedVersion && (
                  <ApprovalBadge status={selectedVersion.approval_status} />
                )}
              </div>

              {pf && !pf.passed && (
                <ActionableErrorState
                  title="影子启动前置门禁阻断"
                  reasonCode={pf.blocking_reasons[0]?.code ?? "SHADOW_BLOCKED"}
                  message={pf.blocking_reasons.map((b) => b.message).join("；")}
                  remediation={pf.blocking_reasons.map((b) => b.remediation).join("；")}
                  actions={[
                    {
                      label: "前往策略详情完成人工审批",
                      onClick: () => {
                        window.location.href = `/strategies/${encodeURIComponent(
                          selectedStrategyId
                        )}?section=code`;
                      },
                    },
                  ]}
                />
              )}

              <label className="flex items-center gap-2 rounded border border-amber-500/35 bg-slate-950 p-3 text-slate-200 cursor-pointer">
                <input
                  type="checkbox"
                  checked={shadowConfirmed}
                  onChange={(e) => setShadowConfirmed(e.target.checked)}
                  className="accent-amber-500"
                />
                <span>
                  我已确认当前版本参数无误，知悉影子环境将按盘口深度排队比例模拟撮合且不向交易所发送真实订单。
                </span>
              </label>

              <div className="flex justify-end">
                <ShadowActionButton
                  size="lg"
                  disabled={!pf?.passed || !shadowConfirmed}
                  onClick={() => launchMutation.mutate()}
                >
                  <span>确认启动影子模拟交易</span>
                </ShadowActionButton>
              </div>
            </section>
          )}

          {/* =============================================================== */}
          {/* FLOW C: LIVE — MANDATORY 3-STEP REAL-MONEY WIZARD               */}
          {/* =============================================================== */}
          {envMode === "live" && (
            <IsolatedDangerZone
              title="实盘交易三步强制确认向导 (LIVE CAPITAL WIZARD)"
              subtitle="第 1 步确认环境与账户 → 第 2 步核对预计保证金/单笔上限/当前杠杆风控预检 → 第 3 步手动输入策略 ID 解锁"
            >
              <div className="w-full mt-3 space-y-4 text-xs">
                {/* Step Progress */}
                <div className="grid grid-cols-3 gap-2">
                  {[
                    [1, "第 1 步 · 确认环境与账户"],
                    [2, "第 2 步 · 确认参数与风控预检"],
                    [3, "第 3 步 · 输入确认词解锁"],
                  ].map(([idx, label]) => (
                    <div
                      key={idx}
                      className={`rounded border px-3 py-2 text-center font-medium ${
                        liveStep === idx
                          ? "border-rose-500 bg-rose-950/60 text-white"
                          : liveStep > Number(idx)
                            ? "border-emerald-500/40 bg-emerald-950/25 text-emerald-300"
                            : "border-slate-800 bg-slate-950/70 text-slate-500"
                      }`}
                    >
                      {label}
                    </div>
                  ))}
                </div>

                {liveStep === 1 && pf && (
                  <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/90 p-4">
                    <div className="grid grid-cols-2 gap-3 font-mono sm:grid-cols-4">
                      <div>
                        <div className="text-slate-400">目标账户环境</div>
                        <div className="mt-1 font-bold text-amber-300">
                          {pf.account_check.is_testnet ? "TESTNET / PAPER" : "MAINNET"}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-400">可用保证金余额</div>
                        <div className="mt-1 font-bold text-white">
                          {formatCurrency(pf.account_check.available_balance_usdt)}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-400">禁提现权限校验</div>
                        <div className="mt-1 font-bold text-emerald-400">
                          {pf.account_check.api_key_no_withdraw ? "通过 (NO_WITHDRAW)" : "失败"}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-400">IP 白名单校验</div>
                        <div className="mt-1 font-bold text-emerald-400">
                          {pf.account_check.ip_whitelist_verified ? "通过" : "未绑定"}
                        </div>
                      </div>
                    </div>

                    <label className="flex items-center gap-2 pt-2 text-slate-200 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={liveAccountAck}
                        onChange={(e) => setLiveAccountAck(e.target.checked)}
                        className="accent-rose-500"
                      />
                      <span>我已核对目标交易账户、合约规格与分配本金无误。</span>
                    </label>

                    <div className="flex justify-end">
                      <Button
                        variant="liveDanger"
                        disabled={!liveAccountAck}
                        onClick={() => setLiveStep(2)}
                      >
                        <span>下一步：参数与风控预检</span>
                        <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                )}

                {liveStep === 2 && pf && (
                  <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/90 p-4">
                    {!pf.passed && (
                      <ActionableErrorState
                        title="实盘风控预检未通过（禁止继续）"
                        reasonCode={pf.blocking_reasons[0]?.code ?? "LIVE_BLOCKED"}
                        message={pf.blocking_reasons.map((b) => b.message).join("；")}
                        remediation={pf.blocking_reasons.map((b) => b.remediation).join("；")}
                        actions={[
                          {
                            label: "前往策略代码区执行人工审批",
                            onClick: () => {
                              window.location.href = `/strategies/${encodeURIComponent(
                                selectedStrategyId
                              )}?section=code`;
                            },
                          },
                        ]}
                      />
                    )}

                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                      <div className="rounded border border-slate-800 bg-slate-900 p-3">
                        <div className="text-slate-400">预计初始占用保证金</div>
                        <div className="mt-1 text-base font-bold text-white">
                          <NumericDisplay
                            value={pf.margin_and_risk_estimate.estimated_initial_margin_usdt}
                            mode="currency"
                          />
                        </div>
                      </div>
                      <div className="rounded border border-slate-800 bg-slate-900 p-3">
                        <div className="text-slate-400">单笔委托名义上限</div>
                        <div className="mt-1 text-base font-bold text-white">
                          <NumericDisplay
                            value={pf.margin_and_risk_estimate.max_order_notional_limit_usdt}
                            mode="currency"
                          />
                        </div>
                      </div>
                      <div className="rounded border border-slate-800 bg-slate-900 p-3">
                        <div className="text-slate-400">当前设定杠杆</div>
                        <div className="mt-1 font-mono text-base font-bold text-amber-300">
                          {pf.margin_and_risk_estimate.configured_leverage}x{" "}
                          <span className="text-xs font-normal text-slate-400">
                            (总上限 {pf.margin_and_risk_estimate.risk_max_total_leverage}x)
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex justify-between pt-2">
                      <Button variant="outline" onClick={() => setLiveStep(1)}>
                        上一步
                      </Button>
                      <Button
                        variant="liveDanger"
                        disabled={!pf.passed}
                        onClick={() => setLiveStep(3)}
                      >
                        <ShieldCheck className="h-3.5 w-3.5" />
                        <span>风控预检通过 · 下一步输入确认词</span>
                      </Button>
                    </div>
                  </div>
                )}

                {liveStep === 3 && (
                  <div className="space-y-3 rounded-lg border border-rose-500/60 bg-slate-950/95 p-4">
                    <div className="flex items-center gap-2 font-semibold text-rose-200">
                      <ShieldAlert className="h-4 w-4 text-rose-400" />
                      <span>
                        请手动输入策略标识{" "}
                        <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-rose-300">
                          {selectedStrategyId}
                        </code>{" "}
                        以解锁最终实盘部署：
                      </span>
                    </div>
                    <input
                      type="text"
                      value={liveTypedPhrase}
                      onChange={(e) => setLiveTypedPhrase(e.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={selectedStrategyId}
                      className="h-9 w-full rounded border border-rose-500/60 bg-slate-900 px-3 font-mono text-white focus:border-rose-400 focus:outline-none"
                    />
                    <div className="flex justify-between pt-1">
                      <Button variant="outline" onClick={() => setLiveStep(2)}>
                        上一步
                      </Button>
                      <LiveDangerActionButton
                        size="lg"
                        disabled={
                          liveTypedPhrase !== selectedStrategyId || !pf?.passed
                        }
                        onClick={() => launchMutation.mutate()}
                      >
                        <Rocket className="h-4 w-4" />
                        <span>授权并立即启动 LIVE 实盘</span>
                      </LiveDangerActionButton>
                    </div>
                  </div>
                )}
              </div>
            </IsolatedDangerZone>
          )}
        </>
      )}
    </div>
  );
}

export default function LaunchRunCenterPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载分级启动中心...
        </div>
      }
    >
      <LaunchRunCenterInner />
    </Suspense>
  );
}
