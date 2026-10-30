"use client";

import React, { Suspense, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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

  const ordersQuery = useInfiniteQuery({
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
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      quantApi.queryOrdersMultiFilter(apiKey, {
        env: envFilter || undefined,
        strategy_id: strategyFilter || undefined,
        symbol: symbolFilter || undefined,
        status: statusFilter || undefined,
        run_id: runIdFilter || undefined,
        q: queryText || undefined,
        start_ts_ms: Number.isFinite(startTsMs) ? startTsMs : undefined,
        end_ts_ms: Number.isFinite(endTsMs) ? endTsMs : undefined,
        limit: 80,
        cursor: pageParam,
      }),
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.next_cursor ? lastPage.next_cursor : undefined,
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

  const mergedOrders = useMemo<LedgerOrderRow[]>(
    () => ordersQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [ordersQuery.data]
  );

  const specsMap = useMemo<Record<string, SymbolSpecSummary>>(() => {
    const out: Record<string, SymbolSpecSummary> = {};
    for (const p of ordersQuery.data?.pages ?? []) {
      Object.assign(out, p.symbol_specs);
    }
    return out;
  }, [ordersQuery.data]);

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
        header: "订单号",
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
    const items = mergedOrders;
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

  const firstPage = ordersQuery.data?.pages[0];
  const sum = firstPage?.summary;
  const totalMatchedCount = firstPage?.total ?? mergedOrders.length;
  const apiErr =
    ordersQuery.error instanceof QuantApiError ? ordersQuery.error : null;

  return (
    <div className="space-y-5 pb-12">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">订单与成交记录</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            集中查看历史回测、模拟盘与实盘的全部买卖委托与成交明细；点击任意订单左侧展开箭头可查看逐笔成交与触发原因。
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowAuxTables((v) => !v)}
          >
            <Database className="h-3.5 w-3.5 text-sky-400" />
            <span>{showAuxTables ? "收起持仓与资金明细" : "查看持仓 / 余额 / 资金费"}</span>
          </Button>
          <Button size="sm" variant="outline" onClick={handleExportOrdersCsv}>
            <Download className="h-3.5 w-3.5" />
            <span>导出 CSV</span>
          </Button>
        </div>
      </div>

      {/* Clean Filter Bar */}
      <section className="rounded-lg border border-border bg-card p-3.5 space-y-3">
        <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 lg:grid-cols-7 text-xs">
          <div className="space-y-1">
            <label className="text-slate-400">运行类型</label>
            <select
              value={envFilter}
              onChange={(e) => {
                setEnvFilter(e.target.value);
                syncFiltersToUrl({ env: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 text-white"
            >
              <option value="">全部类型</option>
              <option value="live">实盘交易</option>
              <option value="shadow">模拟盘</option>
              <option value="backtest">历史回测</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">选择策略</label>
            <select
              value={strategyFilter}
              onChange={(e) => {
                setStrategyFilter(e.target.value);
                syncFiltersToUrl({ strategyId: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 text-white"
            >
              <option value="">全部策略</option>
              {(strategiesQuery.data ?? []).map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">交易品种</label>
            <select
              value={symbolFilter}
              onChange={(e) => {
                setSymbolFilter(e.target.value);
                syncFiltersToUrl({ symbol: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            >
              <option value="">全部品种</option>
              {(specsQuery.data ?? []).map((sp) => (
                <option key={`${sp.exchange}_${sp.symbol}`} value={sp.symbol}>
                  {sp.symbol}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">订单状态</label>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                syncFiltersToUrl({ status: e.target.value });
              }}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 text-white"
            >
              <option value="">全部状态</option>
              <option value="FILLED">已成交</option>
              <option value="NEW">挂单中</option>
              <option value="PARTIALLY_FILLED">部分成交</option>
              <option value="CANCELED">已撤单</option>
              <option value="REJECTED">已拒单</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">开始日期</label>
            <input
              type="date"
              value={startDateStr}
              onChange={(e) => setStartDateStr(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">结束日期</label>
            <input
              type="date"
              value={endDateStr}
              onChange={(e) => setEndDateStr(e.target.value)}
              className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-400">搜索订单号</label>
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
                placeholder="输入单号搜索..."
                className="h-8 w-full rounded border border-input bg-slate-950 px-2 font-mono text-white"
              />
              <Button
                type="button"
                size="icon"
                variant="outline"
                onClick={handleResetFilters}
                title="清空筛选条件"
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
          label="订单总数"
          timeWindow="当前筛选范围"
          benchmarkLabel={`已成交 ${sum?.filled_count ?? 0} · 拒单 ${sum?.rejected_count ?? 0}`}
        >
          <span className="font-mono">{sum?.order_count ?? 0}</span>
        </KpiMetricCard>

        <KpiMetricCard
          label="总成交金额"
          timeWindow="累计成交额"
          benchmarkLabel="USDT 计价"
        >
          <NumericDisplay
            value={sum?.total_notional_usdt ?? "0"}
            mode="currency"
          />
        </KpiMetricCard>

        <KpiMetricCard
          label="累计手续费"
          timeWindow="交易成本"
          benchmarkLabel="实收手续费合计"
        >
          <span className="font-mono text-amber-300">
            {formatCurrency(sum?.total_fee_usdt ?? "0")}
          </span>
        </KpiMetricCard>

        <KpiMetricCard
          label="累计净盈亏"
          timeWindow="已平仓盈亏"
          benchmarkLabel="当前筛选订单合计"
        >
          <NumericDisplay
            value={sum?.realized_pnl_usdt ?? "0"}
            mode="currency"
            coloredBySign
            showPlusSign
          />
        </KpiMetricCard>
      </div>

      {/* Optional Auxiliary Unified Ledger Sub-Tables (Structured Table, Never Raw JSON!) */}
      {showAuxTables && (
        <section className="rounded-lg border border-sky-500/35 bg-slate-900/60 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-semibold text-white">
              账户持仓、余额与资金费快照查询
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={runIdFilter}
                onChange={(e) => setRunIdFilter(e.target.value)}
                placeholder="粘贴运行编号 (run_...) 查询..."
                className="h-7 w-60 rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white"
              />
              {(
                [
                  ["positions", "持仓记录"],
                  ["balance_snapshots", "账户余额"],
                  ["funding_payments", "资金费收支"],
                  ["equity_curve", "账户净值"],
                ] as const
              ).map(([tbl, lbl]) => (
                <button
                  key={tbl}
                  type="button"
                  onClick={() => setAuxTableName(tbl)}
                  className={`rounded px-2.5 py-1 text-[11px] ${
                    auxTableName === tbl
                      ? "bg-blue-600 text-white font-semibold"
                      : "bg-slate-950 text-slate-400 hover:text-white"
                  }`}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>
          {!runIdFilter ? (
            <div className="text-xs text-slate-400">
              请在右侧输入框填入某次运行的编号（可点击下方任意订单展开复制），查看对应的持仓、余额或资金费明细。
            </div>
          ) : (auxLedgerQuery.data?.items ?? []).length === 0 ? (
            <div className="rounded bg-slate-950 p-3 text-xs text-slate-400">
              该运行下暂无此类记录。
            </div>
          ) : (
            <div className="max-h-60 overflow-auto rounded border border-border bg-slate-950">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-900 text-[11px] text-slate-400">
                  <tr>
                    {Object.keys(
                      (auxLedgerQuery.data?.items?.[0] ?? {}) as Record<string, unknown>
                    ).map((colKey) => (
                      <th key={colKey} className="px-3 py-2 font-medium">
                        {colKey}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70 font-mono text-[11px] text-slate-200">
                  {(auxLedgerQuery.data?.items ?? []).map((item, idx) => {
                    const rec = item as Record<string, unknown>;
                    return (
                      <tr key={idx} className="hover:bg-slate-900/50">
                        {Object.values(rec).map((val, cIdx) => (
                          <td key={cIdx} className="px-3 py-1.5 whitespace-nowrap">
                            {val === null || val === undefined ? "—" : String(val)}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
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
      ) : mergedOrders.length === 0 ? (
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
          data={mergedOrders}
          columns={orderColumns}
          getRowId={(r) => `${r.env}_${r.run_id}_${r.client_order_id}`}
          disableClientPagination
          totalCountOverride={totalMatchedCount}
          hasNextPage={Boolean(ordersQuery.hasNextPage)}
          isFetchingNextPage={ordersQuery.isFetchingNextPage}
          onLoadMore={() => {
            if (ordersQuery.hasNextPage && !ordersQuery.isFetchingNextPage) {
              void ordersQuery.fetchNextPage();
            }
          }}
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
