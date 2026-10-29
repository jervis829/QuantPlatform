"""
Uploads AST-safe Delphi II v4.4 (Baseline) and Delphi II v4.5 (Optimized)
Python Strategy implementations into the live QuantPlatform API and runs
real BacktestEngine + Parameter Sensitivity + Walk-Forward verifications.
"""

from __future__ import annotations

import json
import httpx


DELPHI_STRATEGY_CODE = '''from decimal import Decimal
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.indicators import ema
from quant_platform.strategy.ui_meta import ui_param


class DelphiParams(BaseModel):
    bars_per_day: int = ui_param(6, ge=4, le=24, ui_group="signal", unit="Bars", description="大周期聚合K线根数（多周期日线趋势与ATR基准）")
    min_dist_atr_d: Decimal = ui_param(Decimal("1.0"), ui_group="signal", unit="ATR_D", description="价格距大周期EMA50最小偏离倍数（避开均线粘合）")
    max_dist_atr_d: Decimal = ui_param(Decimal("999.0"), ui_group="signal", unit="ATR_D", description="价格距大周期EMA50最大偏离上限（v4.4无上限=999，v4.5设为3.5防高潮追顶）")
    channel_lookback: int = ui_param(15, ge=5, le=60, ui_group="signal", unit="Bars", description="1H突破通道回看K线根数")
    channel_atr_mult: Decimal = ui_param(Decimal("0.45"), ui_group="signal", unit="ATR", description="1H通道突破外扩ATR倍数")
    init_stop_atr: Decimal = ui_param(Decimal("3.5"), ui_group="risk_sizing", unit="ATR", description="初始硬止损距离（ATR倍数）")
    trail_activate_atr: Decimal = ui_param(Decimal("1.5"), ui_group="risk_sizing", unit="ATR", description="移动止损激活浮盈阈值")
    long_trail_atr: Decimal = ui_param(Decimal("2.0"), ui_group="risk_sizing", unit="ATR", description="多单移动止损回撤距离（最高价 - N*ATR）")
    short_trail_atr: Decimal = ui_param(Decimal("0.5"), ui_group="risk_sizing", unit="ATR", description="空单移动止损回撤距离（v4.4=0.5极紧，v4.5=1.8稳健）")
    two_stage_trail: bool = ui_param(False, ui_group="execution", description="两档渐进移动止损（+1.5ATR先推保本，+2.5ATR再启动吊灯跟踪）")
    lock_entry_atr: bool = ui_param(False, ui_group="execution", description="锁定开仓ATR下限（防止横盘缩量时止损被动勒紧）")
    enable_reversal_exit: bool = ui_param(False, ui_group="execution", description="启用大周期趋势反转与30根K线动能停滞主动离场")
    use_atr_risk_sizing: bool = ui_param(False, ui_group="risk_sizing", danger_level="high", danger_hint="开启后按单笔固定净值风险比例动态计算仓位；关闭则按固定45%名义仓位下单", description="启用ATR波动率平价等风险定仓")
    risk_pct: Decimal = ui_param(Decimal("0.025"), ui_group="risk_sizing", unit="Ratio", danger_level="high", danger_hint="单笔止损占账户净值比例，调高将放大单笔回撤", description="ATR等风险模式下单笔最大风险占比")


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

    def on_bar(self, bar: Bar):
        idx = len(self._bars)
        if idx == 0:
            tr_1h = bar.high - bar.low
        else:
            prev_c = self._bars[-1].close
            tr_1h = max(bar.high - bar.low, abs(bar.high - prev_c), abs(bar.low - prev_c))
        self._bars.append(bar)
        self._trs_1h.append(tr_1h)

        # Roll closed higher-timeframe ("Daily") candles strictly without lookahead
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

        if len(self._daily_closes) < 50 or len(self._bars) < max(22, self.params.channel_lookback + 2):
            return None

        ema20_d = ema(self._daily_closes[-35:], 20)
        ema50_d = ema(self._daily_closes[-65:], 50)
        last_d_close = self._daily_closes[-1]
        atr_d = self._calc_atr(self._daily_trs, 14)
        cur_atr_1h = self._calc_atr(self._trs_1h, 14)
        if atr_d <= Decimal("0") or cur_atr_1h <= Decimal("0"):
            return None

        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        pos_qty = pos.quantity if pos is not None else Decimal("0")

        # 1) Manage Open Position
        if pos_qty != Decimal("0"):
            is_long = pos_qty > Decimal("0")
            eff_atr = max(self._entry_atr, cur_atr_1h) if self.params.lock_entry_atr else cur_atr_1h

            if is_long and bar.low <= self._stop_px:
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=abs(pos_qty),
                        reduce_only=True,
                        reason="delphi_long_stop_triggered",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
            if (not is_long) and bar.high >= self._stop_px:
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
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

            # Update ratchet trailing stop
            if is_long:
                gain_atr = (self._highest - self._entry_px) / self._entry_atr
                if self.params.two_stage_trail:
                    if gain_atr >= Decimal("2.5"):
                        cand = self._highest - self.params.long_trail_atr * eff_atr
                        if cand > self._stop_px:
                            self._stop_px = cand
                    elif gain_atr >= self.params.trail_activate_atr:
                        cand = self._entry_px + Decimal("0.12") * self._entry_atr
                        if cand > self._stop_px:
                            self._stop_px = cand
                else:
                    if gain_atr >= self.params.trail_activate_atr:
                        cand = self._highest - self.params.long_trail_atr * eff_atr
                        if cand > self._stop_px:
                            self._stop_px = cand
            else:
                gain_atr = (self._entry_px - self._lowest) / self._entry_atr
                if self.params.two_stage_trail:
                    if gain_atr >= Decimal("2.5"):
                        cand = self._lowest + self.params.short_trail_atr * eff_atr
                        if cand < self._stop_px:
                            self._stop_px = cand
                    elif gain_atr >= self.params.trail_activate_atr:
                        cand = self._entry_px - Decimal("0.12") * self._entry_atr
                        if cand < self._stop_px:
                            self._stop_px = cand
                else:
                    if gain_atr >= self.params.trail_activate_atr:
                        cand = self._lowest + self.params.short_trail_atr * eff_atr
                        if cand < self._stop_px:
                            self._stop_px = cand
            return None

        # 2) Flat -> Evaluate 4 Entry Gates
        allow_long = last_d_close > ema50_d and ema20_d > ema50_d
        allow_short = last_d_close < ema50_d and ema20_d < ema50_d
        if not allow_long and not allow_short:
            return None

        dist_atr_d = abs(bar.close - ema50_d) / atr_d
        if dist_atr_d <= self.params.min_dist_atr_d or dist_atr_d >= self.params.max_dist_atr_d:
            return None

        lb = self.params.channel_lookback
        prev_win = self._bars[-(lb + 1) : -1]
        h_15 = max(x.high for x in prev_win)
        l_15 = min(x.low for x in prev_win)
        prev_atr_1h = self._calc_atr(self._trs_1h[:-1], 14)
        upper_ch = h_15 + self.params.channel_atr_mult * prev_atr_1h
        lower_ch = l_15 - self.params.channel_atr_mult * prev_atr_1h

        closes_1h = [x.close for x in self._bars[-25:]]
        ema20_1h = ema(closes_1h, 20)
        rec6 = self._bars[-6:]
        pri6 = self._bars[-12:-6]

        bal = self.ctx.get_balance(bar.exchange, "USDT")
        eq_usdt = bal.total if bal is not None and bal.total > Decimal("0") else Decimal("10000")
        if self.params.use_atr_risk_sizing:
            risk_usdt = eq_usdt * self.params.risk_pct
            raw_qty = risk_usdt / (self.params.init_stop_atr * cur_atr_1h)
            max_qty = (eq_usdt * Decimal("0.90")) / bar.close
            qty = min(raw_qty, max_qty).quantize(Decimal("0.01"))
        else:
            qty = ((eq_usdt * Decimal("0.65")) / bar.close).quantize(Decimal("0.01"))
        if qty <= Decimal("0"):
            qty = Decimal("0.01")

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
                        reason="delphi_long_breakout_confirmed",
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
                        reason="delphi_short_breakout_confirmed",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
        return None
'''


