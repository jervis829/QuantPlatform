"""
Uploads AST-safe Delphi II v4.4 (Baseline), Delphi II v4.5 (Hard-Cap Conservative),
and Delphi II v4.6 (Tiered Corridor Anti-Miss) Python Strategy implementations into
the live QuantPlatform API and runs real BacktestEngine + Parameter Sensitivity verifications.
"""

from __future__ import annotations

import json
import httpx


DELPHI_STRATEGY_CODE = '''from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import ema
from quant_platform.strategy.ui_meta import ui_param


class DelphiParams(BaseModel):
    bars_per_day: int = ui_param(24, ge=4, le=24, ui_group="signal", unit="Bars", description="大周期聚合K线根数（24根1H = 真实UTC日线）")
    min_dist_atr_d: Decimal = ui_param(Decimal("1.0"), ui_group="signal", unit="ATR_D", description="价格距日线EMA50最小偏离倍数（避开均线粘合震荡区）")
    soft_dist_atr_d: Decimal = ui_param(Decimal("3.8"), ui_group="signal", unit="ATR_D", description="标准满仓走廊上限（超过后进入强趋势延续段，自动按比例降仓）")
    max_dist_atr_d: Decimal = ui_param(Decimal("8.5"), ui_group="signal", unit="ATR_D", description="极端偏离硬上限（v4.4=999无上限，v4.5=3.5一刀切禁开，v4.6=8.5防踏空）")
    extended_size_mult: Decimal = ui_param(Decimal("0.60"), ui_group="risk_sizing", unit="Ratio", description="强趋势延续段[soft, max)仓位缩放系数（既吃二段趋势，又防高潮顶重伤）")
    channel_lookback: int = ui_param(15, ge=5, le=60, ui_group="signal", unit="Bars", description="1H突破通道回看K线根数")
    channel_atr_mult: Decimal = ui_param(Decimal("0.45"), ui_group="signal", unit="ATR", description="1H通道突破外扩ATR缓冲倍数（过滤横盘假突破毛刺）")
    init_stop_atr: Decimal = ui_param(Decimal("3.5"), ui_group="risk_sizing", unit="ATR", description="初始硬止损距离（ATR倍数）")
    trail_activate_atr: Decimal = ui_param(Decimal("1.5"), ui_group="risk_sizing", unit="ATR", description="移动止损激活浮盈阈值")
    long_trail_atr: Decimal = ui_param(Decimal("2.0"), ui_group="risk_sizing", unit="ATR", description="多单移动止损回撤距离（最高价 - N*ATR）")
    short_trail_atr: Decimal = ui_param(Decimal("1.8"), ui_group="risk_sizing", unit="ATR", description="空单移动止损回撤距离（v4.4=0.5极易被洗，v4.5/v4.6=1.8抗熊市逼空反弹）")
    lock_entry_atr: bool = ui_param(True, ui_group="execution", description="锁定开仓ATR下限（防止横盘缩量时止损距离被动勒紧）")
    enable_reversal_exit: bool = ui_param(True, ui_group="execution", description="启用日线趋势反转与30根K线动能停滞主动离场")
    use_atr_risk_sizing: bool = ui_param(True, ui_group="risk_sizing", danger_level="high", danger_hint="开启后按单笔固定净值风险比例动态定仓；关闭则按固定65%名义仓位下单", description="启用ATR波动率风险平价定仓")
    risk_pct: Decimal = ui_param(Decimal("0.055"), ui_group="risk_sizing", unit="Ratio", danger_level="high", danger_hint="单笔初始止损占账户净值比例，调高将放大单笔回撤", description="标准走廊单笔风险预算占比")


class DelphiStrategy(Strategy):
    ParamModel = DelphiParams

    def on_start(self):
        self._bars = []
        self._trs_1h = []
        self._daily_closes = []
        self._daily_trs = []
        self._cur_day_bars = []
        self._entry_px = Decimal("0")
        self._entry_atr = Decimal("0")
        self._entry_bar_idx = 0
        self._stop_px = Decimal("0")
        self._highest = Decimal("0")
        self._lowest = Decimal("0")
        return None

    def _calc_atr(self, trs, window):
        if not trs:
            return Decimal("0")
        sub = trs[-window:]
        return sum(sub, Decimal("0")) / Decimal(len(sub))

    def _tick_for(self, symbol: str) -> Decimal:
        u = symbol.upper()
        if u.startswith("ETH") or u.startswith("SOL"):
            return Decimal("0.01")
        return Decimal("0.1")

    def _step_for(self, symbol: str) -> Decimal:
        if symbol.upper().startswith("SOL"):
            return Decimal("0.01")
        return Decimal("0.001")

    def on_bar(self, bar: Bar):
        idx = len(self._bars)
        if idx == 0:
            tr_1h = bar.high - bar.low
        else:
            prev_c = self._bars[-1].close
            tr_1h = max(bar.high - bar.low, abs(bar.high - prev_c), abs(bar.low - prev_c))
        self._bars.append(bar)
        self._trs_1h.append(tr_1h)

        # Roll closed 24H UTC Daily candles strictly without lookahead
        self._cur_day_bars.append(bar)
        bpd = self.params.bars_per_day
        if len(self._cur_day_bars) >= bpd:
            d_hi = max(x.high for x in self._cur_day_bars)
            d_lo = min(x.low for x in self._cur_day_bars)
            d_cl = self._cur_day_bars[-1].close
            if not self._daily_closes:
                d_tr = d_hi - d_lo
            else:
                prev_dc = self._daily_closes[-1]
                d_tr = max(d_hi - d_lo, abs(d_hi - prev_dc), abs(d_lo - prev_dc))
            self._daily_closes.append(d_cl)
            self._daily_trs.append(d_tr)
            self._cur_day_bars = []

        if len(self._daily_closes) < 50 or len(self._bars) < max(26, self.params.channel_lookback + 2):
            return None

        ema20_d = ema(self._daily_closes, 20)
        ema50_d = ema(self._daily_closes, 50)
        last_d_close = self._daily_closes[-1]
        atr_d = self._calc_atr(self._daily_trs, 14)
        cur_atr_1h = self._calc_atr(self._trs_1h, 14)
        if atr_d <= Decimal("0") or cur_atr_1h <= Decimal("0"):
            return None

        tick = self._tick_for(bar.symbol)
        step_q = self._step_for(bar.symbol)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        pos_qty = pos.quantity if pos is not None else Decimal("0")

        # 1) Manage Open Position (causal STOP_MARKET execution at real stop trigger price!)
        if pos_qty != Decimal("0"):
            is_long = pos_qty > Decimal("0")
            eff_atr = max(self._entry_atr, cur_atr_1h) if self.params.lock_entry_atr else cur_atr_1h

            if is_long and bar.low <= self._stop_px:
                q_stop = max(tick, self._stop_px.quantize(tick, rounding=ROUND_DOWN))
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.STOP_MARKET,
                        stop_price=q_stop,
                        quantity=abs(pos_qty),
                        reduce_only=True,
                        reason="delphi_long_stop_triggered",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            if (not is_long) and bar.high >= self._stop_px:
                q_stop = max(tick, self._stop_px.quantize(tick, rounding=ROUND_UP))
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.STOP_MARKET,
                        stop_price=q_stop,
                        quantity=abs(pos_qty),
                        reduce_only=True,
                        reason="delphi_short_stop_triggered",
                        ts_ms=self.ctx.now_ms,
                    )
                ]

            if bar.high > self._highest:
                self._highest = bar.high
            if bar.low < self._lowest:
                self._lowest = bar.low

            if self.params.enable_reversal_exit:
                if is_long and last_d_close < ema50_d:
                    return [
                        Signal(
                            strategy_id=self.ctx.config.strategy_id,
                            exchange=bar.exchange,
                            symbol=bar.symbol,
                            action=SignalAction.CLOSE,
                            order_type=OrderType.MARKET,
                            quantity=abs(pos_qty),
                            reduce_only=True,
                            reason="delphi_daily_reversal_exit",
                            ts_ms=self.ctx.now_ms,
                        )
                    ]
                if (not is_long) and last_d_close > ema50_d:
                    return [
                        Signal(
                            strategy_id=self.ctx.config.strategy_id,
                            exchange=bar.exchange,
                            symbol=bar.symbol,
                            action=SignalAction.CLOSE,
                            order_type=OrderType.MARKET,
                            quantity=abs(pos_qty),
                            reduce_only=True,
                            reason="delphi_daily_reversal_exit",
                            ts_ms=self.ctx.now_ms,
                        )
                    ]
                if (idx - self._entry_bar_idx) >= 30:
                    mfe = (self._highest - self._entry_px) if is_long else (self._entry_px - self._lowest)
                    unreal = (bar.close - self._entry_px) if is_long else (self._entry_px - bar.close)
                    if mfe < self._entry_atr and unreal < Decimal("0"):
                        return [
                            Signal(
                                strategy_id=self.ctx.config.strategy_id,
                                exchange=bar.exchange,
                                symbol=bar.symbol,
                                action=SignalAction.CLOSE,
                                order_type=OrderType.MARKET,
                                quantity=abs(pos_qty),
                                reduce_only=True,
                                reason="delphi_time_stagnation_exit",
                                ts_ms=self.ctx.now_ms,
                            )
                        ]

            # Update ratchet chandelier trailing stop
            if is_long:
                gain_atr = (self._highest - self._entry_px) / self._entry_atr
                if gain_atr >= self.params.trail_activate_atr:
                    cand = self._highest - self.params.long_trail_atr * eff_atr
                    if cand > self._stop_px:
                        self._stop_px = cand
            else:
                gain_atr = (self._entry_px - self._lowest) / self._entry_atr
                if gain_atr >= self.params.trail_activate_atr:
                    cand = self._lowest + self.params.short_trail_atr * eff_atr
                    if cand < self._stop_px:
                        self._stop_px = cand
            return None

        # 2) Flat -> Evaluate 4 Entry Gates + Tiered Distance Corridor
        allow_long = last_d_close > ema50_d and ema20_d > ema50_d
        allow_short = last_d_close < ema50_d and ema20_d < ema50_d
        if not allow_long and not allow_short:
            return None

        dist_atr_d = abs(bar.close - ema50_d) / atr_d
        if dist_atr_d <= self.params.min_dist_atr_d or dist_atr_d >= self.params.max_dist_atr_d:
            return None

        in_extended = dist_atr_d >= self.params.soft_dist_atr_d
        zone_mult = self.params.extended_size_mult if in_extended else Decimal("1.0")

        lb = self.params.channel_lookback
        prev_win = self._bars[-(lb + 1) : -1]
        h_15 = max(x.high for x in prev_win)
        l_15 = min(x.low for x in prev_win)
        prev_atr_1h = self._calc_atr(self._trs_1h[-15:-1], 14)
        upper_ch = h_15 + self.params.channel_atr_mult * prev_atr_1h
        lower_ch = l_15 - self.params.channel_atr_mult * prev_atr_1h

        closes_1h = [x.close for x in self._bars[-26:]]
        ema20_1h = ema(closes_1h, 20)
        rec6 = self._bars[-6:]
        pri6 = self._bars[-12:-6]

        bal = self.ctx.get_balance(bar.exchange, "USDT")
        eq_usdt = bal.total if bal is not None and bal.total > Decimal("0") else Decimal("10000")
        if self.params.use_atr_risk_sizing:
            risk_usdt = eq_usdt * self.params.risk_pct * zone_mult
            raw_qty = risk_usdt / (self.params.init_stop_atr * cur_atr_1h)
            max_qty = (eq_usdt * Decimal("0.85") * zone_mult) / bar.close
            qty = min(raw_qty, max_qty).quantize(step_q, rounding=ROUND_DOWN)
        else:
            qty = ((eq_usdt * Decimal("0.65") * zone_mult) / bar.close).quantize(step_q, rounding=ROUND_DOWN)
        if qty < step_q:
            qty = step_q

        if allow_long and bar.high > upper_ch:
            if min(x.low for x in rec6) >= min(x.low for x in pri6) and bar.close > self._bars[-2].close and bar.close > ema20_1h:
                self._entry_px = bar.close
                self._entry_atr = cur_atr_1h
                self._entry_bar_idx = idx
                self._highest = bar.high
                self._lowest = bar.low
                self._stop_px = bar.close - self.params.init_stop_atr * cur_atr_1h
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.BUY,
                        order_type=OrderType.MARKET,
                        quantity=qty,
                        reason="delphi_long_extended_tier" if in_extended else "delphi_long_core_corridor",
                        ts_ms=self.ctx.now_ms,
                    )
                ]

        if allow_short and bar.low < lower_ch:
            if max(x.high for x in rec6) <= max(x.high for x in pri6) and bar.close < self._bars[-2].close and bar.close < ema20_1h:
                self._entry_px = bar.close
                self._entry_atr = cur_atr_1h
                self._entry_bar_idx = idx
                self._highest = bar.high
                self._lowest = bar.low
                self._stop_px = bar.close + self.params.init_stop_atr * cur_atr_1h
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.SELL,
                        order_type=OrderType.MARKET,
                        quantity=qty,
                        reason="delphi_short_extended_tier" if in_extended else "delphi_short_core_corridor",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
        return None
'''


