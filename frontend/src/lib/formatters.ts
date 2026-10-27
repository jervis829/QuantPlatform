/**
 * QuantPlatform Unified Number, Precision & Timestamp Formatting Utilities.
 * Enforces:
 * - Thousands separators
 * - Exact decimal places derived from SymbolSpec (tick_size / step_size) without float drift
 * - Explicit currency/unit suffixes (USDT / USD / % / bps)
 * - Unambiguous negative ("-") vs zero ("0.00") distinction
 * - Consistent compact abbreviations (K / M / B)
 */

export type SignCategory = "positive" | "negative" | "zero";

function normalizeNumericInput(raw: string | number | null | undefined): {
  negative: boolean;
  intPart: string;
  fracPart: string;
  isZero: boolean;
  isValid: boolean;
} {
  if (raw === null || raw === undefined || raw === "") {
    return { negative: false, intPart: "0", fracPart: "", isZero: true, isValid: false };
  }
  const str = String(raw).trim();
  if (!str || str === "NaN" || str === "Infinity" || str === "-Infinity") {
    return { negative: false, intPart: "0", fracPart: "", isZero: true, isValid: false };
  }
  if (str.includes("e") || str.includes("E")) {
    const asNum = Number(str);
    if (!Number.isFinite(asNum)) {
      return { negative: false, intPart: "0", fracPart: "", isZero: true, isValid: false };
    }
    return normalizeNumericInput(asNum.toFixed(12));
  }
  const negative = str.startsWith("-");
  const unsigned = str.replace(/^[+-]/, "");
  const [rawInt = "0", rawFrac = ""] = unsigned.split(".");
  const cleanInt = rawInt.replace(/^0+(?=\d)/, "") || "0";
  const cleanFrac = rawFrac.replace(/[^0-9]/g, "");
  const isZero = /^0+$/.test(cleanInt) && (!cleanFrac || /^0+$/.test(cleanFrac));
  return {
    negative: negative && !isZero,
    intPart: cleanInt,
    fracPart: cleanFrac,
    isZero,
    isValid: /^\d+$/.test(cleanInt),
  };
}

