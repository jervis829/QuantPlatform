export type PnlPolarity = "green-up" | "red-up";
export type TableDensity = "compact" | "standard" | "comfortable";
export type RunEnvironment = "backtest" | "shadow" | "live";

export const DESIGN_TOKENS = {
  layout: {
    desktopBaselinePx: 1440,
    minNoScrollWidthPx: 1280,
    topStatusBarHeightPx: 48,
    sidebarWidthPx: 224,
  },
  typography: {
    kpiHero: "text-2xl font-semibold tracking-tight font-mono tabular-nums-strict",
    kpiSub: "text-xs text-muted-foreground",
    sectionTitle: "text-sm font-semibold tracking-wide text-slate-200",
    tableHeader: "text-[11px] font-semibold uppercase tracking-wider text-slate-400",
    monoCell: "font-mono text-xs tabular-nums-strict",
  },
  densityRowHeightPx: {
    compact: 30,
    standard: 38,
    comfortable: 46,
  } as Record<TableDensity, number>,
  densityCellPadding: {
    compact: "px-2.5 py-1 text-xs",
    standard: "px-3 py-2 text-xs",
    comfortable: "px-3.5 py-2.5 text-sm",
  } as Record<TableDensity, string>,
  environmentMeta: {
    backtest: {
      label: "BACKTEST · 回测",
      shortLabel: "BACKTEST",
      riskLevel: "SAFE",
      description: "历史数据离线事件回放，不接触实时行情与资金账户",
    },
    shadow: {
      label: "SHADOW · 影子模拟",
      shortLabel: "SHADOW",
      riskLevel: "CAUTION",
      description: "接入实时盘口流但本地模拟撮合，物理隔离实盘交易密钥",
    },
    live: {
      label: "LIVE · 实盘/测试网",
      shortLabel: "LIVE",
      riskLevel: "DANGER",
      description: "通过 ExchangeBroker 向交易所提交真实委托，受事前风控与 Kill Switch 约束",
    },
  },
  chartColors: {
    "green-up": {
      up: "#1cb978",
      down: "#f43f5e",
      upWick: "#1cb978",
      downWick: "#f43f5e",
    },
    "red-up": {
      up: "#f43f5e",
      down: "#1cb978",
      upWick: "#f43f5e",
      downWick: "#1cb978",
    },
  },
} as const;
