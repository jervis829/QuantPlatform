"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import {
  Camera,
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Download,
  Maximize2,
  Minimize2,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  formatCurrency,
  formatPercent,
  formatPriceBySpec,
  formatQtyBySpec,
  formatUtcTimestamp,
} from "../../lib/formatters";
import { usePreferences } from "../../lib/preferences-context";
import { DESIGN_TOKENS } from "../../lib/tokens";
import { cn } from "../../lib/utils";
import type {
  ChartBarPoint,
  ChartEquityPoint,
  ChartRoundTripPair,
  ChartTradeMarker,
  SymbolSpecSummary,
} from "../../types/openapi.generated";
import { NumericDisplay } from "./numeric-display";
import { Button } from "../ui/button";

type TimeframeOption = "1m" | "5m" | "15m" | "1h";
type RangePreset = "30B" | "60B" | "ALL";
type MarkerFilter = "ALL" | "BUY" | "SELL" | "NONE";

const TF_SECONDS: Record<TimeframeOption, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
};

function computeSma(bars: ChartBarPoint[], period: number): Array<{ time: Time; value: number }> {
  const out: Array<{ time: Time; value: number }> = [];
  let rolling = 0;
  for (let i = 0; i < bars.length; i++) {
    rolling += bars[i].close;
    if (i >= period) {
      rolling -= bars[i - period].close;
    }
    if (i >= period - 1) {
      out.push({ time: bars[i].time_sec as Time, value: rolling / period });
    }
  }
  return out;
}

function computeEma(bars: ChartBarPoint[], period: number): Array<{ time: Time; value: number }> {
  if (bars.length === 0) return [];
  const k = 2 / (period + 1);
  const out: Array<{ time: Time; value: number }> = [];
  let ema = bars[0].close;
  for (let i = 0; i < bars.length; i++) {
    ema = i === 0 ? bars[0].close : bars[i].close * k + ema * (1 - k);
    if (i >= Math.min(period - 1, 2)) {
      out.push({ time: bars[i].time_sec as Time, value: ema });
    }
  }
  return out;
}

function computeBollinger(
  bars: ChartBarPoint[],
  period = 20,
  mult = 2
): {
  upper: Array<{ time: Time; value: number }>;
  middle: Array<{ time: Time; value: number }>;
  lower: Array<{ time: Time; value: number }>;
} {
  const upper: Array<{ time: Time; value: number }> = [];
  const middle: Array<{ time: Time; value: number }> = [];
  const lower: Array<{ time: Time; value: number }> = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
    const mean = sum / period;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = bars[j].close - mean;
      sq += d * d;
    }
    const std = Math.sqrt(sq / period);
    const t = bars[i].time_sec as Time;
    middle.push({ time: t, value: mean });
    upper.push({ time: t, value: mean + mult * std });
    lower.push({ time: t, value: mean - mult * std });
  }
  return { upper, middle, lower };
}

function aggregateBarsByTimeframe(
  sortedBars: ChartBarPoint[],
  tfSec: number
): ChartBarPoint[] {
  if (tfSec <= 60 || sortedBars.length === 0) return sortedBars;
  const buckets = new Map<number, ChartBarPoint>();
  for (const b of sortedBars) {
    const bucketSec = Math.floor(b.time_sec / tfSec) * tfSec;
    const existing = buckets.get(bucketSec);
    if (!existing) {
      buckets.set(bucketSec, {
        time_sec: bucketSec,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
      });
    } else {
      existing.high = Math.max(existing.high, b.high);
      existing.low = Math.min(existing.low, b.low);
      existing.close = b.close;
      existing.volume += b.volume;
    }
  }
  return Array.from(buckets.values()).sort((a, b) => a.time_sec - b.time_sec);
}

export interface IndexedTradeMarker extends ChartTradeMarker {
  seqIndex: number;
  bucketTimeSec: number;
}

export interface FinancialChartContainerProps {
  title: string;
  symbol: string;
  symbolSpec?: SymbolSpecSummary;
  bars: ChartBarPoint[];
  tradeMarkers?: ChartTradeMarker[];
  roundTrips?: ChartRoundTripPair[];
  equitySeries?: ChartEquityPoint[];
  focusedTimeSec?: number | null;
  heightPx?: number;
  showEquitySubpane?: boolean;
  onSelectMarker?: (marker: ChartTradeMarker) => void;
}