STRATEGY_SPECS = [
    {
        "strategy_id": "strat_delphi_v44",
        "name": "Delphi II v4.4（原版逻辑）",
        "description": "原版基准：真实24H日线聚合、无日线偏离上限(999)、空单移动止损0.5 ATR、实时ATR、固定65%名义仓位",
        "note": "原版基准已审核",
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "1.0",
            "soft_dist_atr_d": "999.0",
            "max_dist_atr_d": "999.0",
            "extended_size_mult": "1.0",
            "channel_lookback": 15,
            "channel_atr_mult": "0.45",
            "init_stop_atr": "3.5",
            "trail_activate_atr": "1.5",
            "long_trail_atr": "2.0",
            "short_trail_atr": "0.5",
            "lock_entry_atr": False,
            "enable_reversal_exit": False,
            "use_atr_risk_sizing": False,
            "risk_pct": "0.055",
        },
    },
    {
        "strategy_id": "strat_delphi_v45",
        "name": "Delphi II v4.5（一刀切风控版）",
        "description": "第一版保守优化：偏离>=3.5 ATR_D硬性禁开（防追顶但易踏空二段主升/瀑布）、空单1.8 ATR、2.5%保守风险预算",
        "note": "一刀切风控版已审核",
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "1.0",
            "soft_dist_atr_d": "3.5",
            "max_dist_atr_d": "3.5",
            "extended_size_mult": "1.0",
            "channel_lookback": 15,
            "channel_atr_mult": "0.45",
            "init_stop_atr": "3.5",
            "trail_activate_atr": "1.5",
            "long_trail_atr": "2.0",
            "short_trail_atr": "1.8",
            "lock_entry_atr": True,
            "enable_reversal_exit": True,
            "use_atr_risk_sizing": True,
            "risk_pct": "0.025",
        },
    },
    {
        "strategy_id": "strat_delphi_v46",
        "name": "Delphi II v4.6（分段仓位防踏空版）",
        "description": "终版增强：标准走廊[1.0, 3.8)满额风险预算 + 延续走廊[3.8, 8.5)按0.60x降仓防踏空 + 空单1.8 ATR + 锁开仓ATR + 反转/停滞离场",
        "note": "分段防踏空增强版已审核",
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "1.0",
            "soft_dist_atr_d": "3.8",
            "max_dist_atr_d": "8.5",
            "extended_size_mult": "0.60",
            "channel_lookback": 15,
            "channel_atr_mult": "0.45",
            "init_stop_atr": "3.5",
            "trail_activate_atr": "1.5",
            "long_trail_atr": "2.0",
            "short_trail_atr": "1.8",
            "lock_entry_atr": True,
            "enable_reversal_exit": True,
            "use_atr_risk_sizing": True,
            "risk_pct": "0.055",
        },
    },
]


