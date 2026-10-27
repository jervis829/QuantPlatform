"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { Camera, Download } from "lucide-react";
import { formatCurrency, formatPercent, formatPriceBySpec } from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { DESIGN_TOKENS } from "../../lib/tokens";
import type {
  ChartBarPoint,
  ChartEquityPoint,
  ChartTradeMarker,
  SymbolSpecSummary,
} from "../../types/openapi.generated";
import { Button } from "../ui/button";

type RangePreset = "15M" | "30M" | "ALL";

export interface FinancialChartContainerProps {
  title: string;
  symbol: string;
  symbolSpec?: SymbolSpecSummary;
  bars: ChartBarPoint[];
  tradeMarkers?: ChartTradeMarker[];
  equitySeries?: ChartEquityPoint[];
  focusedTimeSec?: number | null;
  heightPx?: number;
  showEquitySubpane?: boolean;
}

export function FinancialChartContainer({
  title,
  symbol,
  symbolSpec,
  bars,
  tradeMarkers = [],
  equitySeries = [],
  focusedTimeSec,
  heightPx = 320,
  showEquitySubpane = true,
}: FinancialChartContainerProps) {
  const { pnlPolarity } = usePreferences();
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const eqContainerRef = useRef<HTMLDivElement | null>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const eqChartRef = useRef<IChartApi | null>(null);

  const [rangePreset, setRangePreset] = useState<RangePreset>("ALL");
  const [hoverLegend, setHoverLegend] = useState<{
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    equity?: number;
    drawdownPct?: number;
  } | null>(null);

  const palette = DESIGN_TOKENS.chartColors[pnlPolarity];
  const tickSize = symbolSpec?.tick_size ?? "0.1";

  const sortedBars = useMemo(
    () => [...bars].sort((a, b) => a.time_sec - b.time_sec),
    [bars]
  );

  const sortedEquity = useMemo(
    () => [...equitySeries].sort((a, b) => a.time_sec - b.time_sec),
    [equitySeries]
  );

  useEffect(() => {
    if (!mainContainerRef.current || sortedBars.length === 0) return;

    const mainChart = createChart(mainContainerRef.current, {
      width: mainContainerRef.current.clientWidth,
      height: heightPx,
      layout: {
        background: { type: ColorType.Solid, color: "#0b101d" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(30, 41, 59, 0.55)" },
        horzLines: { color: "rgba(30, 41, 59, 0.55)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "#1e293b",
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
    });
    mainChartRef.current = mainChart;

    const candleSeries = mainChart.addCandlestickSeries({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.upWick,
      wickDownColor: palette.downWick,
    });

    candleSeries.setData(
      sortedBars.map((b) => ({
        time: b.time_sec as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    // Volume histogram sub-overlay in bottom 22% of K-line pane
    const volumeSeries = mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });
    volumeSeries.setData(
      sortedBars.map((b) => ({
        time: b.time_sec as Time,
        value: b.volume,
        color: b.close >= b.open ? "rgba(28, 185, 120, 0.28)" : "rgba(244, 63, 94, 0.28)",
      }))
    );

    // Buy / Sell execution markers
    if (tradeMarkers.length > 0) {
      const markers: SeriesMarker<Time>[] = [...tradeMarkers]
        .sort((a, b) => a.time_sec - b.time_sec)
        .map((m) => {
          const isBuy = m.side.toUpperCase() === "BUY";
          return {
            time: m.time_sec as Time,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? palette.up : palette.down,
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${m.side.toUpperCase()} ${m.qty}`,
          };
        });
      candleSeries.setMarkers(markers);
    }

    const eqMap = new Map<number, ChartEquityPoint>();
    sortedEquity.forEach((pt) => eqMap.set(pt.time_sec, pt));
    const barMap = new Map<number, ChartBarPoint>();
    sortedBars.forEach((b) => barMap.set(b.time_sec, b));

    // Set initial legend to latest bar
    const lastBar = sortedBars[sortedBars.length - 1];
    const lastEq = sortedEquity[sortedEquity.length - 1];
    setHoverLegend({
      open: lastBar.open,
      high: lastBar.high,
      low: lastBar.low,
      close: lastBar.close,
      volume: lastBar.volume,
      equity: lastEq?.equity,
      drawdownPct: lastEq?.drawdown_pct,
    });

    mainChart.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const tSec = Number(param.time);
      const b = barMap.get(tSec);
      if (b) {
        const eq = eqMap.get(tSec);
        setHoverLegend({
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
          volume: b.volume,
          equity: eq?.equity,
          drawdownPct: eq?.drawdown_pct,
        });
      }
    });

    mainChart.timeScale().fitContent();

    // Optional synchronized Equity + Benchmark + Drawdown sub-chart
    let eqChart: IChartApi | null = null;
    if (showEquitySubpane && eqContainerRef.current && sortedEquity.length > 0) {
      eqChart = createChart(eqContainerRef.current, {
        width: eqContainerRef.current.clientWidth,
        height: 150,
        layout: {
          background: { type: ColorType.Solid, color: "#0b101d" },
          textColor: "#94a3b8",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(30, 41, 59, 0.45)" },
          horzLines: { color: "rgba(30, 41, 59, 0.45)" },
        },
        rightPriceScale: { borderColor: "#1e293b" },
        leftPriceScale: { visible: true, borderColor: "#1e293b" },
        timeScale: { borderColor: "#1e293b", timeVisible: true },
      });
      eqChartRef.current = eqChart;

      const eqLine = eqChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 2,
        title: "账户净值",
      });
      eqLine.setData(
        sortedEquity.map((pt) => ({ time: pt.time_sec as Time, value: pt.equity }))
      );

      const benchLine = eqChart.addLineSeries({
        color: "#64748b",
        lineWidth: 1,
        lineStyle: 2,
        title: "基准",
      });
      benchLine.setData(
        sortedEquity.map((pt) => ({
          time: pt.time_sec as Time,
          value: pt.benchmark_equity,
        }))
      );

      const ddArea = eqChart.addAreaSeries({
        priceScaleId: "left",
        lineColor: "#f43f5e",
        topColor: "rgba(244, 63, 94, 0.05)",
        bottomColor: "rgba(244, 63, 94, 0.32)",
        lineWidth: 1,
        title: "回撤%",
      });
      ddArea.setData(
        sortedEquity.map((pt) => ({
          time: pt.time_sec as Time,
          value: -Math.abs(pt.drawdown_pct),
        }))
      );
      eqChart.timeScale().fitContent();

      mainChart.timeScale().subscribeVisibleTimeRangeChange((range) => {
        if (range && eqChart) {
          eqChart.timeScale().setVisibleRange(range);
        }
      });
    }

    const handleResize = () => {
      if (mainContainerRef.current) {
        mainChart.applyOptions({ width: mainContainerRef.current.clientWidth });
      }
      if (eqChart && eqContainerRef.current) {
        eqChart.applyOptions({ width: eqContainerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      mainChart.remove();
      mainChartRef.current = null;
      if (eqChart) {
        eqChart.remove();
        eqChartRef.current = null;
      }
    };
  }, [heightPx, palette.down, palette.downWick, palette.up, palette.upWick, showEquitySubpane, sortedBars, sortedEquity, tradeMarkers]);

  // Apply range preset changes
  useEffect(() => {
    const chart = mainChartRef.current;
    if (!chart || sortedBars.length === 0) return;
    if (rangePreset === "ALL") {
      chart.timeScale().fitContent();
      return;
    }
    const lastSec = sortedBars[sortedBars.length - 1].time_sec;
    const spanSec = rangePreset === "15M" ? 15 * 60 : 30 * 60;
    chart.timeScale().setVisibleRange({
      from: (lastSec - spanSec) as Time,
      to: lastSec as Time,
    });
  }, [rangePreset, sortedBars]);

  // Table row click -> center visible chart window around focused timestamp
  useEffect(() => {
    const chart = mainChartRef.current;
    if (!chart || !focusedTimeSec || sortedBars.length === 0) return;
    chart.timeScale().setVisibleRange({
      from: (focusedTimeSec - 600) as Time,
      to: (focusedTimeSec + 600) as Time,
    });
  }, [focusedTimeSec, sortedBars.length]);

  const handleExportCsv = () => {
    const header = "time_sec,open,high,low,close,volume\n";
    const body = sortedBars
      .map((b) => `${b.time_sec},${b.open},${b.high},${b.low},${b.close},${b.volume}`)
      .join("\n");
    const blob = new Blob([header + body], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${symbol.replace(/[^a-zA-Z0-9]/g, "_")}_kline_series.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportPng = () => {
    const chart = mainChartRef.current;
    if (!chart) return;
    const canvas = chart.takeScreenshot();
    const url = canvas.toDataURL("image/png");
    const a = document.createElement("a");
    a.href = url;
    a.download = `${symbol.replace(/[^a-zA-Z0-9]/g, "_")}_chart.png`;
    a.click();
  };

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Header: Title, Crosshair Readout, Timeframe Switcher, Export */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3.5 py-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-xs font-semibold text-slate-100">
            {title} <span className="ml-1 font-mono text-sky-400">{symbol}</span>
          </div>
          {hoverLegend && (
            <div className="flex flex-wrap items-center gap-2.5 font-mono text-[11px] tabular-nums-strict text-slate-300">
              <span>O: {formatPriceBySpec(hoverLegend.open, tickSize)}</span>
              <span>H: {formatPriceBySpec(hoverLegend.high, tickSize)}</span>
              <span>L: {formatPriceBySpec(hoverLegend.low, tickSize)}</span>
              <span className="font-semibold text-white">
                C: {formatPriceBySpec(hoverLegend.close, tickSize)}
              </span>
              <span className="text-slate-400">Vol: {hoverLegend.volume.toFixed(1)}</span>
              {hoverLegend.equity !== undefined && (
                <span className="text-sky-300">
                  净值: {formatCurrency(hoverLegend.equity)}
                </span>
              )}
              {hoverLegend.drawdownPct !== undefined && (
                <span className="text-rose-300">
                  回撤: {formatPercent(-Math.abs(hoverLegend.drawdownPct))}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <div className="inline-flex rounded border border-border bg-slate-950 p-0.5">
            {(["15M", "30M", "ALL"] as RangePreset[]).map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setRangePreset(preset)}
                className={`rounded px-2 py-0.5 font-mono text-[10px] ${
                  rangePreset === preset
                    ? "bg-blue-600 text-white font-semibold"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {preset}
              </button>
            ))}
          </div>
          <Button type="button" variant="outline" size="sm" onClick={handleExportCsv}>
            <Download className="h-3.5 w-3.5" />
            <span>CSV</span>
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={handleExportPng}>
            <Camera className="h-3.5 w-3.5" />
            <span>截图</span>
          </Button>
        </div>
      </div>

      {/* Main K-line + Markers + Volume Pane */}
      <div ref={mainContainerRef} className="w-full" />

      {/* Synchronized Equity + Benchmark + Underwater Drawdown Pane */}
      {showEquitySubpane && sortedEquity.length > 0 && (
        <div className="border-t border-slate-800">
          <div className="bg-slate-950/80 px-3 py-1 text-[10px] font-mono text-slate-400">
            资金曲线 vs 基准 (右轴 USDT) · 水下回撤面积图 (左轴 %)
          </div>
          <div ref={eqContainerRef} className="w-full" />
        </div>
      )}
    </div>
  );
}