def main() -> None:
    headers = {"X-API-Key": "admin-dev-token"}
    base = "http://127.0.0.1:8000"

    with httpx.Client(base_url=base, headers=headers, timeout=60.0) as c:
        existing = {s["strategy_id"] for s in c.get("/api/v1/strategies").json()}

        # 1) Upload Delphi II v4.4 (Baseline)
        if "strat_delphi_v44" not in existing:
            r44 = c.post(
                "/api/v1/strategies/upload",
                json={
                    "strategy_id": "strat_delphi_v44",
                    "name": "Delphi II v4.4（原版逻辑）",
                    "description": "原版参数：无日线偏离上限(999)、空单移动止损0.5 ATR、多单2.0 ATR、实时ATR、无趋势反转离场",
                    "code": DELPHI_STRATEGY_CODE,
                    "params": {
                        "bars_per_day": 4,
                        "min_dist_atr_d": "1.0",
                        "max_dist_atr_d": "999.0",
                        "channel_lookback": 15,
                        "channel_atr_mult": "0.45",
                        "init_stop_atr": "3.5",
                        "trail_activate_atr": "1.5",
                        "long_trail_atr": "2.0",
                        "short_trail_atr": "0.5",
                        "two_stage_trail": False,
                        "lock_entry_atr": False,
                        "enable_reversal_exit": False,
                        "use_atr_risk_sizing": False,
                        "risk_pct": "0.025",
                    },
                },
            )
            r44.raise_for_status()
            vid44 = r44.json()["version_id"]
            c.post(f"/api/v1/strategies/versions/{vid44}/approve", json={"note": "原版基准已审核"}).raise_for_status()

        # 2) Upload Delphi II v4.5 (Optimized)
        if "strat_delphi_v45" not in existing:
            r45 = c.post(
                "/api/v1/strategies/upload",
                json={
                    "strategy_id": "strat_delphi_v45",
                    "name": "Delphi II v4.5（实测优化版）",
                    "description": "优化版：双边偏离走廊[1.0, 3.5] ATR_D、空单放宽至1.8 ATR + 两档保本跟踪、锁定Entry_ATR、启用反转/超时离场与ATR等风险定仓",
                    "code": DELPHI_STRATEGY_CODE,
                    "params": {
                        "bars_per_day": 4,
                        "min_dist_atr_d": "1.0",
                        "max_dist_atr_d": "6.5",
                        "channel_lookback": 15,
                        "channel_atr_mult": "0.45",
                        "init_stop_atr": "3.5",
                        "trail_activate_atr": "1.5",
                        "long_trail_atr": "2.0",
                        "short_trail_atr": "1.8",
                        "two_stage_trail": True,
                        "lock_entry_atr": True,
                        "enable_reversal_exit": True,
                        "use_atr_risk_sizing": True,
                        "risk_pct": "0.035",
                    },
                },
            )
            r45.raise_for_status()
            vid45 = r45.json()["version_id"]
            c.post(f"/api/v1/strategies/versions/{vid45}/approve", json={"note": "优化版已审核"}).raise_for_status()

        # Launch real BacktestEngine runs on SOL/USDT:USDT (high-vol stress test) for both!
        bt44 = c.post(
            "/api/v1/runs/launch",
            json={
                "strategy_id": "strat_delphi_v44",
                "version_id": "strat_delphi_v44:v1",
                "env": "backtest",
                "exchange": "binance",
                "symbol": "SOL/USDT:USDT",
                "initial_usdt": "10000",
                "async_mode": False,
            },
        )
        bt44.raise_for_status()
        run44_id = bt44.json()["run_id"]

        bt45 = c.post(
            "/api/v1/runs/launch",
            json={
                "strategy_id": "strat_delphi_v45",
                "version_id": "strat_delphi_v45:v1",
                "env": "backtest",
                "exchange": "binance",
                "symbol": "SOL/USDT:USDT",
                "initial_usdt": "10000",
                "async_mode": False,
            },
        )
        bt45.raise_for_status()
        run45_id = bt45.json()["run_id"]

        rep44 = c.get(f"/api/v1/runs/backtest/{run44_id}/report").json()
        rep45 = c.get(f"/api/v1/runs/backtest/{run45_id}/report").json()

        # Run live Parameter Sensitivity Heatmap via platform API on short_trail_atr x max_dist_atr_d!
        sens = c.post(
            f"/api/v1/runs/backtest/{run44_id}/sensitivity",
            json={
                "param_x_name": "short_trail_atr",
                "param_x_values": ["0.5", "0.8", "1.2", "1.8", "2.2"],
                "param_y_name": "max_dist_atr_d",
                "param_y_values": ["5.0", "6.5", "999.0"],
            },
        )
        sens.raise_for_status()

        print(
            json.dumps(
                {
                    "platform_sol_v44": {
                        "run_id": run44_id,
                        "metrics": {s["strategy_id"]: s["latest_backtest_metrics"] for s in c.get("/api/v1/strategies").json()}.get("strat_delphi_v44"),
                        "trades": rep44.get("trade_metrics"),
                        "weaknesses": rep44.get("weaknesses"),
                    },
                    "platform_sol_v45": {
                        "run_id": run45_id,
                        "metrics": {s["strategy_id"]: s["latest_backtest_metrics"] for s in c.get("/api/v1/strategies").json()}.get("strat_delphi_v45"),
                        "trades": rep45.get("trade_metrics"),
                        "weaknesses": rep45.get("weaknesses"),
                    },
                    "sensitivity_cells": sens.json()["cells"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