function addThousandsSeparators(intDigits: string): string {
  return intDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Derive exact decimal precision from a SymbolSpec tick_size or step_size string (e.g. "0.001" -> 3).
 */
export function getDecimalsFromStep(stepOrTick: string | number | null | undefined, fallback = 2): number {
  if (stepOrTick === null || stepOrTick === undefined) return fallback;
  const s = String(stepOrTick).trim();
  if (!s.includes(".")) return 0;
  const frac = s.split(".")[1]?.replace(/0+$/, "") ?? "";
  return frac.length;
}

export function classifySign(raw: string | number | null | undefined): SignCategory {
  const parsed = normalizeNumericInput(raw);
  if (!parsed.isValid || parsed.isZero) return "zero";
  return parsed.negative ? "negative" : "positive";
}

export interface FormatDecimalOptions {
  decimals?: number;
  showPlusSign?: boolean;
  unit?: string;
  trimTrailingZeros?: boolean;
}

/**
 * String-safe fixed-precision formatter with thousands separators and explicit unit support.
 */
export function formatFixedDecimal(
  raw: string | number | null | undefined,
  options: FormatDecimalOptions = {}
): string {
  const { decimals = 2, showPlusSign = false, unit, trimTrailingZeros = false } = options;
  const parsed = normalizeNumericInput(raw);
  if (!parsed.isValid) return "—";

  let intPart = parsed.intPart;
  let frac = parsed.fracPart;

  if (decimals <= 0) {
    // Round half-up at integer boundary
    if (frac.length > 0 && Number(frac[0]) >= 5) {
      intPart = (BigInt(intPart) + 1n).toString();
    }
    frac = "";
  } else if (frac.length > decimals) {
    const keep = frac.slice(0, decimals);
    const nextDigit = Number(frac[decimals] || "0");
    if (nextDigit >= 5) {
      const bumped = (BigInt(keep || "0") + 1n).toString().padStart(decimals, "0");
      if (bumped.length > decimals) {
        intPart = (BigInt(intPart) + 1n).toString();
        frac = "0".repeat(decimals);
      } else {
        frac = bumped;
      }
    } else {
      frac = keep;
    }
  } else {
    frac = frac.padEnd(decimals, "0");
  }

  if (trimTrailingZeros && frac) {
    frac = frac.replace(/0+$/, "");
  }

  const allZeroAfterRound = /^0+$/.test(intPart) && (!frac || /^0+$/.test(frac));
  const signPrefix = allZeroAfterRound
    ? ""
    : parsed.negative
      ? "-"
      : showPlusSign
        ? "+"
        : "";

  const formattedInt = addThousandsSeparators(intPart);
  const core = frac.length > 0 ? `${signPrefix}${formattedInt}.${frac}` : `${signPrefix}${formattedInt}`;
  return unit ? `${core} ${unit}` : core;
}

/**
 * Format price strictly according to SymbolSpec.tick_size.
 */
export function formatPriceBySpec(
  price: string | number | null | undefined,
  tickSize: string | number | null | undefined,
  quoteAsset?: string
): string {
  const decimals = getDecimalsFromStep(tickSize, 2);
  return formatFixedDecimal(price, { decimals, unit: quoteAsset });
}

/**
 * Format order/position quantity strictly according to SymbolSpec.step_size.
 */
export function formatQtyBySpec(
  qty: string | number | null | undefined,
  stepSize: string | number | null | undefined,
  baseAsset?: string
): string {
  const decimals = getDecimalsFromStep(stepSize, 4);
  return formatFixedDecimal(qty, { decimals, unit: baseAsset });
}

/**
 * Format monetary value with explicit currency suffix (default USDT).
 */
export function formatCurrency(
  amount: string | number | null | undefined,
  currency: "USDT" | "USD" | string = "USDT",
  options: { decimals?: number; showPlusSign?: boolean } = {}
): string {
  return formatFixedDecimal(amount, {
    decimals: options.decimals ?? 2,
    showPlusSign: options.showPlusSign ?? false,
    unit: currency,
  });
}

/**
 * Consistent large-number abbreviation: >=1B -> B, >=1M -> M, >=10K -> K.
 */
export function formatCompactNumber(
  raw: string | number | null | undefined,
  options: { unit?: string; showPlusSign?: boolean } = {}
): string {
  const num = typeof raw === "number" ? raw : Number(raw);
  if (raw === null || raw === undefined || raw === "" || !Number.isFinite(num)) {
    return "—";
  }
  const abs = Math.abs(num);
  const isZero = abs < 1e-12;
  const sign = isZero ? "" : num < 0 ? "-" : options.showPlusSign ? "+" : "";
  let scaled = abs;
  let suffix = "";
  if (abs >= 1_000_000_000) {
    scaled = abs / 1_000_000_000;
    suffix = "B";
  } else if (abs >= 1_000_000) {
    scaled = abs / 1_000_000;
    suffix = "M";
  } else if (abs >= 10_000) {
    scaled = abs / 1_000;
    suffix = "K";
  }
  const body = suffix ? `${scaled.toFixed(2)}${suffix}` : formatFixedDecimal(abs, { decimals: 2 });
  const core = `${sign}${body}`;
  return options.unit ? `${core} ${options.unit}` : core;
}

/**
 * Format ratio or percentage. Set `inputIsRatio: true` when passing 0.1234 -> "12.34%".
 */
export function formatPercent(
  value: string | number | null | undefined,
  options: { inputIsRatio?: boolean; decimals?: number; showPlusSign?: boolean } = {}
): string {
  const num = typeof value === "number" ? value : Number(value);
  if (value === null || value === undefined || value === "" || !Number.isFinite(num)) {
    return "—";
  }
  const pct = options.inputIsRatio ? num * 100 : num;
  const decimals = options.decimals ?? 2;
  const core = formatFixedDecimal(pct.toFixed(decimals + 2), {
    decimals,
    showPlusSign: options.showPlusSign ?? false,
  });
  return core === "—" ? "—" : `${core}%`;
}

export function formatBps(
  bps: string | number | null | undefined,
  options: { decimals?: number; showPlusSign?: boolean } = {}
): string {
  return formatFixedDecimal(bps, {
    decimals: options.decimals ?? 2,
    showPlusSign: options.showPlusSign ?? true,
    unit: "bps",
  });
}

export function formatUtcTimestamp(tsMs: number | null | undefined, includeSeconds = true): string {
  if (!tsMs || !Number.isFinite(tsMs)) return "—";
  const d = new Date(tsMs);
  const iso = d.toISOString(); // YYYY-MM-DDTHH:mm:ss.sssZ
  return includeSeconds ? `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC` : `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${(ms / 1000).toFixed(1)}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return `${min}m ${sec}s`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin}m`;
}
