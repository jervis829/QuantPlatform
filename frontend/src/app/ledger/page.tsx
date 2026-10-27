"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Database, Download, RotateCcw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { EnvBadge } from "../../components/quant/badges";
import { QuantDataTable } from "../../components/quant/data-table";
import { KpiMetricCard, NumericDisplay } from "../../components/quant/numeric-display";
import {
  ActionableErrorState,
  EmptyState,
  RunLoadingState,
} from "../../components/quant/state-views";
import { Button } from "../../components/ui/button";
import { QuantApiError, quantApi } from "../../lib/apiClient";
import {
  formatCurrency,
  formatPriceBySpec,
  formatQtyBySpec,
  formatUtcTimestamp,
} from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import type { LedgerOrderRow, SymbolSpecSummary } from "../../types/openapi.generated";

function OrderFillsSubpanel({
  order,
  spec,
}: {
  order: LedgerOrderRow;
  spec?: SymbolSpecSummary;
}) {
  const { apiKey } = usePreferences();
  const tickSize = spec?.tick_size ?? "0.1";
  const stepSize = spec?.step_size ?? "0.001";

  const drillQuery = useQuery({
    queryKey: [
      "order-fills-drilldown",
      order.env,
      order.run_id,
      order.client_order_id,
      apiKey,
    ],
    queryFn: () =>
      quantApi.getOrderFillsDrilldown(
        apiKey,
        order.env,
        order.run_id,
        order.client_order_id
      ),
  });

  if (drillQuery.isLoading) {
    return (
      <div className="py-2 font-mono text-xs text-slate-400">
        正在拉取订单 {order.client_order_id} 关联成交明细 (Fills) 与原始信号链路...
      </div>
    );
  }

  const fills = drillQuery.data?.fills ?? [];
  const traces = drillQuery.data?.signal_traces ?? [];

  return (
    <div className="space-y-3 rounded-md border border-slate-800 bg-slate-900/60 p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-2">
        <div className="font-mono text-[11px] text-slate-300">
          委托单 <strong className="text-white">{order.client_order_id}</strong> · 交易所回执号:{" "}
          <span className="text-sky-300">{order.exchange_order_id || "—"}</span> · Run:{" "}
          <span className="text-slate-400">{order.run_id}</span>
        </div>
        {order.reject_reason && (
          <span className="rounded bg-rose-500/20 px-2 py-0.5 font-mono text-[11px] text-rose-300">
            拒单原因: {order.reject_reason}
          </span>
        )}
      </div>

      {/* Associated Fills Table */}
      <div>
        <div className="mb-1.5 font-semibold text-slate-200">
          关联逐笔成交明细 Fills ({fills.length})
        </div>
        {fills.length === 0 ? (
          <div className="font-mono text-[11px] text-slate-500">
            该委托单暂无成交明细（可能已被撤销或拒单）。
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-slate-800">
            <table className="w-full text-left font-mono text-[11px] tabular-nums-strict">
              <thead className="bg-slate-950 text-slate-400">
                <tr>
                  <th className="px-2.5 py-1.5">fill_id</th>
                  <th className="px-2.5 py-1.5">流动性</th>
                  <th className="px-2.5 py-1.5 text-right">成交价</th>
                  <th className="px-2.5 py-1.5 text-right">成交量</th>
                  <th className="px-2.5 py-1.5 text-right">滑点 (bps)</th>
                  <th className="px-2.5 py-1.5 text-right">手续费</th>
                  <th className="px-2.5 py-1.5 text-right">已实现盈亏</th>
                  <th className="px-2.5 py-1.5">成交时间 (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {fills.map((f, idx) => (
                  <tr key={String(f.fill_id ?? idx)} className="border-t border-slate-800">
                    <td className="px-2.5 py-1.5 text-sky-300">{String(f.fill_id)}</td>
                    <td className="px-2.5 py-1.5 text-slate-300">{String(f.liquidity)}</td>
                    <td className="px-2.5 py-1.5 text-right text-white">
                      {formatPriceBySpec(String(f.price ?? "0"), tickSize)}
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-white">
                      {formatQtyBySpec(String(f.quantity ?? "0"), stepSize)}
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-slate-300">
                      {String(f.slippage_bps ?? "0")}
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-amber-300">
                      {String(f.fee ?? "0")} {String(f.fee_asset ?? "USDT")}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      <NumericDisplay
                        value={String(f.realized_pnl ?? "0")}
                        mode="currency"
                        coloredBySign
                        showPlusSign
                      />
                    </td>
                    <td className="px-2.5 py-1.5 text-slate-400">
                      {formatUtcTimestamp(Number(f.exchange_ts_ms ?? 0), true)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Associated Signal Traces */}
      {traces.length > 0 && (
        <div className="font-mono text-[11px] text-slate-400">
          触发信号溯源:{" "}
          {traces.map((t, idx) => (
            <span key={idx} className="mr-3 text-slate-300">
              signal_id={String(t.signal_id)} ({String(t.intent_type)})
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function UnifiedLedgerInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { apiKey } = usePreferences();

  const [envFilter, setEnvFilter] = useState(searchParams.get("env") ?? "");
  const [strategyFilter, setStrategyFilter] = useState(
    searchParams.get("strategyId") ?? ""
  );
  const [symbolFilter, setSymbolFilter] = useState(searchParams.get("symbol") ?? "");
  const [statusFilter, setStatusFilter] = useState(searchParams.get("status") ?? "");
  const [runIdFilter, setRunIdFilter] = useState(searchParams.get("runId") ?? "");
  const [queryText, setQueryText] = useState(searchParams.get("q") ?? "");
  const [startDateStr, setStartDateStr] = useState("");
  const [endDateStr, setEndDateStr] = useState("");

  // Auxiliary Unified Sub-Table Inspector (Positions / Balances / Equity / Funding)
  const [showAuxTables, setShowAuxTables] = useState(false);
  const [auxTableName, setAuxTableName] = useState("positions");

  const strategiesQuery = useQuery({
    queryKey: ["strategies", apiKey],
    queryFn: () => quantApi.listStrategies(apiKey),
  });

  const specsQuery = useQuery({
    queryKey: ["symbol-specs", apiKey],
    queryFn: () => quantApi.listSpecs(apiKey),
  });

  const startTsMs = startDateStr ? Date.parse(startDateStr) : undefined;
  const endTsMs = endDateStr ? Date.parse(endDateStr) + 86_399_999 : undefined;

  const ordersQuery = useQuery({
    queryKey: [
      "ledger-orders-multi",
      apiKey,
      envFilter,
      strategyFilter,
      symbolFilter,
      statusFilter,
      runIdFilter,
      queryText,
      startTsMs,
      endTsMs,
    ],
    queryFn: () =>
      quantApi.queryOrdersMultiFilter(apiKey, {
        env: envFilter || undefined,
        strategy_id: strategyFilter || undefined,
        symbol: symbolFilter || undefined,
        status: statusFilter || undefined,
        run_id: runIdFilter || undefined,
        q: queryText || undefined,
        start_ts_ms: Number.isFinite(startTsMs) ? startTsMs : undefined,
        end_ts_ms: Number.isFinite(endTsMs) ? endTsMs : undefined,
        limit: 500,
      }),
  });

  const auxLedgerQuery = useQuery({
    queryKey: ["aux-ledger", auxTableName, envFilter || "backtest", runIdFilter, apiKey],
    queryFn: () =>
      quantApi.queryLedger(
        apiKey,
        auxTableName,
        envFilter || "backtest",
        runIdFilter
      ),
    enabled: showAuxTables && Boolean(runIdFilter),
  });

  const syncFiltersToUrl = (overrides: Record<string, string>) => {
    const sp = new URLSearchParams(window.location.search);
    Object.entries(overrides).forEach(([k, v]) => {
      if (v) sp.set(k, v);
      else sp.delete(k);
    });
    const qs = sp.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  };

  const handleResetFilters = () => {
    setEnvFilter("");
    setStrategyFilter("");
    setSymbolFilter("");
    setStatusFilter("");
    setRunIdFilter("");
    setQueryText("");
    setStartDateStr("");
    setEndDateStr("");
    router.replace(pathname, { scroll: false });
  };

  const specsMap = ordersQuery.data?.symbol_specs ?? {};

  const orderColumns = useMemo<ColumnDef<LedgerOrderRow, unknown>[]>(
    () => [
      {
        id: "env",
        accessorKey: "env",
        header: "环境",
        cell: ({ row }) => <EnvBadge env={row.original.env} />,
      },
      {
        id: "created_ts",
        accessorKey: "created_ts_ms",
        header: "委托时间 (UTC)",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-xs text-slate-300">
            {formatUtcTimestamp(row.original.created_ts_ms, true)}
          </span>
        ),
      },
      {
        id: "strategy",
        accessorKey: "strategy_id",
        header: "策略 ID",
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-200">
            {row.original.strategy_id}
          </span>
        ),
      },
      {
        id: "symbol_side",
        accessorFn: (r) => `${r.symbol} ${r.side}`,
        header: "交易对 / 方向",
        cell: ({ row }) => {
          const r = row.original;
          const isBuy = r.side.toUpperCase() === "BUY";
          return (
            <div className="flex items-center gap-1.5 font-mono text-xs">
              <span className="font-semibold text-white">{r.symbol}</span>
              <span
                className={`rounded px-1.5 py-0.2 text-[10px] font-bold ${
                  isBuy
                    ? "bg-emerald-500/20 text-emerald-300"
                    : "bg-rose-500/20 text-rose-300"
                }`}
              >
                {r.side} · {r.order_type}
              </span>
            </div>
          );
        },
      },
      {
        id: "qty",
        accessorFn: (r) => Number(r.filled_qty),
        header: "已成交 / 委托量",
        meta: { align: "right", mono: true },
        cell: ({ row }) => {
          const r = row.original;
          const step = specsMap[r.symbol]?.step_size ?? "0.001";
          return (
            <span>
              <strong className="text-white">{formatQtyBySpec(r.filled_qty, step)}</strong> /{" "}
              <span className="text-slate-400">{formatQtyBySpec(r.quantity, step)}</span>
            </span>
          );
        },
      },
      {
        id: "avg_price",
        accessorFn: (r) => Number(r.avg_fill_price),
        header: "成交均价",
        meta: { align: "right", mono: true },
        cell: ({ row }) => {
          const r = row.original;
          const tick = specsMap[r.symbol]?.tick_size ?? "0.1";
          return (
            <span>
              {Number(r.avg_fill_price) > 0
                ? formatPriceBySpec(r.avg_fill_price, tick)
                : r.price
                  ? formatPriceBySpec(r.price, tick)
                  : "市价"}
            </span>
          );
        },
      },
      {
        id: "status",
        accessorKey: "status",
        header: "订单状态",
        cell: ({ row }) => {
          const st = row.original.status.toUpperCase();
          return (
            <span
              className={`rounded px-2 py-0.5 font-mono text-[10px] font-semibold ${
                st === "FILLED"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : st === "REJECTED"
                    ? "bg-rose-500/20 text-rose-300"
                    : "bg-slate-800 text-slate-300"
              }`}
            >
              {st} ({row.original.fill_count}笔)
            </span>
          );
        },
      },
      {
        id: "pnl",
        accessorFn: (r) => Number(r.realized_pnl_usdt),
        header: "已实现盈亏",
        meta: { align: "right", mono: true },
        cell: ({ row }) => (
          <NumericDisplay
            value={row.original.realized_pnl_usdt}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        ),
      },
      {
        id: "cid",
        accessorKey: "client_order_id",
        header: "client_order_id",
        meta: { mono: true },
        cell: ({ row }) => (
          <span className="text-[11px] text-slate-400">
            {row.original.client_order_id.slice(0, 18)}
          </span>
        ),
      },
    ],
    [specsMap]
  );

  const handleExportOrdersCsv = () => {
    const items = ordersQuery.data?.items ?? [];
    const hdr =
      "env,run_id,strategy_id,client_order_id,exchange_order_id,symbol,side,order_type,quantity,filled_qty,avg_fill_price,fee_total,status,realized_pnl_usdt,created_ts_ms\n";
    const body = items
      .map(
        (o) =>
          `${o.env},${o.run_id},${o.strategy_id},${o.client_order_id},${o.exchange_order_id ?? ""},${o.symbol},${o.side},${o.order_type},${o.quantity},${o.filled_qty},${o.avg_fill_price},${o.fee_total},${o.status},${o.realized_pnl_usdt},${o.created_ts_ms}`
      )
      .join("\n");
    const blob = new Blob([hdr + body], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "unified_ledger_orders.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const sum = ordersQuery.data?.summary;
  const apiErr =
    ordersQuery.error instanceof QuantApiError ? ordersQuery.error : null;

  return (
    <div className="space-y-5 pb-12">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">
            三环境统一交易账本 (/ledger · 红线 5)
          </h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            回测、影子与实盘共用同一套表结构；点击任意订单左侧展开箭头即可查看逐笔成交 Fills 与触发信号链路。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowAuxTables((v) => !v)}
          >
            <Database className="h-3.5 w-3.5 text-sky-400" />
            <span>{showAuxTables ? "收起持仓/资金费子表" : "查询持仓/余额/资金费子表"}</span>
          </Button>
          <Button size="sm" variant="outline" onClick={handleExportOrdersCsv}>
            <Download className="h-3.5 w-3.5" />
            <span>导出当前筛选 CSV</span>
          </Button>
        </div>
      </div>

      {/* Multi-Dimensional Combined Filter Bar (Env + Strategy + Symbol + Time + Status + ID) */}
      <section className="rounded-lg border border-border bg-card p-3.5 space-y-3">
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 lg:grid-cols-7 text-xs">
          <div className="space-y-1">
            <label className="text-slate-400">运行环境 (env)</label>
            <select
              value={envFilter}
              onChange={(e) => {
                setEnvFilter(e.target.value);
                syncFiltersToUrl({ env: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              <option value="">全部环境</option>
              <option value="live">LIVE (实盘)</option>
              <option value="shadow">SHADOW (影子)</option>
              <option value="backtest">BACKTEST (回测)</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">策略 (strategy_id)</label>
            <select
              value={strategyFilter}
              onChange={(e) => {
                setStrategyFilter(e.target.value);
                syncFiltersToUrl({ strategyId: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              <option value="">全部策略</option>
              {(strategiesQuery.data ?? []).map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.strategy_id}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">交易对 (symbol)</label>
            <select
              value={symbolFilter}
              onChange={(e) => {
                setSymbolFilter(e.target.value);
                syncFiltersToUrl({ symbol: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              <option value="">全部交易对</option>
              {(specsQuery.data ?? []).map((sp) => (
                <option key={`${sp.exchange}_${sp.symbol}`} value={sp.symbol}>
                  {sp.symbol}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">订单状态 (status)</label>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                syncFiltersToUrl({ status: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              <option value="">全部状态</option>
              <option value="FILLED">FILLED (完全成交)</option>
              <option value="NEW">NEW (挂单中)</option>
              <option value="PARTIALLY_FILLED">PARTIALLY_FILLED</option>
              <option value="CANCELED">CANCELED (已撤)</option>
              <option value="REJECTED">REJECTED (已拒单)</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">起始日期 (UTC)</label>
            <input
              type="date"
              value={startDateStr}
              onChange={(e) => setStartDateStr(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">截止日期 (UTC)</label>
            <input
              type="date"
              value={endDateStr}
              onChange={(e) => setEndDateStr(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">单号 / Run ID</label>
            <div className="flex gap-1">
              <input
                type="text"
                value={queryText || runIdFilter}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v.startsWith("run_")) {
                    setRunIdFilter(v);
                    setQueryText("");
                    syncFiltersToUrl({ runId: v, q: "" });
                  } else {
                    setQueryText(v);
                    setRunIdFilter("");
                    syncFiltersToUrl({ q: v, runId: "" });
                  }
                }}
                placeholder="client_order_id / run_id"
                className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
              />
              <Button
                type="button"
                size="icon"
                variant="outline"
                onClick={handleResetFilters}
                title="清空全部组合筛选"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* First-Glance Summary Strip */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiMetricCard
          label="筛选委托单总数"
          timeWindow="组合条件聚合"
          benchmarkLabel={`完全成交 ${sum?.filled_count ?? 0} · 拒单 ${sum?.rejected_count ?? 0}`}
        >
          <span className="font-mono">{sum?.order_count ?? 0}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="累计成交名义金额"
          timeWindow="Quote Notional"
          benchmarkLabel="严格 Decimal 累加"
        >
          <NumericDisplay
            value={sum?.total_notional_usdt ?? "0"}
            mode="currency"
          />
        </KpiMetricCard>

        <KpiMetricCard
          label="累计手续费支出"
          timeWindow="Maker + Taker"
          benchmarkLabel="账本实收实付"
        >
          <span className="font-mono text-amber-300">
            {formatCurrency(sum?.total_fee_usdt ?? "0")}
          </span>
        </KpiMetricCard>

        <KpiMetricCard
          label="累计已实现净盈亏"
          timeWindow="筛选订单集"
          benchmarkLabel="极性配色联动"
        >
          <NumericDisplay
            value={sum?.realized_pnl_usdt ?? "0"}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        </KpiMetricCard>
      </div>

      {/* Optional Auxiliary Unified Ledger Sub-Tables (Positions / Funding / Balances / Equity) */}
      {showAuxTables && (
        <section className="rounded-lg border border-sky-500/35 bg-slate-900/60 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-semibold text-white">
              统一账本其余子表直查（需输入上方或下方指定 Run ID）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={runIdFilter}
                onChange={(e) => setRunIdFilter(e.target.value)}
                placeholder="输入指定 run_id 查询持仓/资金费快照..."
                className="h-7 w-64 rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
              />
              {["positions", "funding_payments", "balance_snapshots", "equity_curve"].map(
                (tbl) => (
                  <button
                    key={tbl}
                    type="button"
                    onClick={() => setAuxTableName(tbl)}
                    className={`rounded px-2.5 py-1 font-mono text-[11px] ${
                      auxTableName === tbl
                        ? "bg-blue-600 text-white font-semibold"
                        : "bg-slate-950 text-slate-400 hover:text-white"
                    }`}
                  >
                    {tbl}
                  </button>
                )
              )}
            </div>
          </div>
          {!runIdFilter ? (
            <div className="text-xs text-slate-400">
              请输入任一 `run_id`（可从下方订单展开区复制）查看该运行的 `{auxTableName}` 统一表快照。
            </div>
          ) : (
            <pre className="max-h-56 overflow-auto rounded bg-slate-950 p-3 font-mono text-[11px] text-slate-200">
              {JSON.stringify(auxLedgerQuery.data?.items ?? [], null, 2)}
            </pre>
          )}
        </section>
      )}

      {/* Orders Table with Expandable Order -> Fills Linkage */}
      {ordersQuery.isLoading ? (
        <RunLoadingState
          title="正在查询三环境统一委托与成交账本..."
          currentStageLabel="按组合索引检索 orders 并聚合手续费与已实现盈亏"
          progressPct={65}
        />
      ) : ordersQuery.isError ? (
        <ActionableErrorState
          title="查询统一交易账本失败"
          reasonCode={apiErr?.reasonCode ?? "LEDGER_QUERY_ERROR"}
          message={apiErr?.message ?? String(ordersQuery.error)}
          remediation="请检查时间范围格式或重置筛选条件后重试。"
          actions={[{ label: "重置筛选并重试", onClick: handleResetFilters }]}
        />
      ) : (ordersQuery.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          title="当前组合筛选条件下暂无委托记录"
          description="您可以清空筛选条件，或前往策略工作台发起一次回测/影子运行以产生统一账本流水。"
          primaryAction={{
            label: "清空筛选条件",
            onClick: handleResetFilters,
          }}
          secondaryAction={{
            label: "前往策略工作台",
            variant: "outline",
            onClick: () => router.push("/strategies"),
          }}
        />
      ) : (
        <QuantDataTable
          tableId="ledger_orders"
          data={ordersQuery.data?.items ?? []}
          columns={orderColumns}
          getRowId={(r) => `${r.env}_${r.run_id}_${r.client_order_id}`}
          renderExpandedRow={(order) => (
            <OrderFillsSubpanel order={order} spec={specsMap[order.symbol]} />
          )}
        />
      )}
    </div>
  );
}

export default function UnifiedLedgerPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 font-mono text-xs text-muted-foreground">
          正在加载统一交易账本...
        </div>
      }
    >
      <UnifiedLedgerInner />
    </Suspense>
  );
}
