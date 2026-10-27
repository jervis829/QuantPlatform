"use client";

import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  Play,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import { formatCurrency } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import type { LaunchRunResponse } from "../../types/openapi.generated";
import { ApprovalBadge, EnvBadge } from "../quant/badges";
import { NumericDisplay } from "../quant/numeric-display";
import { ActionableErrorState, RunLoadingState } from "../quant/state-views";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

export interface QuickBacktestModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategyId: string;
  strategyName: string;
  versionId: string;
  onCompleted?: (res: LaunchRunResponse) => void;
}

export function QuickBacktestModal({
  open,
  onOpenChange,
  strategyId,
  strategyName,
  versionId,
  onCompleted,
}: QuickBacktestModalProps) {
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const [symbol, setSymbol] = useState("BTC/USDT:USDT");
  const [initialUsdt, setInitialUsdt] = useState("10000");
  const [completedRun, setCompletedRun] = useState<LaunchRunResponse | null>(null);

  const specsQuery = useQuery({
    queryKey: ["symbol-specs", apiKey],
    queryFn: () => quantApi.listSpecs(apiKey),
    enabled: open,
  });

  useEffect(() => {
    if (open) setCompletedRun(null);
  }, [open, strategyId, versionId]);

  const launchMutation = useMutation({
    mutationFn: () =>
      quantApi.launchRun(apiKey, {
        strategy_id: strategyId,
        version_id: versionId,
        env: "backtest",
        exchange: "binance",
        symbol,
        initial_usdt: initialUsdt,
      }),
    onSuccess: (data) => {
      setCompletedRun(data);
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
      onCompleted?.(data);
    },
  });

  const apiErr =
    launchMutation.error instanceof QuantApiError ? launchMutation.error : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg border-blue-500/40">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <EnvBadge env="backtest" />
            <DialogTitle>发起历史回测 · {strategyName}</DialogTitle>
          </div>
          <DialogDescription>
            安全投研环境（零真实资金触碰）· 版本 <code className="font-mono text-slate-200">{versionId}</code>
          </DialogDescription>
        </DialogHeader>

        {launchMutation.isPending ? (
          <RunLoadingState
            title={`正在执行回测 (${symbol})`}
            currentStageLabel="Walk-the-Book 深度撮合与绩效归因计算"
            progressPct={70}
          />
        ) : completedRun ? (
          <div className="space-y-3 rounded-lg border border-emerald-500/40 bg-emerald-950/20 p-4 text-xs">
            <div className="flex items-center gap-2 font-semibold text-emerald-300">
              <CheckCircle2 className="h-4 w-4" />
              <span>回测执行完成！确定性哈希 ID 已落库</span>
            </div>
            <div className="font-mono text-[11px] text-slate-300 break-all">
              run_id: <strong className="text-white">{completedRun.run_id}</strong>
            </div>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Link href={`/reports/${encodeURIComponent(completedRun.run_id)}`}>
                <Button size="sm" variant="default" onClick={() => onOpenChange(false)}>
                  <span>查看回测归因报告</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </Button>
              </Link>
              <Link
                href={`/ledger?env=backtest&runId=${encodeURIComponent(completedRun.run_id)}`}
              >
                <Button size="sm" variant="outline" onClick={() => onOpenChange(false)}>
                  查看逐笔委托与成交账本
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <div className="space-y-3 text-xs">
            {launchMutation.isError && (
              <ActionableErrorState
                title="回测启动失败"
                reasonCode={apiErr?.reasonCode ?? "BACKTEST_LAUNCH_FAILED"}
                message={apiErr?.message ?? String(launchMutation.error)}
                remediation="请检查交易对是否有可用历史行情数据，并确认策略参数在有效范围内。"
                rawDetail={apiErr?.rawDetail}
              />
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">合约交易对 (SymbolSpec)</label>
                <select
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-xs text-white"
                >
                  {(specsQuery.data ?? [{ symbol: "BTC/USDT:USDT", exchange: "binance" }]).map(
                    (s) => (
                      <option key={`${s.exchange}_${s.symbol}`} value={s.symbol}>
                        {s.symbol} ({s.exchange})
                      </option>
                    )
                  )}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">初始回测本金 (USDT)</label>
                <input
                  type="text"
                  value={initialUsdt}
                  onChange={(e) => setInitialUsdt(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
                />
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          {!completedRun && (
            <Button
              variant="default"
              disabled={launchMutation.isPending}
              onClick={() => launchMutation.mutate()}
            >
              <Play className="h-3.5 w-3.5 fill-current" />
              <span>立即开始回测</span>
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export interface ShadowLaunchModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategyId: string;
  strategyName: string;
  versionId: string;
  approvalStatus: string;
  onJumpToApproval?: () => void;
}

export function ShadowLaunchModal({
  open,
  onOpenChange,
  strategyId,
  strategyName,
  versionId,
  approvalStatus,
  onJumpToApproval,
}: ShadowLaunchModalProps) {
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();
  const [symbol, setSymbol] = useState("BTC/USDT:USDT");

  const preflightQuery = useQuery({
    queryKey: ["preflight", "shadow", strategyId, versionId, symbol, apiKey],
    queryFn: () =>
      quantApi.preflightRun(apiKey, {
        strategy_id: strategyId,
        version_id: versionId,
        env: "shadow",
        symbol,
        exchange: "binance",
      }),
    enabled: open,
  });

  const launchMutation = useMutation({
    mutationFn: () =>
      quantApi.launchRun(apiKey, {
        strategy_id: strategyId,
        version_id: versionId,
        env: "shadow",
        symbol,
        exchange: "binance",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
      onOpenChange(false);
    },
  });

  const pf = preflightQuery.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg border-amber-500/45">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <EnvBadge env="shadow" pulse />
            <DialogTitle>启动影子模拟交易 · {strategyName}</DialogTitle>
          </div>
          <DialogDescription>
            连接实时盘口并经本地同构撮合成交，物理禁用任何实盘交易 API Key。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-xs">
          <div className="flex items-center justify-between rounded border border-slate-800 bg-slate-900 p-2.5">
            <span>
              目标版本: <code className="font-mono font-bold text-white">{versionId}</code>
            </span>
            <ApprovalBadge status={approvalStatus} />
          </div>

          <div className="space-y-1">
            <label className="text-slate-300 font-medium">目标合约交易对</label>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
            />
          </div>

          {pf && !pf.passed && (
            <ActionableErrorState
              title="影子环境启动门禁未通过"
              reasonCode={pf.blocking_reasons[0]?.code ?? "PREFLIGHT_BLOCKED"}
              message={pf.blocking_reasons.map((b) => b.message).join("；")}
              remediation={pf.blocking_reasons.map((b) => b.remediation).join("；")}
              actions={
                onJumpToApproval
                  ? [
                      {
                        label: "前往代码与版本区人工审批",
                        onClick: () => {
                          onOpenChange(false);
                          onJumpToApproval();
                        },
                      },
                    ]
                  : []
              }
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="shadow"
            disabled={!pf?.passed || launchMutation.isPending}
            onClick={() => launchMutation.mutate()}
          >
            {launchMutation.isPending ? "启动中..." : "确认启动 SHADOW 模拟"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export interface LiveThreeStepWizardModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategyId: string;
  strategyName: string;
  versionId: string;
  versionParams: Record<string, unknown>;
  onJumpToApproval?: () => void;
}

export function LiveThreeStepWizardModal({
  open,
  onOpenChange,
  strategyId,
  strategyName,
  versionId,
  versionParams,
  onJumpToApproval,
}: LiveThreeStepWizardModalProps) {
  const { apiKey } = usePreferences();
  const queryClient = useQueryClient();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [symbol, setSymbol] = useState("BTC/USDT:USDT");
  const [initialUsdt, setInitialUsdt] = useState("10000");
  const [leverage, setLeverage] = useState("3");
  const [accountAck, setAccountAck] = useState(false);
  const [typedPhrase, setTypedPhrase] = useState("");

  useEffect(() => {
    if (open) {
      setStep(1);
      setAccountAck(false);
      setTypedPhrase("");
    }
  }, [open, strategyId, versionId]);

  const preflightQuery = useQuery({
    queryKey: [
      "preflight",
      "live",
      strategyId,
      versionId,
      symbol,
      initialUsdt,
      leverage,
      apiKey,
    ],
    queryFn: () =>
      quantApi.preflightRun(apiKey, {
        strategy_id: strategyId,
        version_id: versionId,
        env: "live",
        exchange: "binance",
        symbol,
        initial_usdt: initialUsdt,
        leverage,
        margin_mode: "CROSS",
      }),
    enabled: open,
  });

  const launchLiveMutation = useMutation({
    mutationFn: () =>
      quantApi.launchRun(apiKey, {
        strategy_id: strategyId,
        version_id: versionId,
        env: "live",
        exchange: "binance",
        symbol,
        initial_usdt: initialUsdt,
        leverage,
        margin_mode: "CROSS",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["strategy-detail", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
      onOpenChange(false);
    },
  });

  const pf = preflightQuery.data;
  const phraseMatched = typedPhrase === strategyId;
  const apiErr =
    launchLiveMutation.error instanceof QuantApiError ? launchLiveMutation.error : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl border-rose-500/70">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-rose-400" />
            <EnvBadge env="live" pulse />
            <DialogTitle className="text-rose-200">
              部署策略至实盘 (LIVE 三步安全确认向导)
            </DialogTitle>
          </div>
          <DialogDescription>
            策略 <strong className="text-white">{strategyName}</strong> (
            <code className="font-mono">{versionId}</code>) · 实盘运行将通过 ExchangeBroker 发送真实委托指令。
          </DialogDescription>
        </DialogHeader>

        {/* 3-Step Indicator */}
        <div className="grid grid-cols-3 gap-2 text-xs font-medium">
          {[
            [1, "1. 确认环境与账户"],
            [2, "2. 参数与风控预检"],
            [3, "3. 输入确认词解锁"],
          ].map(([idx, label]) => (
            <div
              key={idx}
              className={`rounded border px-3 py-2 text-center ${
                step === idx
                  ? "border-rose-500 bg-rose-950/45 text-white font-semibold"
                  : step > Number(idx)
                    ? "border-emerald-500/40 bg-emerald-950/20 text-emerald-300"
                    : "border-slate-800 bg-slate-900/60 text-slate-500"
              }`}
            >
              {label}
            </div>
          ))}
        </div>

        {/* Step 1: Confirm Env & Exchange Account */}
        {step === 1 && (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <label className="text-slate-300">目标交易对</label>
                <input
                  type="text"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-300">分配资金额度 (USDT)</label>
                <input
                  type="text"
                  value={initialUsdt}
                  onChange={(e) => setInitialUsdt(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                />
              </div>
              <div className="space-y-1">
                <label className="text-slate-300">目标杠杆倍数 (x)</label>
                <input
                  type="text"
                  value={leverage}
                  onChange={(e) => setLeverage(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-white"
                />
              </div>
            </div>

            {pf && (
              <div className="rounded-lg border border-slate-800 bg-slate-900/80 p-3 space-y-2">
                <div className="font-semibold text-slate-200">
                  交易所账户安全核验 (Exchange Single Source of Truth)
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
                  <div className="rounded bg-slate-950 p-2">
                    <span className="text-slate-400">账户环境: </span>
                    <strong className="text-amber-300">
                      {pf.account_check.is_testnet ? "BINANCE TESTNET / PAPER" : "MAINNET"}
                    </strong>
                  </div>
                  <div className="rounded bg-slate-950 p-2">
                    <span className="text-slate-400">可用保证金余额: </span>
                    <strong className="text-white">
                      {formatCurrency(pf.account_check.available_balance_usdt)}
                    </strong>
                  </div>
                  <div className="rounded bg-slate-950 p-2">
                    <span className="text-slate-400">API Key 禁提现权限: </span>
                    <strong
                      className={
                        pf.account_check.api_key_no_withdraw
                          ? "text-emerald-400"
                          : "text-rose-400"
                      }
                    >
                      {pf.account_check.api_key_no_withdraw ? "已验证 (NO_WITHDRAW)" : "异常"}
                    </strong>
                  </div>
                  <div className="rounded bg-slate-950 p-2">
                    <span className="text-slate-400">IP 白名单绑定: </span>
                    <strong
                      className={
                        pf.account_check.ip_whitelist_verified
                          ? "text-emerald-400"
                          : "text-rose-400"
                      }
                    >
                      {pf.account_check.ip_whitelist_verified ? "已验证" : "未绑定"}
                    </strong>
                  </div>
                </div>
              </div>
            )}

            <label className="flex items-center gap-2 rounded border border-rose-500/35 bg-rose-950/20 p-2.5 text-slate-200 cursor-pointer">
              <input
                type="checkbox"
                checked={accountAck}
                onChange={(e) => setAccountAck(e.target.checked)}
                className="accent-rose-500"
              />
              <span>
                我已核实目标交易所账户、交易对规格（tick_size={pf?.symbol_spec.tick_size ?? "-"}）与分配本金无误。
              </span>
            </label>
          </div>
        )}

        {/* Step 2: Confirm Params & Pre-Trade Risk Preflight */}
        {step === 2 && (
          <div className="space-y-3 text-xs">
            {pf && !pf.passed && (
              <ActionableErrorState
                title="事前风控预检阻断 (无法进入实盘)"
                reasonCode={pf.blocking_reasons[0]?.code ?? "PREFLIGHT_BLOCKED"}
                message={pf.blocking_reasons.map((b) => b.message).join("；")}
                remediation={pf.blocking_reasons.map((b) => b.remediation).join("；")}
                actions={
                  onJumpToApproval
                    ? [
                        {
                          label: "前往代码与版本区完成人工审批",
                          onClick: () => {
                            onOpenChange(false);
                            onJumpToApproval();
                          },
                        },
                      ]
                    : []
                }
              />
            )}

            {pf && (
              <div className="grid grid-cols-3 gap-2.5">
                <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
                  <div className="text-[11px] text-slate-400">预计初始占用保证金</div>
                  <div className="mt-1 text-base font-bold text-white">
                    <NumericDisplay
                      value={pf.margin_and_risk_estimate.estimated_initial_margin_usdt}
                      mode="currency"
                    />
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-400">
                    预估强平距离: {pf.margin_and_risk_estimate.estimated_liq_distance_pct}%
                  </div>
                </div>

                <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
                  <div className="text-[11px] text-slate-400">单笔委托名义上限</div>
                  <div className="mt-1 text-base font-bold text-white">
                    <NumericDisplay
                      value={pf.margin_and_risk_estimate.max_order_notional_limit_usdt}
                      mode="currency"
                    />
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-400">
                    日最大亏损熔断:{" "}
                    {formatCurrency(pf.margin_and_risk_estimate.max_daily_loss_limit_usdt)}
                  </div>
                </div>

                <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
                  <div className="text-[11px] text-slate-400">当前设定杠杆</div>
                  <div className="mt-1 font-mono text-base font-bold text-amber-300">
                    {pf.margin_and_risk_estimate.configured_leverage}x
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-400">
                    风控总上限 {pf.margin_and_risk_estimate.risk_max_total_leverage}x · 合约上限{" "}
                    {pf.margin_and_risk_estimate.symbol_max_leverage}x
                  </div>
                </div>
              </div>
            )}

            <div className="rounded border border-slate-800 bg-slate-950 p-3">
              <div className="mb-1.5 flex items-center justify-between text-[11px] text-slate-400">
                <span>生效策略参数快照 ({versionId})</span>
                <span className="font-mono text-emerald-400">
                  审批状态: {pf?.approval_status} {pf?.approved_by ? `(${pf.approved_by})` : ""}
                </span>
              </div>
              <div className="flex flex-wrap gap-2 font-mono text-[11px]">
                {Object.entries(versionParams).map(([k, v]) => (
                  <span
                    key={k}
                    className="rounded border border-slate-800 bg-slate-900 px-2 py-0.5 text-slate-200"
                  >
                    {k}=<strong className="text-white">{String(v)}</strong>
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Type Exact Confirmation Phrase */}
        {step === 3 && (
          <div className="space-y-3 text-xs">
            {launchLiveMutation.isError && (
              <ActionableErrorState
                title="实盘启动请求被拒绝"
                reasonCode={apiErr?.reasonCode ?? "LIVE_LAUNCH_ERROR"}
                message={apiErr?.message ?? String(launchLiveMutation.error)}
                remediation="请确认当前角色具备 OPERATOR/ADMIN 权限、策略已人工审批且未开启全局维护或紧急停机。"
                rawDetail={apiErr?.rawDetail}
              />
            )}

            <div className="rounded-lg border border-rose-500/50 bg-rose-950/25 p-3.5 space-y-2">
              <div className="flex items-center gap-2 font-semibold text-rose-200">
                <AlertTriangle className="h-4 w-4 text-rose-400" />
                <span>最终解锁：请手动输入策略标识以确认实盘动钱授权</span>
              </div>
              <p className="text-[11px] text-slate-300">
                请输入 <code className="rounded bg-slate-950 px-1.5 py-0.5 font-mono font-bold text-rose-300">{strategyId}</code>（不支持自动填充，启动后可随时通过全局右上角 KILL SWITCH 亚秒级全撤平仓）：
              </p>
              <input
                type="text"
                value={typedPhrase}
                onChange={(e) => setTypedPhrase(e.target.value)}
                placeholder={strategyId}
                autoComplete="off"
                spellCheck={false}
                className="h-9 w-full rounded border border-rose-500/60 bg-slate-950 px-3 font-mono text-xs text-white focus:border-rose-400 focus:outline-none"
              />
            </div>
          </div>
        )}

        <DialogFooter>
          {step > 1 ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setStep((s) => (s === 3 ? 2 : 1))}
            >
              上一步
            </Button>
          ) : (
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              取消
            </Button>
          )}

          {step === 1 && (
            <Button
              type="button"
              variant="liveDanger"
              disabled={!accountAck || preflightQuery.isLoading}
              onClick={() => setStep(2)}
            >
              <span>下一步：风控预检</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}

          {step === 2 && (
            <Button
              type="button"
              variant="liveDanger"
              disabled={!pf?.passed}
              onClick={() => setStep(3)}
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              <span>预检通过 · 进入输入确认词</span>
            </Button>
          )}

          {step === 3 && (
            <Button
              type="button"
              variant="liveDanger"
              disabled={!phraseMatched || !pf?.passed || launchLiveMutation.isPending}
              onClick={() => launchLiveMutation.mutate()}
            >
              {launchLiveMutation.isPending ? "正在启动 LIVE 实盘..." : "确认授权并启动 LIVE 实盘"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