export function FinancialChartContainer({
  title,
  symbol,
  symbolSpec,
  bars,
  tradeMarkers = [],
  roundTrips = [],
  equitySeries = [],
  focusedTimeSec,
  heightPx = 420,
  showEquitySubpane = true,
  onSelectMarker,
}: FinancialChartContainerProps) {
  const { pnlPolarity } = usePreferences();
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const eqContainerRef = useRef<HTMLDivElement | null>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const activePriceLineRef = useRef<IPriceLine | null>(null);
  const eqChartRef = useRef<IChartApi | null>(null);

  const [timeframe, setTimeframe] = useState<TimeframeOption>("1m");
  const [rangePreset, setRangePreset] = useState<RangePreset>("ALL");
  const [markerFilter, setMarkerFilter] = useState<MarkerFilter>("ALL");
  const [showMa, setShowMa] = useState(true);
  const [showEma, setShowEma] = useState(false);
  const [showBoll, setShowBoll] = useState(false);
  const [showTradePaths, setShowTradePaths] = useState(true);
  const [showMoreTools, setShowMoreTools] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [selectedMarkerIdx, setSelectedMarkerIdx] = useState<number | null>(null);
  const [hoveredBucketFills, setHoveredBucketFills] = useState<IndexedTradeMarker[]>([]);

  const [hoverLegend, setHoverLegend] = useState<{
    timeSec: number;
    open: number;
    high: number;
    low: number;
    close: number;
    changePct: number;
    volume: number;
    ma5?: number;
    ma10?: number;
    ma20?: number;
    ema20?: number;
    equity?: number;
    drawdownPct?: number;
  } | null>(null);

  const palette = DESIGN_TOKENS.chartColors[pnlPolarity];
  const tickSize = symbolSpec?.tick_size ?? "0.1";
  const stepSize = symbolSpec?.step_size ?? "0.001";
  const tfSec = TF_SECONDS[timeframe];
  const effectiveHeight = isFullscreen ? 580 : heightPx;

  const sortedRawBars = useMemo(
    () => [...bars].sort((a, b) => a.time_sec - b.time_sec),
    [bars]
  );

  const aggBars = useMemo(
    () => aggregateBarsByTimeframe(sortedRawBars, tfSec),
    [sortedRawBars, tfSec]
  );

  const sortedEquity = useMemo(
    () => [...equitySeries].sort((a, b) => a.time_sec - b.time_sec),
    [equitySeries]
  );

  const indexedMarkers = useMemo<IndexedTradeMarker[]>(() => {
    const ordered = [...tradeMarkers].sort(
      (a, b) => (a.ts_ms || a.time_sec * 1000) - (b.ts_ms || b.time_sec * 1000)
    );
    return ordered.map((m, idx) => ({
      ...m,
      seqIndex: idx + 1,
      bucketTimeSec: Math.floor(m.time_sec / tfSec) * tfSec,
    }));
  }, [tradeMarkers, tfSec]);

  const filteredMarkers = useMemo(() => {
    if (markerFilter === "NONE") return [];
    if (markerFilter === "BUY") {
      return indexedMarkers.filter((m) => m.side.toUpperCase() === "BUY");
    }
    if (markerFilter === "SELL") {
      return indexedMarkers.filter((m) => m.side.toUpperCase() === "SELL");
    }
    return indexedMarkers;
  }, [indexedMarkers, markerFilter]);

  const buyCount = useMemo(
    () => indexedMarkers.filter((m) => m.side.toUpperCase() === "BUY").length,
    [indexedMarkers]
  );
  const sellCount = useMemo(
    () => indexedMarkers.filter((m) => m.side.toUpperCase() === "SELL").length,
    [indexedMarkers]
  );

  const selectedMarker =
    selectedMarkerIdx !== null && indexedMarkers[selectedMarkerIdx]
      ? indexedMarkers[selectedMarkerIdx]
      : null;

  useEffect(() => {
    if (!mainContainerRef.current || aggBars.length === 0) return;

    const mainChart = createChart(mainContainerRef.current, {
      width: mainContainerRef.current.clientWidth,
      height: effectiveHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#080d18" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(30, 41, 59, 0.45)" },
        horzLines: { color: "rgba(30, 41, 59, 0.45)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "rgba(56, 189, 248, 0.45)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "#0f172a",
        },
        horzLine: {
          color: "rgba(56, 189, 248, 0.45)",
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: "#0f172a",
        },
      },
      rightPriceScale: {
        borderColor: "#1e293b",
        scaleMargins: { top: 0.08, bottom: 0.24 },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        barSpacing: timeframe === "1m" ? 8 : timeframe === "5m" ? 14 : 20,
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
    candleSeriesRef.current = candleSeries;

    candleSeries.setData(
      aggBars.map((b) => ({
        time: b.time_sec as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    // Technical Indicator Overlays
    const ma5Data = computeSma(aggBars, 5);
    const ma10Data = computeSma(aggBars, 10);
    const ma20Data = computeSma(aggBars, 20);
    const ema20Data = computeEma(aggBars, 20);

    const ma5Map = new Map<number, number>(ma5Data.map((d) => [Number(d.time), d.value]));
    const ma10Map = new Map<number, number>(ma10Data.map((d) => [Number(d.time), d.value]));
    const ma20Map = new Map<number, number>(ma20Data.map((d) => [Number(d.time), d.value]));
    const ema20Map = new Map<number, number>(ema20Data.map((d) => [Number(d.time), d.value]));

    if (showMa) {
      const s5 = mainChart.addLineSeries({
        color: "#f59e0b",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s5.setData(ma5Data);

      const s10 = mainChart.addLineSeries({
        color: "#a78bfa",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s10.setData(ma10Data);

      const s20 = mainChart.addLineSeries({
        color: "#38bdf8",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s20.setData(ma20Data);
    }

    if (showEma) {
      const sEma = mainChart.addLineSeries({
        color: "#f472b6",
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      sEma.setData(ema20Data);
    }

    if (showBoll && aggBars.length >= 20) {
      const boll = computeBollinger(aggBars, 20, 2);
      const bUp = mainChart.addLineSeries({
        color: "rgba(129, 140, 248, 0.7)",
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      bUp.setData(boll.upper);

      const bMid = mainChart.addLineSeries({
        color: "rgba(129, 140, 248, 0.45)",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      bMid.setData(boll.middle);

      const bLow = mainChart.addLineSeries({
        color: "rgba(129, 140, 248, 0.7)",
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      bLow.setData(boll.lower);
    }

    // Round-trip Entry -> Exit holding trajectory lines on K-line
    if (showTradePaths && roundTrips.length > 0) {
      const validBarSecs = new Set(aggBars.map((b) => b.time_sec));
      for (const rt of roundTrips) {
        const entryBucket = Math.floor(rt.entry_time_sec / tfSec) * tfSec;
        const exitBucket = Math.floor(rt.exit_time_sec / tfSec) * tfSec;
        if (
          entryBucket < exitBucket &&
          validBarSecs.has(entryBucket) &&
          validBarSecs.has(exitBucket)
        ) {
          const isWin = rt.net_pnl >= 0;
          const pathColor = isWin ? palette.up : palette.down;
          const rtLine = mainChart.addLineSeries({
            color: pathColor,
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          rtLine.setData([
            { time: entryBucket as Time, value: rt.entry_price },
            { time: exitBucket as Time, value: rt.exit_price },
          ]);
        }
      }
    }

    // Volume histogram sub-overlay in bottom 20% of K-line pane
    const volumeSeries = mainChart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.81, bottom: 0 },
    });
    volumeSeries.setData(
      aggBars.map((b) => ({
        time: b.time_sec as Time,
        value: b.volume,
        color:
          b.close >= b.open
            ? pnlPolarity === "green-up"
              ? "rgba(16, 185, 129, 0.30)"
              : "rgba(239, 68, 68, 0.30)"
            : pnlPolarity === "green-up"
              ? "rgba(244, 63, 94, 0.30)"
              : "rgba(16, 185, 129, 0.30)",
      }))
    );

    // High-visibility Buy / Sell execution arrows on K-line
    const fillsByBucket = new Map<number, IndexedTradeMarker[]>();
    for (const m of filteredMarkers) {
      const arr = fillsByBucket.get(m.bucketTimeSec) ?? [];
      arr.push(m);
      fillsByBucket.set(m.bucketTimeSec, arr);
    }

    if (filteredMarkers.length > 0) {
      const seriesMarkers: SeriesMarker<Time>[] = [];
      const sortedBuckets = Array.from(fillsByBucket.entries()).sort((a, b) => a[0] - b[0]);
      for (const [bSec, bucketFills] of sortedBuckets) {
        for (const m of bucketFills) {
          const isBuy = m.side.toUpperCase() === "BUY";
          const pnlNum = Number(m.realized_pnl || 0);
          const pnlTag =
            Math.abs(pnlNum) > 1e-8
              ? ` (${pnlNum > 0 ? "+" : ""}${pnlNum.toFixed(1)}U)`
              : "";
          const shortAction = m.action_label
            ? m.action_label.split(" ")[0]
            : isBuy
              ? "买入"
              : "卖出";
          const buyColor = pnlPolarity === "red-up" ? "#fb7185" : "#34d399";
          const sellColor = pnlPolarity === "red-up" ? "#34d399" : "#fb7185";
          seriesMarkers.push({
            time: bSec as Time,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? buyColor : sellColor,
            shape: isBuy ? "arrowUp" : "arrowDown",
            size: 1.4,
            text: `#${m.seqIndex} ${shortAction} ${formatQtyBySpec(
              m.qty,
              stepSize
            )} @${formatPriceBySpec(m.price, tickSize)}${pnlTag}`,
          });
        }
      }
      candleSeries.setMarkers(seriesMarkers);
    }

    const eqMap = new Map<number, ChartEquityPoint>();
    for (const pt of sortedEquity) {
      const bucketSec = Math.floor(pt.time_sec / tfSec) * tfSec;
      eqMap.set(bucketSec, pt);
      eqMap.set(pt.time_sec, pt);
    }
    const barMap = new Map<number, ChartBarPoint>();
    aggBars.forEach((b) => barMap.set(b.time_sec, b));

    // Initialize legend to latest candle
    const lastBar = aggBars[aggBars.length - 1];
    const lastEq = sortedEquity[sortedEquity.length - 1];
    setHoverLegend({
      timeSec: lastBar.time_sec,
      open: lastBar.open,
      high: lastBar.high,
      low: lastBar.low,
      close: lastBar.close,
      changePct:
        lastBar.open !== 0 ? ((lastBar.close - lastBar.open) / lastBar.open) * 100 : 0,
      volume: lastBar.volume,
      ma5: ma5Map.get(lastBar.time_sec),
      ma10: ma10Map.get(lastBar.time_sec),
      ma20: ma20Map.get(lastBar.time_sec),
      ema20: ema20Map.get(lastBar.time_sec),
      equity: lastEq?.equity,
      drawdownPct: lastEq?.drawdown_pct,
    });

    mainChart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setHoveredBucketFills([]);
        return;
      }
      const tSec = Number(param.time);
      const b = barMap.get(tSec);
      if (b) {
        const eq = eqMap.get(tSec);
        setHoverLegend({
          timeSec: b.time_sec,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
          changePct: b.open !== 0 ? ((b.close - b.open) / b.open) * 100 : 0,
          volume: b.volume,
          ma5: ma5Map.get(tSec),
          ma10: ma10Map.get(tSec),
          ma20: ma20Map.get(tSec),
          ema20: ema20Map.get(tSec),
          equity: eq?.equity,
          drawdownPct: eq?.drawdown_pct,
        });
      }
      const bucketFills = fillsByBucket.get(tSec) ?? [];
      setHoveredBucketFills(bucketFills);
    });

    mainChart.subscribeClick((param) => {
      if (!param.time) return;
      const tSec = Number(param.time);
      const bucketFills = fillsByBucket.get(tSec);
      if (bucketFills && bucketFills.length > 0) {
        const target = bucketFills[0];
        setSelectedMarkerIdx(target.seqIndex - 1);
        onSelectMarker?.(target);
      }
    });

    mainChart.timeScale().fitContent();

    // Synchronized Equity + Benchmark + Underwater Drawdown Sub-pane
    let eqChart: IChartApi | null = null;
    if (showEquitySubpane && eqContainerRef.current && sortedEquity.length > 0) {
      eqChart = createChart(eqContainerRef.current, {
        width: eqContainerRef.current.clientWidth,
        height: 155,
        layout: {
          background: { type: ColorType.Solid, color: "#080d18" },
          textColor: "#94a3b8",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(30, 41, 59, 0.40)" },
          horzLines: { color: "rgba(30, 41, 59, 0.40)" },
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
        lineStyle: LineStyle.Dashed,
        title: "基准买入持有",
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
        topColor: "rgba(244, 63, 94, 0.04)",
        bottomColor: "rgba(244, 63, 94, 0.34)",
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
          try {
            eqChart.timeScale().setVisibleRange(range);
          } catch {
            // ignore transient range sync when subpane range is narrower
          }
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
      activePriceLineRef.current = null;
      candleSeriesRef.current = null;
      mainChart.remove();
      mainChartRef.current = null;
      if (eqChart) {
        eqChart.remove();
        eqChartRef.current = null;
      }
    };
  }, [
    aggBars,
    effectiveHeight,
    filteredMarkers,
    onSelectMarker,
    palette.down,
    palette.downWick,
    palette.up,
    palette.upWick,
    pnlPolarity,
    roundTrips,
    showBoll,
    showEma,
    showEquitySubpane,
    showMa,
    showTradePaths,
    sortedEquity,
    stepSize,
    tfSec,
    tickSize,
    timeframe,
  ]);

  // Apply range preset changes
  useEffect(() => {
    const chart = mainChartRef.current;
    if (!chart || aggBars.length === 0) return;
    if (rangePreset === "ALL") {
      chart.timeScale().fitContent();
      return;
    }
    const count = rangePreset === "30B" ? 30 : 60;
    const toIdx = aggBars.length - 1;
    const fromIdx = Math.max(0, toIdx - count);
    chart.timeScale().setVisibleRange({
      from: aggBars[fromIdx].time_sec as Time,
      to: aggBars[toIdx].time_sec as Time,
    });
  }, [rangePreset, aggBars]);

  // Focus chart on external table row click (`focusedTimeSec`)
  useEffect(() => {
    const chart = mainChartRef.current;
    if (!chart || !focusedTimeSec || aggBars.length === 0) return;
    const halfSpan = tfSec * 18;
    const snapped = Math.floor(focusedTimeSec / tfSec) * tfSec;
    chart.timeScale().setVisibleRange({
      from: (snapped - halfSpan) as Time,
      to: (snapped + halfSpan) as Time,
    });
    const matchIdx = indexedMarkers.findIndex(
      (m) => Math.abs(m.time_sec - focusedTimeSec) <= tfSec
    );
    if (matchIdx >= 0) {
      setSelectedMarkerIdx(matchIdx);
    }
  }, [focusedTimeSec, aggBars.length, indexedMarkers, tfSec]);

  // Highlight selected buy/sell execution price line + center window
  useEffect(() => {
    const candle = candleSeriesRef.current;
    const chart = mainChartRef.current;
    if (!candle || !chart) return;
    if (activePriceLineRef.current) {
      try {
        candle.removePriceLine(activePriceLineRef.current);
      } catch {
        // ignore stale reference
      }
      activePriceLineRef.current = null;
    }
    if (!selectedMarker) return;
    const isBuy = selectedMarker.side.toUpperCase() === "BUY";
    const px = Number(selectedMarker.price);
    if (Number.isFinite(px) && px > 0) {
      activePriceLineRef.current = candle.createPriceLine({
        price: px,
        color: isBuy ? "#38bdf8" : "#f59e0b",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `#${selectedMarker.seqIndex} ${
          selectedMarker.action_label || selectedMarker.side
        }`,
      });
    }
    const halfSpan = tfSec * 16;
    chart.timeScale().setVisibleRange({
      from: (selectedMarker.bucketTimeSec - halfSpan) as Time,
      to: (selectedMarker.bucketTimeSec + halfSpan) as Time,
    });
  }, [selectedMarker, tfSec]);

  const handleZoom = (factor: number) => {
    const chart = mainChartRef.current;
    if (!chart) return;
    const logical = chart.timeScale().getVisibleLogicalRange();
    if (!logical) return;
    const center = (logical.from + logical.to) / 2;
    const nextHalf = Math.max(5, ((logical.to - logical.from) / 2) * factor);
    chart.timeScale().setVisibleLogicalRange({
      from: center - nextHalf,
      to: center + nextHalf,
    });
  };

  const stepMarker = (delta: -1 | 1) => {
    if (indexedMarkers.length === 0) return;
    setSelectedMarkerIdx((prev) => {
      if (prev === null) return delta > 0 ? 0 : indexedMarkers.length - 1;
      const next = (prev + delta + indexedMarkers.length) % indexedMarkers.length;
      return next;
    });
  };

  const handleExportCsv = () => {
    const header = "time_sec,open,high,low,close,volume\n";
    const body = aggBars
      .map((b) => `${b.time_sec},${b.open},${b.high},${b.low},${b.close},${b.volume}`)
      .join("\n");
    const blob = new Blob([header + body], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${symbol.replace(/[^a-zA-Z0-9]/g, "_")}_${timeframe}_kline.csv`;
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
    a.download = `${symbol.replace(/[^a-zA-Z0-9]/g, "_")}_${timeframe}_chart.png`;
    a.click();
  };

  const activeInspectorFills =
    hoveredBucketFills.length > 0
      ? hoveredBucketFills
      : selectedMarker
        ? [selectedMarker]
        : [];

  return (
    <div
      ref={wrapperRef}
      className={cn(
        "rounded-lg border border-border bg-card overflow-hidden transition-all",
        isFullscreen && "fixed inset-3 z-50 flex flex-col bg-slate-950 shadow-2xl overflow-y-auto"
      )}
    >
      {/* Row 1: Primary Workstation Toolbar — Symbol, Timeframes, Indicator Overlays, Buy/Sell Filter & Stepper, Zoom/Export */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-slate-950/90 px-3.5 py-2">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-100">
            <span>{title}</span>
            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 font-mono text-[11px] text-sky-300">
              {symbol}
            </span>
          </div>

          {/* Timeframe Switcher */}
          <div className="inline-flex items-center rounded border border-slate-800 bg-slate-900 p-0.5">
            {(["1m", "5m", "15m", "1h"] as TimeframeOption[]).map((tf) => (
              <button
                key={tf}
                type="button"
                onClick={() => setTimeframe(tf)}
                className={cn(
                  "rounded px-2 py-0.5 font-mono text-[11px] transition-colors",
                  timeframe === tf
                    ? "bg-blue-600 text-white font-semibold"
                    : "text-slate-400 hover:text-white"
                )}
              >
                {tf}
              </button>
            ))}
          </div>

          {/* Primary Indicator Toggles */}
          <div className="inline-flex items-center gap-1 rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[11px]">
            <button
              type="button"
              onClick={() => setShowMa((v) => !v)}
              className={cn(
                "rounded px-1.5 py-0.5 transition-colors",
                showMa ? "bg-amber-500/20 text-amber-300 font-semibold" : "text-slate-400 hover:text-slate-200"
              )}
            >
              均线 MA
            </button>
            <button
              type="button"
              onClick={() => setShowBoll((v) => !v)}
              className={cn(
                "rounded px-1.5 py-0.5 transition-colors",
                showBoll ? "bg-indigo-500/20 text-indigo-300 font-semibold" : "text-slate-400 hover:text-slate-200"
              )}
            >
              布林带
            </button>
            {roundTrips.length > 0 && (
              <button
                type="button"
                onClick={() => setShowTradePaths((v) => !v)}
                className={cn(
                  "rounded px-1.5 py-0.5 transition-colors",
                  showTradePaths
                    ? "bg-emerald-500/20 text-emerald-300 font-semibold"
                    : "text-slate-400 hover:text-slate-200"
                )}
                title="在 K 线上连接每笔买入开仓点与卖出平仓点"
              >
                买卖连线
              </button>
            )}
          </div>

          {/* Buy / Sell Marker Filter */}
          <div className="inline-flex items-center rounded border border-slate-800 bg-slate-900 p-0.5 text-[11px]">
            {(
              [
                ["ALL", `全部买卖点 (${indexedMarkers.length})`],
                ["BUY", `只看买点 (${buyCount})`],
                ["SELL", `只看卖点 (${sellCount})`],
                ["NONE", "隐藏"],
              ] as Array<[MarkerFilter, string]>
            ).map(([key, lbl]) => (
              <button
                key={key}
                type="button"
                onClick={() => setMarkerFilter(key)}
                className={cn(
                  "rounded px-2 py-0.5 transition-colors",
                  markerFilter === key
                    ? "bg-sky-600 text-white font-semibold"
                    : "text-slate-400 hover:text-slate-200"
                )}
              >
                {lbl}
              </button>
            ))}
          </div>
        </div>

        {/* Right Controls: Clean Buy/Sell Stepper + Reset + More Tools + Fullscreen */}
        <div className="relative flex flex-wrap items-center gap-1.5">
          {indexedMarkers.length > 0 && (
            <div className="inline-flex items-center gap-1 rounded border border-sky-500/35 bg-sky-950/25 px-1.5 py-0.5 text-[11px] text-sky-200">
              <button
                type="button"
                onClick={() => stepMarker(-1)}
                className="rounded p-0.5 hover:bg-sky-500/20"
                title="定位上一笔买卖点"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-1 font-mono">
                第{" "}
                <strong className="text-white">
                  {selectedMarkerIdx !== null ? selectedMarkerIdx + 1 : "—"}
                </strong>
                /{indexedMarkers.length} 笔
              </span>
              <button
                type="button"
                onClick={() => stepMarker(1)}
                className="rounded p-0.5 hover:bg-sky-500/20"
                title="定位下一笔买卖点"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={() => {
              setSelectedMarkerIdx(null);
              setRangePreset("ALL");
              mainChartRef.current?.timeScale().fitContent();
            }}
            title="重置全图视野"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>重置视野</span>
          </Button>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2.5"
            onClick={() => setShowMoreTools((v) => !v)}
          >
            <span>更多工具 ▾</span>
          </Button>

          {showMoreTools && (
            <div className="absolute right-10 top-9 z-30 w-60 rounded-lg border border-border bg-popover p-3 shadow-2xl space-y-2.5 text-xs">
              <div className="space-y-1">
                <div className="text-[11px] font-semibold text-slate-300">视野范围与缩放</div>
                <div className="flex items-center gap-1">
                  {(
                    [
                      ["30B", "近30根"],
                      ["60B", "近60根"],
                      ["ALL", "全部"],
                    ] as Array<[RangePreset, string]>
                  ).map(([preset, lbl]) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => setRangePreset(preset)}
                      className={cn(
                        "flex-1 rounded border border-slate-800 py-1 text-[11px]",
                        rangePreset === preset
                          ? "bg-blue-600 text-white font-semibold"
                          : "bg-slate-950 text-slate-300 hover:text-white"
                      )}
                    >
                      {lbl}
                    </button>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    onClick={() => handleZoom(0.7)}
                    title="放大"
                  >
                    <ZoomIn className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    onClick={() => handleZoom(1.4)}
                    title="缩小"
                  >
                    <ZoomOut className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>

              <label className="flex items-center justify-between rounded border border-slate-800 px-2 py-1.5 text-slate-200 cursor-pointer hover:bg-slate-800/60">
                <span>叠加 EMA20 指数均线</span>
                <input
                  type="checkbox"
                  checked={showEma}
                  onChange={() => setShowEma((v) => !v)}
                  className="accent-blue-500"
                />
              </label>

              <div className="flex items-center gap-2 pt-1 border-t border-slate-800">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="flex-1 h-7"
                  onClick={handleExportCsv}
                >
                  <Download className="h-3.5 w-3.5" />
                  <span>导出 CSV</span>
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="flex-1 h-7"
                  onClick={handleExportPng}
                >
                  <Camera className="h-3.5 w-3.5" />
                  <span>保存图片</span>
                </Button>
              </div>
            </div>
          )}

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={() => setIsFullscreen((v) => !v)}
            title={isFullscreen ? "退出全屏" : "全屏展开 K 线复盘"}
          >
            {isFullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
      </div>

      {/* Row 2: Live OHLCV + Indicator Crosshair Telemetry Strip */}
      {hoverLegend && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 bg-[#080d18] px-3.5 py-1.5 font-mono text-[11px] tabular-nums-strict text-slate-300">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-1 text-slate-400">
              <Crosshair className="h-3 w-3 text-sky-400" />
              {formatUtcTimestamp(hoverLegend.timeSec * 1000, false)}
            </span>
            <span>
              开 <strong className="text-slate-100">{formatPriceBySpec(hoverLegend.open, tickSize)}</strong>
            </span>
            <span>
              高 <strong className="text-slate-100">{formatPriceBySpec(hoverLegend.high, tickSize)}</strong>
            </span>
            <span>
              低 <strong className="text-slate-100">{formatPriceBySpec(hoverLegend.low, tickSize)}</strong>
            </span>
            <span>
              收{" "}
              <strong className="text-white">
                {formatPriceBySpec(hoverLegend.close, tickSize)}
              </strong>
            </span>
            <span
              className={cn(
                "font-semibold",
                hoverLegend.changePct >= 0
                  ? pnlPolarity === "red-up"
                    ? "text-rose-400"
                    : "text-emerald-400"
                  : pnlPolarity === "red-up"
                    ? "text-emerald-400"
                    : "text-rose-400"
              )}
            >
              {hoverLegend.changePct >= 0 ? "+" : ""}
              {hoverLegend.changePct.toFixed(2)}%
            </span>
            <span className="text-slate-400">量 {hoverLegend.volume.toFixed(1)}</span>

            {showMa && hoverLegend.ma5 !== undefined && (
              <span className="text-amber-300">
                MA5: {formatPriceBySpec(hoverLegend.ma5, tickSize)}
              </span>
            )}
            {showMa && hoverLegend.ma10 !== undefined && (
              <span className="text-violet-300">
                MA10: {formatPriceBySpec(hoverLegend.ma10, tickSize)}
              </span>
            )}
            {showMa && hoverLegend.ma20 !== undefined && (
              <span className="text-sky-300">
                MA20: {formatPriceBySpec(hoverLegend.ma20, tickSize)}
              </span>
            )}
            {showEma && hoverLegend.ema20 !== undefined && (
              <span className="text-pink-300">
                EMA20: {formatPriceBySpec(hoverLegend.ema20, tickSize)}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {hoverLegend.equity !== undefined && (
              <span className="text-sky-300">
                账户净值: <strong>{formatCurrency(hoverLegend.equity)}</strong>
              </span>
            )}
            {hoverLegend.drawdownPct !== undefined && (
              <span className="text-rose-300">
                实时回撤: <strong>{formatPercent(-Math.abs(hoverLegend.drawdownPct))}</strong>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Main K-line Canvas + Floating Execution Point Inspector Card */}
      <div className="relative w-full bg-[#080d18]">
        <div ref={mainContainerRef} className="w-full" />

        {activeInspectorFills.length > 0 && (
          <div className="pointer-events-auto absolute left-3 top-3 z-20 max-w-sm rounded-lg border border-sky-500/45 bg-slate-950/92 p-3 shadow-2xl backdrop-blur">
            <div className="mb-1.5 flex items-center justify-between gap-2 border-b border-slate-800 pb-1">
              <span className="font-mono text-[11px] font-semibold text-sky-300">
                {hoveredBucketFills.length > 0
                  ? `当前 K 线触发 ${hoveredBucketFills.length} 笔成交明细`
                  : `已锁定买卖点 #${activeInspectorFills[0].seqIndex}`}
              </span>
              {selectedMarker && hoveredBucketFills.length === 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedMarkerIdx(null)}
                  className="font-mono text-[10px] text-slate-400 hover:text-white"
                >
                  关闭锁定
                </button>
              )}
            </div>
            <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
              {activeInspectorFills.map((m) => {
                const isBuy = m.side.toUpperCase() === "BUY";
                const pnlNum = Number(m.realized_pnl || 0);
                return (
                  <div
                    key={m.fill_id}
                    className="rounded border border-slate-800/90 bg-slate-900/75 p-2 font-mono text-[11px] space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[10px] font-bold",
                          isBuy
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-rose-500/20 text-rose-300"
                        )}
                      >
                        #{m.seqIndex} {m.action_label || m.side.toUpperCase()}
                      </span>
                      <span className="text-[10px] text-slate-400">
                        {formatUtcTimestamp(m.ts_ms || m.time_sec * 1000, true)}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-300">
                      <span>
                        成交价:{" "}
                        <strong className="text-white">
                          {formatPriceBySpec(m.price, tickSize)}
                        </strong>
                      </span>
                      <span>
                        数量:{" "}
                        <strong className="text-white">
                          {formatQtyBySpec(m.qty, stepSize)}
                        </strong>
                      </span>
                      <span>
                        滑点: <strong className="text-slate-200">{m.slippage_bps} bps</strong>
                      </span>
                      <span>
                        手续费: <strong className="text-amber-300">{m.fee} U</strong>
                      </span>
                      <span>
                        成交后净仓:{" "}
                        <strong className="text-sky-300">
                          {formatQtyBySpec(m.position_after || "0", stepSize)}
                        </strong>
                      </span>
                      <span>
                        已实现盈亏:{" "}
                        {Math.abs(pnlNum) > 1e-9 ? (
                          <NumericDisplay
                            value={pnlNum}
                            mode="currency"
                            coloredBySign
                            showPlusSign
                          />
                        ) : (
                          <span className="text-slate-500">开仓无平仓盈亏</span>
                        )}
                      </span>
                    </div>
                    {m.signal_reason && (
                      <div className="text-[10px] text-sky-300/90">
                        信号原因: <code>{m.signal_reason}</code> · {m.liquidity}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Row 3: Chronological All Buy/Sell Execution Points Strip (1-Click Jump to Exact Candle) */}
      {indexedMarkers.length > 0 && (
        <div className="border-t border-slate-800 bg-slate-950/90 px-3 py-2">
          <div className="mb-1.5 flex items-center justify-between text-[11px]">
            <span className="font-semibold text-slate-200">
              回测全量买卖执行点时间轴（共 {indexedMarkers.length} 笔：买入 {buyCount} 笔 / 卖出 {sellCount} 笔 · 点击任一节点精准聚焦 K 线）
            </span>
            <span className="font-mono text-[10px] text-slate-400">
              点击 K 线箭头或下方胶囊均可锁定成交价格水平线
            </span>
          </div>
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
            {indexedMarkers.map((m, idx) => {
              const isBuy = m.side.toUpperCase() === "BUY";
              const isSelected = selectedMarkerIdx === idx;
              const pnlNum = Number(m.realized_pnl || 0);
              return (
                <button
                  key={m.fill_id}
                  type="button"
                  onClick={() => {
                    setSelectedMarkerIdx(idx);
                    onSelectMarker?.(m);
                  }}
                  className={cn(
                    "shrink-0 inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[11px] transition-all",
                    isSelected
                      ? "border-sky-400 bg-sky-500/20 text-white ring-1 ring-sky-400/50"
                      : isBuy
                        ? "border-emerald-500/30 bg-emerald-950/25 text-emerald-200 hover:border-emerald-400"
                        : "border-rose-500/30 bg-rose-950/25 text-rose-200 hover:border-rose-400"
                  )}
                >
                  <span className="font-bold">
                    #{m.seqIndex} {m.action_label ? m.action_label.split(" ")[0] : m.side}
                  </span>
                  <span className="text-slate-200">
                    @{formatPriceBySpec(m.price, tickSize)}
                  </span>
                  <span className="text-slate-400">
                    ×{formatQtyBySpec(m.qty, stepSize)}
                  </span>
                  {Math.abs(pnlNum) > 1e-8 && (
                    <span
                      className={cn(
                        "font-semibold",
                        pnlNum > 0 ? "text-emerald-300" : "text-rose-300"
                      )}
                    >
                      ({pnlNum > 0 ? "+" : ""}
                      {pnlNum.toFixed(2)}U)
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Row 4: Synchronized Equity + Benchmark + Underwater Drawdown Pane */}
      {showEquitySubpane && sortedEquity.length > 0 && (
        <div className="border-t border-slate-800">
          <div className="bg-slate-950/85 px-3.5 py-1 text-[10px] font-mono text-slate-400">
            同步资金曲线 vs 买入持有基准 (右轴 USDT) · 水下回撤面积图 (左轴 %)
          </div>
          <div ref={eqContainerRef} className="w-full" />
        </div>
      )}
    </div>
  );
}
