"""
Authentic Historical Binance USDT-M Perpetual Verification for Delphi II (v4.4 vs v4.5 vs v4.6).
Uses real Binance 1H OHLCV + 8H funding settlements (2023-10-15..2026-07-01 UTC, 23,760 bars/symbol)
stored in quant_platform/data/historical/{BTC,ETH,SOL}USDT_1h.csv.gz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from quant_platform.data.historical import load_real_binance_1h_dataset

TRADE_START_MS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class SimPos:
    symbol: str
    side: str  # "L" | "S"
    qty: Decimal
    entry_px: Decimal
    entry_atr: Decimal
    entry_idx: int
    stop_px: Decimal
    highest: Decimal
    lowest: Decimal


def _tick_step(symbol: str) -> tuple[Decimal, Decimal]:
    if symbol.startswith("SOL"):
        return Decimal("0.001"), Decimal("0.01")
    if symbol.startswith("ETH"):
        return Decimal("0.01"), Decimal("0.001")
    return Decimal("0.1"), Decimal("0.001")


def run_portfolio_real_binance(
    symbols: list[str],
    max_slots: int,
    params: dict[str, Any],
    end_ts_ms: int | None = None,
) -> dict[str, Any]:
    datasets = {
        sym: load_real_binance_1h_dataset("binance", sym, end_ts_ms=end_ts_ms)
        for sym in symbols
    }
    n_bars = min(len(datasets[s][0]) for s in symbols)
    fr_lookup: dict[tuple[str, int], Decimal] = {}
    for sym in symbols:
        for fp in datasets[sym][1]:
            fr_lookup[(sym, fp.funding_ts_ms)] = fp.funding_rate

    eq = Decimal("10000")
    peak_eq = eq
    max_dd = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    fee_rate = Decimal("0.0004")
    a20 = Decimal("2") / Decimal("21")
    a50 = Decimal("2") / Decimal("51")

    state: dict[str, dict[str, Any]] = {
        sym: {
            "bars": [],
            "trs_1h": [],
            "cur_d": [],
            "d_closes": [],
            "d_trs": [],
            "e20_d": None,
            "e50_d": None,
            "e20_d_hist": [],
            "e50_d_hist": [],
            "e20_1h": None,
        }
        for sym in symbols
    }
    open_pos: dict[str, SimPos] = {}
    trades: list[tuple[str, str, Decimal]] = []

    for i in range(n_bars):
        # 1) Update indicators & settle 8H funding for each asset
        for sym in symbols:
            b = datasets[sym][0][i]
            st = state[sym]
            tr1 = (b.high - b.low) if i == 0 else max(b.high - b.low, abs(b.high - st["bars"][-1].close), abs(b.low - st["bars"][-1].close))
            st["bars"].append(b)
            st["trs_1h"].append(tr1)
            st["e20_1h"] = b.close if st["e20_1h"] is None else (a20 * b.close + (Decimal("1") - a20) * st["e20_1h"])
            st["cur_d"].append(b)
            if len(st["cur_d"]) >= 24:
                d_hi = max(x.high for x in st["cur_d"])
                d_lo = min(x.low for x in st["cur_d"])
                d_cl = st["cur_d"][-1].close
                d_tr = (d_hi - d_lo) if not st["d_closes"] else max(d_hi - d_lo, abs(d_hi - st["d_closes"][-1]), abs(d_lo - st["d_closes"][-1]))
                st["d_closes"].append(d_cl)
                st["d_trs"].append(d_tr)
                st["e20_d"] = d_cl if st["e20_d"] is None else (a20 * d_cl + (Decimal("1") - a20) * st["e20_d"])
                st["e50_d"] = d_cl if st["e50_d"] is None else (a50 * d_cl + (Decimal("1") - a50) * st["e50_d"])
                st["e20_d_hist"].append(st["e20_d"])
                st["e50_d_hist"].append(st["e50_d"])
                st["cur_d"] = []

            fr = fr_lookup.get((sym, b.close_time_ms))
            if fr is not None and sym in open_pos:
                p = open_pos[sym]
                signed_q = p.qty if p.side == "L" else -p.qty
                f_pay = -(signed_q * b.close * fr)
                eq += f_pay
                total_funding += f_pay

        bar_ts = datasets[symbols[0]][0][i].open_time_ms
        if bar_ts < TRADE_START_MS:
            continue

        # 2) Manage open positions
        for sym in list(open_pos.keys()):
            st = state[sym]
            b = st["bars"][-1]
            p = open_pos[sym]
            tick, _ = _tick_step(sym)
            cur_atr = sum(st["trs_1h"][-14:], Decimal("0")) / Decimal("14")
            eff_atr = max(p.entry_atr, cur_atr) if params["lock_atr"] else cur_atr
            exit_px: Decimal | None = None

            if p.side == "L" and b.low <= p.stop_px:
                q_stop = max(tick, p.stop_px.quantize(tick, rounding=ROUND_DOWN))
                exit_px = min(b.open, q_stop)
            elif p.side == "S" and b.high >= p.stop_px:
                q_stop = max(tick, p.stop_px.quantize(tick, rounding=ROUND_UP))
                exit_px = max(b.open, q_stop)
            else:
                p.highest = max(p.highest, b.high)
                p.lowest = min(p.lowest, b.low)
                last_dc = st["d_closes"][-1]
                e20d, e50d = st["e20_d"], st["e50_d"]
                if params["rev_exit"] and (
                    (p.side == "L" and last_dc < e50d and e20d < e50d)
                    or (p.side == "S" and last_dc > e50d and e20d > e50d)
                ):
                    exit_px = b.close
                else:
                    if p.side == "L" and (p.highest - p.entry_px) / p.entry_atr >= params["act"]:
                        p.stop_px = max(p.stop_px, p.highest - params["l_trail"] * eff_atr)
                    elif p.side == "S" and (p.entry_px - p.lowest) / p.entry_atr >= params["act"]:
                        p.stop_px = min(p.stop_px, p.lowest + params["s_trail"] * eff_atr)

            if exit_px is not None:
                gross = ((exit_px - p.entry_px) if p.side == "L" else (p.entry_px - exit_px)) * p.qty
                fee = (p.entry_px + exit_px) * p.qty * fee_rate
                net = gross - fee
                eq += net
                total_fees += fee
                trades.append((sym, p.side, net))
                del open_pos[sym]

        # 3) Evaluate new entries up to max_slots
        for sym in symbols:
            if sym in open_pos or len(open_pos) >= max_slots:
                continue
            st = state[sym]
            lb = params["lb"]
            if len(st["d_closes"]) < 55 or len(st["bars"]) < max(72, lb + 5):
                continue
            b = st["bars"][-1]
            e20d, e50d = st["e20_d"], st["e50_d"]
            last_dc = st["d_closes"][-1]
            atrd = sum(st["d_trs"][-14:], Decimal("0")) / Decimal("14")
            cur_atr = sum(st["trs_1h"][-14:], Decimal("0")) / Decimal("14")

            slope_up = (not params["slope"]) or (e50d > st["e50_d_hist"][-4])
            slope_dn = (not params["slope"]) or (e50d < st["e50_d_hist"][-4] and e20d < st["e20_d_hist"][-4])
            allow_l = last_dc > e50d and e20d > e50d and slope_up
            allow_s = last_dc < e50d and e20d < e50d and slope_dn
            if not allow_l and not allow_s:
                continue

            dist = abs(b.close - e50d) / atrd
            eff_max = params["max_d"] if allow_l else params["s_max_d"]
            if dist <= params["min_d"] or dist >= eff_max:
                continue
            z_mult = params["ext_mult"] if dist >= params["soft_d"] else Decimal("1.0")
            if allow_s:
                z_mult *= params["s_mult"]

            pwin = st["bars"][-(lb + 1) : -1]
            prev_atr = sum(st["trs_1h"][-15:-1], Decimal("0")) / Decimal("14")
            uch = max(x.high for x in pwin) + params["ch_mult"] * prev_atr
            lch = min(x.low for x in pwin) - params["ch_mult"] * prev_atr
            rec6, pri6 = st["bars"][-6:], st["bars"][-12:-6]
            _, step_q = _tick_step(sym)

            slot_cap = Decimal("0.90") / Decimal(max_slots) if max_slots > 1 else Decimal("0.90")
            if params["atr_sizing"]:
                raw_q = (eq * params["risk_pct"] * z_mult) / (params["init_stop"] * cur_atr)
                max_q = (eq * slot_cap * z_mult) / b.close
                qty = max(step_q, min(raw_q, max_q).quantize(step_q, rounding=ROUND_DOWN))
            else:
                fixed_cap = (Decimal("0.65") / Decimal(max_slots)) if max_slots > 1 else Decimal("0.65")
                qty = max(step_q, ((eq * fixed_cap * z_mult) / b.close).quantize(step_q, rounding=ROUND_DOWN))

            brk_l = (b.close > uch) if params["close_brk"] else (b.high > uch)
            brk_s = (b.close < lch) if params["close_brk"] else (b.low < lch)

            if allow_l and brk_l and min(x.low for x in rec6) >= min(x.low for x in pri6) and b.close > st["bars"][-2].close and b.close > st["e20_1h"]:
                open_pos[sym] = SimPos(sym, "L", qty, b.close, cur_atr, i, b.close - params["init_stop"] * cur_atr, b.high, b.low)
            elif allow_s and brk_s and max(x.high for x in rec6) <= max(x.high for x in pri6) and b.close < st["bars"][-2].close and b.close < st["e20_1h"]:
                open_pos[sym] = SimPos(sym, "S", qty, b.close, cur_atr, i, b.close + params["init_stop"] * cur_atr, b.high, b.low)

        mtm = eq
        for sym, p in open_pos.items():
            c = state[sym]["bars"][-1].close
            mtm += ((c - p.entry_px) if p.side == "L" else (p.entry_px - c)) * p.qty
        if mtm > peak_eq:
            peak_eq = mtm
        dd = (mtm - peak_eq) / peak_eq
        if dd < max_dd:
            max_dd = dd

    end_ms = datasets[symbols[0]][0][n_bars - 1].open_time_ms
    months = (end_ms - TRADE_START_MS) / (1000.0 * 86400.0 * 30.4375)
    wins = sum(1 for _, _, pnl in trades if pnl > 0)
    return {
        "return_pct": float((eq - Decimal("10000")) / Decimal("100")),
        "max_dd_pct": float(max_dd * Decimal("100")),
        "trades": len(trades),
        "trades_per_mo": len(trades) / max(0.1, months),
        "win_rate_pct": (wins / len(trades) * 100.0) if trades else 0.0,
        "fees_usdt": float(total_fees),
        "funding_usdt": float(total_funding),
        "long_pnl": float(sum((p for _, s, p in trades if s == "L"), Decimal("0"))),
        "short_pnl": float(sum((p for _, s, p in trades if s == "S"), Decimal("0"))),
    }


PRESETS = {
    "v4.4 原版基准": dict(
        min_d=Decimal("1.0"), soft_d=Decimal("999"), max_d=Decimal("999"), s_max_d=Decimal("999"),
        ext_mult=Decimal("1.0"), s_mult=Decimal("1.0"), lb=15, ch_mult=Decimal("0.45"),
        close_brk=False, slope=False, init_stop=Decimal("3.5"), act=Decimal("1.5"),
        l_trail=Decimal("2.0"), s_trail=Decimal("0.5"), lock_atr=False, rev_exit=False,
        atr_sizing=False, risk_pct=Decimal("0.045"),
    ),
    "v4.5 1H风控版": dict(
        min_d=Decimal("1.0"), soft_d=Decimal("3.5"), max_d=Decimal("3.5"), s_max_d=Decimal("3.5"),
        ext_mult=Decimal("1.0"), s_mult=Decimal("1.0"), lb=15, ch_mult=Decimal("0.45"),
        close_brk=False, slope=False, init_stop=Decimal("3.5"), act=Decimal("1.5"),
        l_trail=Decimal("2.0"), s_trail=Decimal("1.8"), lock_atr=True, rev_exit=True,
        atr_sizing=True, risk_pct=Decimal("0.025"),
    ),
    "v4.6 真实行情波段增强版": dict(
        min_d=Decimal("0.20"), soft_d=Decimal("2.8"), max_d=Decimal("6.0"), s_max_d=Decimal("3.2"),
        ext_mult=Decimal("0.55"), s_mult=Decimal("0.50"), lb=48, ch_mult=Decimal("0.10"),
        close_brk=True, slope=True, init_stop=Decimal("5.0"), act=Decimal("3.5"),
        l_trail=Decimal("8.0"), s_trail=Decimal("5.0"), lock_atr=True, rev_exit=True,
        atr_sizing=True, risk_pct=Decimal("0.045"),
    ),
}


def main() -> None:
    print("=== REAL BINANCE USDT-M PERPETUAL 1H + 8H FUNDING (2024-01-01 .. 2026-07-01, 30 MONTHS) ===")
    for target_lbl, syms, slots in [
        ("BTC/USDT:USDT 单币", ["BTC/USDT:USDT"], 1),
        ("ETH/USDT:USDT 单币", ["ETH/USDT:USDT"], 1),
        ("SOL/USDT:USDT 单币", ["SOL/USDT:USDT"], 1),
        ("BTC+ETH+SOL 双槽位组合 (Max 2 Slots)", ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"], 2),
    ]:
        print(f"\n[{target_lbl}]")
        for v_name, cfg in PRESETS.items():
            r = run_portfolio_real_binance(syms, slots, cfg)
            print(
                f"  {v_name:22s} | Ret={r['return_pct']:+7.2f}% | MaxDD={r['max_dd_pct']:6.2f}% | "
                f"Tr={r['trades']:3d} ({r['trades_per_mo']:4.1f}/mo) | Win={r['win_rate_pct']:5.1f}% | "
                f"Fees=${r['fees_usdt']:6.0f} | Fund=${r['funding_usdt']:+6.0f} | L=${r['long_pnl']:+6.0f} S=${r['short_pnl']:+6.0f}"
            )


if __name__ == "__main__":
    main()
