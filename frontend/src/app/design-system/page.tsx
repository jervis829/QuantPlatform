"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Sparkles } from "lucide-react";
import { quantApi } from "../../lib/apiClient";
import { formatBps, formatCompactNumber, formatCurrency, formatPercent } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import type { SymbolSpecSummary } from "../../types/openapi.generated";
import {
  ApprovalBadge,
  EnvBadge,
  RunStatusBadge,
  SeverityBadge,
} from "../../components/quant/badges";
import { FinancialChartContainer } from "../../components/quant/chart-container";
import { ConfirmActionDialog } from "../../components/quant/confirm-dialog";
import { QuantDataTable } from "../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../components/quant/state-views";
import {
  BacktestActionButton,
  Button,
  IsolatedDangerZone,
  LiveDangerActionButton,
  ShadowActionButton,
} from "../../components/ui/button";

function DesignSystemShowcaseInner() {
  const { apiKey, pnlPolarity, setPnlPolarity, setKillSwitchModalOpen } = usePreferences();
  const queryClient = useQueryClient();

  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);
  const [shadowConfirmOpen, setShadowConfirmOpen] = useState(false);
  const [demoLoadingPct, setDemoLoadingPct] = useState(62.5);
  const [focusedTradeSec, setFocusedTradeSec] = useState<number | null>(null);

  // Live API queries (Zero mock data!)
  const overviewQuery = useQuery({
    queryKey: ["system-overview", apiKey],
    queryFn: () => quantApi.systemOverview(apiKey),
  });

  const specsQuery = useQuery({
    queryKey: ["symbol-specs", apiKey],
    queryFn: () => quantApi.listSpecs(apiKey),
  });

  const btRunsQuery = useQuery({
    queryKey: ["runs", "backtest", apiKey],
    queryFn: () => quantApi.listRuns(apiKey, { env: "backtest", limit: 10 }),
  });

  const latestBtRunId = btRunsQuery.data?.[0]?.run_id ?? null;

  const chartDataQuery = useQuery({
    queryKey: ["run-chart-data", "backtest", latestBtRunId, apiKey],
    queryFn: () => quantApi.getRunChartData(apiKey, "backtest", latestBtRunId!),
    enabled: Boolean(latestBtRunId),
  });

  // One-click real backend bootstrap (NL strategy + backtest) when DB has no runs yet
  const bootstrapRealRunMutation = useMutation({
    mutationFn: async () => {
      const stratId = `ds_sma_${Date.now().toString().slice(-5)}`;
      const ver = await quantApi.generateNLStrategy(apiKey, {
        strategy_id: stratId,
        name: "Design System Real Backtest Verification",
        prompt: "当 BTC/USDT:USDT 4周期均线上穿 10周期均线时买入 0.05 BTC，下穿时平仓",
      });
      return quantApi.launchRun(apiKey, {
        strategy_id: stratId,
        version_id: ver.version_id,
        env: "backtest",
        symbol: "BTC/USDT:USDT",
        exchange: "binance",
        initial_usdt: "10000",
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
      void queryClient.invalidateQueries({ queryKey: ["system-overview"] });
    },
  });

  const specColumns = useMemo<ColumnDef<SymbolSpecSummary, unknown>[]>(
    () => [
      {
        id: "symbol",
        accessorKey: "symbol",
        header: "交易对 (Symbol)",
        cell: ({ row }) => (
          <span className="font-mono font-semibold text-white">{row.original.symbol}</span>
        ),
      },
      {
        id: "exchange",
        accessorKey: "exchange",
        header: "交易所",
        cell: ({ row }) => (
          <span className="uppercase text-slate-300">{row.original.exchange}</span>
        ),
      },
      {
        id: "contract_type",
        accessorKey: "contract_type",
        header: "合约类型",
        cell: ({ row }) => (
          <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-sky-300">
            {row.original.contract_type}
          </span>
        ),
      },
      {
        id: "tick_size",
        accessorKey: "tick_size",
        header: "价格精度 (tick_size)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{row.original.tick_size}</span>,
      },
      {
        id: "step_size",
        accessorKey: "step_size",
        header: "数量步长 (step_size)",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{row.original.step_size}</span>,
      },
      {
        id: "min_notional",
        accessorKey: "min_notional",
        header: "最小名义额",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.min_notional}
            mode="currency"
            currency={row.original.quote_asset}
          />
        ),
      },
      {
        id: "max_leverage",
        accessorKey: "max_leverage",
        header: "最高杠杆",
        meta: { align: "right", mono: true },
        cell: ({ row }) => <span>{row.original.max_leverage}x</span>,
      },
      {
        id: "fees",
        accessorFn: (r) => r.taker_fee,
        header: "Maker / Taker 费率",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <span>
            {formatPercent(row.original.maker_fee, { inputIsRatio: true, decimals: 3 })} /{" "}
            {formatPercent(row.original.taker_fee, { inputIsRatio: true, decimals: 3 })}
          </span>
        ),
      },
    ],
    []
  );

  const ov = overviewQuery.data;

  return (
    <div className="space-y-6 pb-10">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-lg font-semibold text-white">
            批 1 验收页 · 量化设计基础与组件规范 (/design-system)
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            展示设计 Token、危险分级隔离按钮、统一精度数字格式、空/加载/错误三态组件、TanStack Table 与 lightweight-charts 专业金融图表（全部绑定真实后端 API）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setPnlPolarity(pnlPolarity === "green-up" ? "red-up" : "green-up")
            }
          >
            切换涨跌极性（当前：{pnlPolarity === "green-up" ? "绿涨红跌" : "红涨绿跌"}）
          </Button>
        </div>
      </div>

      {/* Section 1: First-Glance KPI Cards & Number Formatting Rules */}
      <section className="space-y-2.5">
        <h2 className="text-sm font-semibold text-slate-200">
          1. 信息密度「一眼层」指标卡与数字格式化规范（千分位 · 显式货币单位 · 负号与零值区分 · 极性可配）
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <KpiMetricCard
            label="今日已实现净盈亏"
            timeWindow="UTC 24H"
            benchmarkLabel={`昨日: ${formatCurrency(ov?.yesterday_pnl_usdt ?? "0")}`}
            deltaNode={
              <NumericDisplay
                value={ov?.today_return_pct ?? 0}
                mode="percent"
                coloredBySign
                showPlusSign
              />
            }
          >
            <NumericDisplay
              value={ov?.today_pnl_usdt ?? "0"}
              mode="currency"
              coloredBySign
              showPlusSign
            />
          </KpiMetricCard>

          <KpiMetricCard
            label="正收益涨跌着色示例 (+18,420.55 USDT)"
            timeWindow="极性联动"
            benchmarkLabel={`大数缩写: ${formatCompactNumber(18420550, { unit: "USDT", showPlusSign: true })}`}
            deltaNode={<span className="font-mono text-pnl-up">{formatBps(14.5)}</span>}
          >
            <NumericDisplay
              value="18420.55"
              mode="currency"
              coloredBySign
              showPlusSign
            />
          </KpiMetricCard>

          <KpiMetricCard
            label="负收益涨跌着色示例 (-1,205.40 USDT)"
            timeWindow="极性联动"
            benchmarkLabel="严格负号前缀与等宽对齐"
            deltaNode={<span className="font-mono text-pnl-down">{formatBps(-8.25)}</span>}
          >
            <NumericDisplay
              value="-1205.40"
              mode="currency"
              coloredBySign
              showPlusSign
            />
          </KpiMetricCard>

          <KpiMetricCard
            label="精确零值视觉区分 (0.00 USDT)"
            timeWindow="零值中性灰"
            benchmarkLabel="不带正负号，与微小亏损明确区分"
            alertTone={(ov?.unacknowledged_alert_count ?? 0) > 0 ? "warning" : "none"}
          >
            <NumericDisplay value="0" mode="currency" coloredBySign showPlusSign />
          </KpiMetricCard>
        </div>
      </section>

      {/* Section 2: Principle 1 — Risk-Graded Action Buttons & Physical Isolation */}
      <section className="rounded-lg border border-border bg-card p-4 space-y-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">
            2. 危险操作分级与物理隔离（原则 1：禁止实盘按钮与安全按钮同色紧邻）
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            回测为安全主按钮；影子为谨慎琥珀次级样式 + 显式环境标签；实盘必须置于独立斜纹警示隔离区内并经确认词解锁。
          </p>
        </div>

        {/* Safe & Caution Zone */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-900/60 p-3">
          <div className="space-y-1">
            <div className="text-xs font-medium text-slate-200">
              投研与模拟验证操作区（不触碰真实资金）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <EnvBadge env="backtest" />
              <EnvBadge env="shadow" pulse />
              <ApprovalBadge status="APPROVED" />
              <ApprovalBadge status="PENDING" />
              <ApprovalBadge status="REJECTED" />
              <SeverityBadge severity="CRITICAL" />
              <SeverityBadge severity="HIGH" />
              <SeverityBadge severity="MEDIUM" />
              <SeverityBadge severity="LOW" />
              <RunStatusBadge status="RUNNING" />
              <RunStatusBadge status="COMPLETED" />
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <BacktestActionButton
              onClick={() => bootstrapRealRunMutation.mutate()}
              disabled={bootstrapRealRunMutation.isPending}
            >
              {bootstrapRealRunMutation.isPending ? "正在执行回测..." : "发起回测 (Safe)"}
            </BacktestActionButton>
            <ShadowActionButton onClick={() => setShadowConfirmOpen(true)}>
              发起影子交易
            </ShadowActionButton>
          </div>
        </div>

        {/* Physically Isolated Live Capital Danger Zone */}
        <IsolatedDangerZone
          title="实盘动钱操作隔离区 (LIVE CAPITAL ZONE · 物理分离)"
          subtitle="发起实盘必须通过：账户确认 → 事前风控预检（保证金/单笔上限/杠杆）→ 手动输入策略名解锁；右上角常驻 Kill Switch 需二次确认。"
        >
          <div className="flex items-center gap-2.5">
            <LiveDangerActionButton onClick={() => setLiveConfirmOpen(true)}>
              部署至实盘 (需输入确认词)
            </LiveDangerActionButton>
            <Button variant="killSwitch" onClick={() => setKillSwitchModalOpen(true)}>
              预览并触发 KILL SWITCH
            </Button>
          </div>
        </IsolatedDangerZone>
      </section>

      {/* Section 3: Principle 6 — All Three Mandatory States Visible Simultaneously */}
      <section className="space-y-2.5">
        <h2 className="text-sm font-semibold text-slate-200">
          3. 三种强制状态组件同屏验收（空状态 Empty · 阶段进度加载态 Loading · 带原因码与下一步的错误态 Error）
        </h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* State 1: Empty State */}
          <EmptyState
            title="当前策略尚无回测记录（空状态）"
            description="新创建的策略版本需先完成至少一次历史回测以生成净值归因、最大回撤与夏普指标。"
            primaryAction={{
              label: bootstrapRealRunMutation.isPending
                ? "正在调用后端运行..."
                : "立即运行真实后端回测",
              onClick: () => bootstrapRealRunMutation.mutate(),
            }}
            secondaryAction={{
              label: "查看交易对规格",
              variant: "outline",
              onClick: () => {
                document.getElementById("spec-table-section")?.scrollIntoView({ behavior: "smooth" });
              },
            }}
          />

          {/* State 2: Loading State with Stage Stepper, Progress %, ETA & Cancel */}
          <RunLoadingState
            title="回测执行中 · BTC/USDT:USDT (加载态)"
            currentStageLabel="Walk-the-Book 逐档撮合与资金费结算"
            progressPct={demoLoadingPct}
            processedBars={Math.round((demoLoadingPct / 100) * 1440)}
            totalBars={1440}
            elapsedMs={4800}
            estimatedRemainingMs={2900}
            stages={[
              { id: "s1", label: "1. AST与沙箱预检", status: "done" },
              { id: "s2", label: "2. 加载SymbolSpec", status: "done" },
              { id: "s3", label: "3. 事件撮合回放", status: "active" },
              { id: "s4", label: "4. 绩效归因持久化", status: "pending" },
            ]}
            onCancel={() => setDemoLoadingPct((p) => (p >= 95 ? 25 : p + 15))}
          />

          {/* State 3: Error State with Reason Code & Executable Next Steps */}
          <ActionableErrorState
            title="实盘启动被事前门禁拦截（错误态）"
            reasonCode="APPROVAL_REQUIRED"
            message="策略版本 strat_btc_sma:v2 当前状态为 PENDING，违反红线 8（未经人工审批严禁进入影子或实盘环境）。"
            remediation="由 OPERATOR 或 ADMIN 审阅版本参数 Diff 后点击人工审批通过，或先发起安全回测验证。"
            rawDetail={'{"status":400,"detail":"APPROVAL_REQUIRED: Version strat_btc_sma:v2 status is PENDING"}'}
            actions={[
              {
                label: "前往策略工作台审批",
                onClick: () => {
                  window.location.href = "/strategies";
                },
              },
              {
                label: "改为运行安全回测",
                variant: "outline",
                onClick: () => bootstrapRealRunMutation.mutate(),
              },
            ]}
          />
        </div>
      </section>

      {/* Section 4: Principle 4 — QuantDataTable bound to live /api/v1/specs */}
      <section id="spec-table-section" className="space-y-2.5">
        <h2 className="text-sm font-semibold text-slate-200">
          4. 核心资产数据表格 QuantDataTable（排序 · 列筛选 · 列显隐 · 三档密度 · 虚拟滚动 · URL状态同步 · 行展开）
        </h2>
        <QuantDataTable
          tableId="ds_specs"
          data={specsQuery.data ?? []}
          columns={specColumns}
          getRowId={(r) => `${r.exchange}_${r.symbol}`}
          renderExpandedRow={(spec) => (
            <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4 font-mono">
              <div>
                <span className="text-slate-400">基础资产 (Base): </span>
                <strong className="text-white">{spec.base_asset}</strong>
              </div>
              <div>
                <span className="text-slate-400">计价资产 (Quote): </span>
                <strong className="text-white">{spec.quote_asset}</strong>
              </div>
              <div>
                <span className="text-slate-400">保证金资产: </span>
                <strong className="text-white">{spec.margin_asset}</strong>
              </div>
              <div>
                <span className="text-slate-400">数量区间: </span>
                <strong className="text-white">
                  [{spec.min_qty}, {spec.max_qty}]
                </strong>
              </div>
            </div>
          )}
        />
      </section>

      {/* Section 5: Principle 5 — lightweight-charts K-Line + Markers + Equity/Drawdown + Table Sync */}
      <section className="space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-200">
            5. 专业金融图表 lightweight-charts（K线 · 买卖标记 · 成交量副图 · 净值/回撤联动 · CSV/截图导出 · 表格行点击定位）
          </h2>
          <Button
            size="sm"
            variant="secondary"
            disabled={bootstrapRealRunMutation.isPending}
            onClick={() => bootstrapRealRunMutation.mutate()}
          >
            <Sparkles className="h-3.5 w-3.5 text-sky-400" />
            <span>
              {bootstrapRealRunMutation.isPending
                ? "生成并回测中..."
                : "触发一次真实后端回测刷新图表数据"}
            </span>
          </Button>
        </div>

        {!latestBtRunId ? (
          <EmptyState
            title="数据库中暂无历史回测曲线数据"
            description="遵循「零前端 Mock 数据」约束，请点击下方按钮调用真实后端 NL 策略管线与回测引擎生成一条真实回测记录。"
            primaryAction={{
              label: bootstrapRealRunMutation.isPending
                ? "正在生成真实回测数据..."
                : "一键生成真实后端回测并渲染 K 线图",
              onClick: () => bootstrapRealRunMutation.mutate(),
            }}
          />
        ) : chartDataQuery.isLoading ? (
          <RunLoadingState
            title={`正在加载真实回测图表数据 (${latestBtRunId})`}
            currentStageLabel="读取 K 线序列、买卖成交标记与净值回撤曲线"
            progressPct={75}
          />
        ) : chartDataQuery.data ? (
          <div className="space-y-3">
            <FinancialChartContainer
              title={`回测复盘 (${chartDataQuery.data.run_id.slice(0, 18)}...)`}
              symbol={chartDataQuery.data.symbol}
              symbolSpec={chartDataQuery.data.symbol_spec}
              bars={chartDataQuery.data.bars}
              tradeMarkers={chartDataQuery.data.trade_markers}
              equitySeries={chartDataQuery.data.equity_series}
              focusedTimeSec={focusedTradeSec}
            />
            {chartDataQuery.data.trade_markers.length > 0 && (
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-2 text-xs font-semibold text-slate-300">
                  成交标记联动（点击任意一笔成交，上方 K 线自动定位至对应时间窗口）：
                </div>
                <div className="flex flex-wrap gap-2">
                  {chartDataQuery.data.trade_markers.map((tm) => (
                    <button
                      key={tm.fill_id}
                      type="button"
                      onClick={() => setFocusedTradeSec(tm.time_sec)}
                      className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1 font-mono text-[11px] text-slate-200 hover:border-blue-500"
                    >
                      <span
                        className={
                          tm.side === "BUY" ? "text-pnl-up font-bold" : "text-pnl-down font-bold"
                        }
                      >
                        {tm.side}
                      </span>{" "}
                      {tm.qty} @ {tm.price} ({tm.fill_id.slice(0, 10)})
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </section>

      {/* Confirmation Dialogs Demo */}
      <ConfirmActionDialog
        open={shadowConfirmOpen}
        onOpenChange={setShadowConfirmOpen}
        title="确认启动影子模拟交易 (SHADOW)"
        description="影子环境将连接实时行情盘口流，但使用本地模拟撮合引擎，与实盘交易 API Key 物理隔离。"
        dangerLevel="caution"
        confirmLabel="确认启动 SHADOW"
        onConfirm={() => {
          setShadowConfirmOpen(false);
        }}
      >
        <div className="rounded border border-amber-500/30 bg-amber-950/20 p-2.5 font-mono text-[11px] text-amber-200">
          排队比例 queue_ahead_ratio=0.50 · 模拟网络延迟 latency=10ms · 实盘密钥挂载=DISABLED
        </div>
      </ConfirmActionDialog>

      <ConfirmActionDialog
        open={liveConfirmOpen}
        onOpenChange={setLiveConfirmOpen}
        title="高危操作确认 · 部署策略至实盘 (LIVE)"
        description="实盘运行将通过 ExchangeBroker 发送真实委托单。请核对事前风控预检额度并输入策略 ID 解锁。"
        dangerLevel="danger"
        confirmPhrase="strat_btc_sma"
        confirmLabel="解锁并提交实盘部署"
        onConfirm={() => {
          setLiveConfirmOpen(false);
        }}
      >
        <div className="grid grid-cols-3 gap-2 rounded border border-slate-800 bg-slate-900 p-2.5 font-mono text-[11px]">
          <div>
            <div className="text-slate-400">预计初始保证金</div>
            <div className="font-bold text-white">3,333.33 USDT</div>
          </div>
          <div>
            <div className="text-slate-400">单笔名义上限</div>
            <div className="font-bold text-white">100,000.00 USDT</div>
          </div>
          <div>
            <div className="text-slate-400">当前设定杠杆</div>
            <div className="font-bold text-amber-300">3x (上限 10x)</div>
          </div>
        </div>
      </ConfirmActionDialog>
    </div>
  );
}

export default function DesignSystemPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 text-xs text-muted-foreground font-mono">
          加载设计系统组件陈列页...
        </div>
      }
    >
      <DesignSystemShowcaseInner />
    </Suspense>
  );
}
