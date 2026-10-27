"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  type ColumnDef,
  type ColumnFiltersState,
  type ExpandedState,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Rows3,
  Search,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { usePreferences } from "../../lib/preferences-context";
import { DESIGN_TOKENS, type TableDensity } from "../../lib/tokens";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

export interface QuantColumnMeta {
  align?: "left" | "right" | "center";
  mono?: boolean;
  filterPlaceholder?: string;
}

export interface QuantDataTableProps<TData> {
  tableId: string;
  data: TData[];
  columns: ColumnDef<TData, unknown>[];
  getRowId?: (originalRow: TData, index: number) => string;
  renderExpandedRow?: (row: TData) => React.ReactNode;
  onRowClick?: (row: TData) => void;
  enableVirtualScroll?: boolean;
  maxBodyHeightPx?: number;
  defaultPageSize?: number;
  syncToUrl?: boolean;
  emptyNode?: React.ReactNode;
  toolbarExtra?: React.ReactNode;
}

export function QuantDataTable<TData>({
  tableId,
  data,
  columns,
  getRowId,
  renderExpandedRow,
  onRowClick,
  enableVirtualScroll = true,
  maxBodyHeightPx = 440,
  defaultPageSize = 50,
  syncToUrl = true,
  emptyNode,
  toolbarExtra,
}: QuantDataTableProps<TData>) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { tableDensity, setTableDensity } = usePreferences();

  // Read initial state from URL searchParams if enabled
  const initialQuery = syncToUrl ? searchParams.get(`${tableId}_q`) ?? "" : "";
  const initialSortParam = syncToUrl ? searchParams.get(`${tableId}_sort`) ?? "" : "";
  const initialPage = syncToUrl ? Math.max(0, Number(searchParams.get(`${tableId}_p`) || "0")) : 0;

  const [globalFilter, setGlobalFilter] = useState<string>(initialQuery);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [showColMenu, setShowColMenu] = useState(false);
  const [showColumnFilterRow, setShowColumnFilterRow] = useState(false);
  const [sorting, setSorting] = useState<SortingState>(() => {
    if (!initialSortParam) return [];
    const [id, dir] = initialSortParam.split(":");
    return id ? [{ id, desc: dir === "desc" }] : [];
  });
  const [pagination, setPagination] = useState({
    pageIndex: initialPage,
    pageSize: defaultPageSize,
  });

  // Sync key state back to URL query string without triggering scroll jump
  useEffect(() => {
    if (!syncToUrl || typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    if (globalFilter) {
      sp.set(`${tableId}_q`, globalFilter);
    } else {
      sp.delete(`${tableId}_q`);
    }
    if (sorting.length > 0) {
      sp.set(`${tableId}_sort`, `${sorting[0].id}:${sorting[0].desc ? "desc" : "asc"}`);
    } else {
      sp.delete(`${tableId}_sort`);
    }
    if (pagination.pageIndex > 0) {
      sp.set(`${tableId}_p`, String(pagination.pageIndex));
    } else {
      sp.delete(`${tableId}_p`);
    }
    const nextQs = sp.toString();
    const currentQs = window.location.search.replace(/^\?/, "");
    if (nextQs !== currentQs) {
      router.replace(`${pathname}${nextQs ? `?${nextQs}` : ""}`, { scroll: false });
    }
  }, [globalFilter, sorting, pagination.pageIndex, pathname, router, syncToUrl, tableId]);

  const effectiveColumns = useMemo<ColumnDef<TData, unknown>[]>(() => {
    if (!renderExpandedRow) return columns;
    const expanderCol: ColumnDef<TData, unknown> = {
      id: "__expander",
      header: () => null,
      size: 32,
      enableHiding: false,
      cell: ({ row }) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            row.toggleExpanded();
          }}
          className="inline-flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-white"
          title={row.getIsExpanded() ? "收起关联明细" : "展开查看关联明细"}
        >
          {row.getIsExpanded() ? (
            <ChevronDown className="h-3.5 w-3.5 text-blue-400" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>
      ),
    };
    return [expanderCol, ...columns];
  }, [columns, renderExpandedRow]);

  const table = useReactTable({
    data,
    columns: effectiveColumns,
    state: {
      sorting,
      globalFilter,
      columnFilters,
      columnVisibility,
      expanded,
      pagination,
    },
    getRowId,
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onExpandedChange: setExpanded,
    onPaginationChange: setPagination,
    getRowCanExpand: () => Boolean(renderExpandedRow),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
  });

  const rows = table.getRowModel().rows;
  const scrollParentRef = useRef<HTMLDivElement | null>(null);
  const rowHeight = DESIGN_TOKENS.densityRowHeightPx[tableDensity];
  const cellPad = DESIGN_TOKENS.densityCellPadding[tableDensity];

  const useVirtual = enableVirtualScroll && rows.length > 25 && Object.keys(expanded).length === 0;
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const virtualItems = useVirtual ? rowVirtualizer.getVirtualItems() : null;
  const paddingTop =
    useVirtual && virtualItems && virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom =
    useVirtual && virtualItems && virtualItems.length > 0
      ? rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end
      : 0;

  return (
    <div className="rounded-lg border border-border bg-card">
      {/* Toolbar: Search, Column Filter Toggle, Column Visibility, Density Switcher */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
            <input
              type="text"
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
              placeholder="快速筛选当前表..."
              className="h-7 w-48 rounded border border-input bg-slate-950 pl-8 pr-2.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-blue-500 focus:outline-none sm:w-56"
            />
          </div>
          <Button
            type="button"
            variant={showColumnFilterRow ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowColumnFilterRow((v) => !v)}
          >
            按列筛选
          </Button>
          {toolbarExtra}
        </div>

        <div className="flex items-center gap-2">
          {/* Density Toggle */}
          <div className="inline-flex items-center rounded border border-border bg-slate-950 p-0.5">
            <Rows3 className="mx-1.5 h-3.5 w-3.5 text-slate-400" />
            {(
              [
                ["compact", "紧凑"],
                ["standard", "标准"],
                ["comfortable", "宽松"],
              ] as Array<[TableDensity, string]>
            ).map(([d, label]) => (
              <button
                key={d}
                type="button"
                onClick={() => setTableDensity(d)}
                className={cn(
                  "rounded px-2 py-0.5 text-[11px] transition-colors",
                  tableDensity === d
                    ? "bg-blue-600 text-white font-semibold"
                    : "text-slate-400 hover:text-slate-200"
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Column Visibility Dropdown */}
          <div className="relative">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowColMenu((v) => !v)}
            >
              <Columns3 className="h-3.5 w-3.5" />
              <span>列显隐</span>
            </Button>
            {showColMenu && (
              <div className="absolute right-0 top-8 z-30 w-48 rounded-md border border-border bg-popover p-2 shadow-xl">
                <div className="mb-1.5 border-b border-border pb-1 text-[11px] font-semibold text-slate-300">
                  显示 / 隐藏列
                </div>
                <div className="max-h-56 space-y-1 overflow-y-auto">
                  {table
                    .getAllLeafColumns()
                    .filter((col) => col.getCanHide())
                    .map((col) => (
                      <label
                        key={col.id}
                        className="flex items-center gap-2 rounded px-1.5 py-1 text-xs text-slate-200 hover:bg-slate-800/70 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={col.getIsVisible()}
                          onChange={col.getToggleVisibilityHandler()}
                          className="accent-blue-500"
                        />
                        <span className="truncate">{col.id}</span>
                      </label>
                    ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Table Scroll Container */}
      <div
        ref={scrollParentRef}
        style={{ maxHeight: `${maxBodyHeightPx}px` }}
        className="overflow-auto"
      >
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-border">
                {hg.headers.map((header) => {
                  const meta = (header.column.columnDef.meta || {}) as QuantColumnMeta;
                  const canSort = header.column.getCanSort();
                  const sortState = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                      className={cn(
                        DESIGN_TOKENS.typography.tableHeader,
                        "px-3 py-2 select-none whitespace-nowrap",
                        meta.align === "right" ? "text-right" : "text-left",
                        canSort && "cursor-pointer hover:text-slate-200"
                      )}
                    >
                      <span
                        className={cn(
                          "inline-flex items-center gap-1",
                          meta.align === "right" && "justify-end"
                        )}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          <span className="text-slate-500">
                            {sortState === "asc" ? (
                              <ArrowUp className="h-3 w-3 text-blue-400" />
                            ) : sortState === "desc" ? (
                              <ArrowDown className="h-3 w-3 text-blue-400" />
                            ) : (
                              <ArrowUpDown className="h-3 w-3 opacity-50" />
                            )}
                          </span>
                        )}
                      </span>
                    </th>
                  );
                })}
              </tr>
            ))}

            {showColumnFilterRow && (
              <tr className="border-b border-border bg-slate-950/90">
                {table.getFlatHeaders().map((header) => (
                  <th key={`${header.id}_filter`} className="px-2 py-1">
                    {header.column.getCanFilter() ? (
                      <input
                        type="text"
                        value={(header.column.getFilterValue() as string) ?? ""}
                        onChange={(e) => header.column.setFilterValue(e.target.value)}
                        placeholder="过滤..."
                        className="h-6 w-full rounded border border-slate-800 bg-slate-900 px-2 text-[11px] font-normal text-slate-200 focus:border-blue-500 focus:outline-none"
                      />
                    ) : null}
                  </th>
                ))}
              </tr>
            )}
          </thead>

          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={table.getVisibleLeafColumns().length} className="p-4">
                  {emptyNode ?? (
                    <div className="py-6 text-center text-xs text-muted-foreground">
                      当前筛选条件下暂无记录
                    </div>
                  )}
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 && (
                  <tr>
                    <td style={{ height: `${paddingTop}px` }} />
                  </tr>
                )}
                {(useVirtual && virtualItems
                  ? virtualItems.map((vi) => rows[vi.index])
                  : rows
                ).map((row) => (
                  <React.Fragment key={row.id}>
                    <tr
                      onClick={() => onRowClick?.(row.original)}
                      className={cn(
                        "border-b border-slate-800/70 transition-colors hover:bg-slate-800/45",
                        onRowClick && "cursor-pointer",
                        row.getIsExpanded() && "bg-slate-800/35"
                      )}
                    >
                      {row.getVisibleCells().map((cell) => {
                        const meta = (cell.column.columnDef.meta || {}) as QuantColumnMeta;
                        return (
                          <td
                            key={cell.id}
                            className={cn(
                              cellPad,
                              "whitespace-nowrap",
                              meta.align === "right" ? "text-right" : "text-left",
                              meta.mono && "font-mono tabular-nums-strict"
                            )}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        );
                      })}
                    </tr>
                    {row.getIsExpanded() && renderExpandedRow && (
                      <tr className="border-b border-slate-800 bg-slate-950/80">
                        <td
                          colSpan={row.getVisibleCells().length}
                          className="p-3"
                        >
                          {renderExpandedRow(row.original)}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
                {paddingBottom > 0 && (
                  <tr>
                    <td style={{ height: `${paddingBottom}px` }} />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>

      {/* Footer: Row Count & Pagination */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        <div className="font-mono tabular-nums-strict">
          共 <strong className="text-slate-200">{table.getFilteredRowModel().rows.length}</strong> 条记录
          {table.getPageCount() > 1 && (
            <span className="ml-2">
              · 第 {table.getState().pagination.pageIndex + 1} / {table.getPageCount()} 页
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <select
            value={table.getState().pagination.pageSize}
            onChange={(e) => table.setPageSize(Number(e.target.value))}
            className="h-6 rounded border border-border bg-slate-950 px-1.5 font-mono text-[11px] text-slate-200"
          >
            {[20, 50, 100, 250].map((sz) => (
              <option key={sz} value={sz}>
                {sz} 条/页
              </option>
            ))}
          </select>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-6 px-2"
            disabled={!table.getCanPreviousPage()}
            onClick={() => table.previousPage()}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-6 px-2"
            disabled={!table.getCanNextPage()}
            onClick={() => table.nextPage()}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