def main() -> None:
    headers = {"X-API-Key": "admin-dev-token"}
    base = "http://127.0.0.1:8000"

    with httpx.Client(base_url=base, headers=headers, timeout=180.0) as c:
        existing = {s["strategy_id"] for s in c.get("/api/v1/strategies").json()}
        run_ids: dict[str, str] = {}

        for spec in STRATEGY_SPECS:
            sid = spec["strategy_id"]
            if sid not in existing:
                r = c.post(
                    "/api/v1/strategies/upload",
                    json={
                        "strategy_id": sid,
                        "name": spec["name"],
                        "description": spec["description"],
                        "code": DELPHI_STRATEGY_CODE,
                        "params": spec["params"],
                    },
                )
                r.raise_for_status()
                vid = r.json()["version_id"]
                c.post(f"/api/v1/strategies/versions/{vid}/approve", json={"note": spec["note"]}).raise_for_status()

            bt = c.post(
                "/api/v1/runs/launch",
                json={
                    "strategy_id": sid,
                    "version_id": f"{sid}:v1",
                    "env": "backtest",
                    "exchange": "binance",
                    "symbol": "SOL/USDT:USDT",
                    "initial_usdt": "10000",
                    "async_mode": False,
                },
            )
            bt.raise_for_status()
            run_ids[sid] = bt.json()["run_id"]

        # Run Parameter Sensitivity Heatmap on v4.6 across short_trail_atr x max_dist_atr_d
        sens = c.post(
            f"/api/v1/runs/backtest/{run_ids['strat_delphi_v46']}/sensitivity",
            json={
                "param_x_name": "short_trail_atr",
                "param_x_values": ["0.5", "1.0", "1.5", "1.8", "2.2"],
                "param_y_name": "max_dist_atr_d",
                "param_y_values": ["3.5", "8.5", "999.0"],
            },
        )
        sens.raise_for_status()

        strat_map = {s["strategy_id"]: s["latest_backtest_metrics"] for s in c.get("/api/v1/strategies").json()}
        summary_out = {}
        for sid, rid in run_ids.items():
            rep = c.get(f"/api/v1/runs/backtest/{rid}/report").json()
            summary_out[sid] = {
                "run_id": rid,
                "metrics": strat_map.get(sid),
                "summary_metrics": rep.get("summary_metrics"),
                "trade_metrics": rep.get("trade_metrics"),
                "weaknesses": rep.get("weaknesses"),
            }
        summary_out["sensitivity_cells"] = sens.json()["cells"]
        print(json.dumps(summary_out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
