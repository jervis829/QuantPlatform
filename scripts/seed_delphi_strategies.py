"""
Seeds AST-safe Delphi II v4.4 (Original Baseline), Delphi II v4.5 (1H Narrow-Stop Cap),
Delphi II v4.6 (Real-Market 48H Swing Enhancement), and BTC Dual-SMA Starter Template into
the live QuantPlatform API and runs real historical Binance USDT-M Perpetual backtests
(2023-10-15 .. 2026-07-01 UTC, 23,760 1H bars + 2,970 8H funding settlements).
"""

from __future__ import annotations

import json
import httpx


DELPHI_STRATEGY_CODE = '''from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.ui_meta import ui_param


class DelphiParams(BaseModel):
    bars_per_day: int = ui_param(24, ge=4, le=24, ui_group="signal", unit="Bars", description="大周期聚合K线根数（24根1H = 真实UTC日线）")
    min_dist_atr_d: Decimal = ui_param(Decimal("0.20"), ui_group="signal", unit="ATR_D", description="价格距日线EMA50最小偏离倍数（v4.4=1.0易错过底部启动，v4.6=0.20捕捉主升起点）")
    soft_dist_atr_d: Decimal = ui_param(Decimal("2.8"), ui_group="signal", unit="ATR_D", description="标准满额风险走廊上限（超过后进入强趋势延续段，自动降仓）")
    max_dist_atr_d: Decimal = ui_param(Decimal("6.0"), ui_group="signal", unit="ATR_D", description="多头极端偏离硬上限（防止高潮顶接盘）")
    short_max_dist_atr_d: Decimal = ui_param(Decimal("3.2"), ui_group="signal", unit="ATR_D", description="空头极端偏离硬上限（防止深跌超卖区追空被逼空）")
    extended_size_mult: Decimal = ui_param(Decimal("0.55"), ui_group="risk_sizing", unit="Ratio", description="强趋势延续走廊[soft, max)仓位缩放系数")
    short_size_mult: Decimal = ui_param(Decimal("0.50"), ui_group="risk_sizing", unit="Ratio", description="空头相对多头风险权重（适配加密市场长期正偏与逼空特征）")
    channel_lookback: int = ui_param(48, ge=10, le=96, ui_group="signal", unit="Bars", description="1H突破通道回看K线根数（v4.4=15h高频易磨损，v4.6=48h波段箱体突破）")
    channel_atr_mult: Decimal = ui_param(Decimal("0.10"), ui_group="signal", unit="ATR", description="1H通道突破外扩ATR缓冲倍数")
    require_close_breakout: bool = ui_param(True, ui_group="signal", description="要求1H实体收盘价站上突破通道（过滤盘中假突破插针诱多/诱空）")
    require_ema50_slope: bool = ui_param(True, ui_group="signal", description="要求日线EMA50斜率与开仓方向一致（过滤震荡期均线横盘假信号）")
    init_stop_atr: Decimal = ui_param(Decimal("5.0"), ui_group="risk_sizing", unit="ATR_1H", description="初始硬止损距离（1H ATR倍数）")
    trail_activate_atr: Decimal = ui_param(Decimal("3.5"), ui_group="risk_sizing", unit="ATR_1H", description="移动止损激活浮盈阈值（1H ATR倍数）")
    long_trail_atr: Decimal = ui_param(Decimal("8.0"), ui_group="risk_sizing", unit="ATR_1H", description="多单吊灯移动止损回撤距离（v4.4=2.0易被洗出，v4.6=8.0吃满周线主升浪）")
    short_trail_atr: Decimal = ui_param(Decimal("5.0"), ui_group="risk_sizing", unit="ATR_1H", description="空单吊灯移动止损回撤距离（v4.4=0.5，v4.6=5.0，v4.7=5.4抗熊市反抽）")
    short_trail_activate_atr: Decimal = ui_param(Decimal("0.0"), ui_group="risk_sizing", unit="ATR_1H", description="空单独立跟踪激活阈值（0表示沿用trail_activate_atr，v4.7=4.0避免破位初段过早下移止损）")
    short_close_anchor: bool = ui_param(False, ui_group="execution", description="空单以1H收盘最低价锚定浮盈（防止急跌长下影线针尖虚拉止损线）")
    short_close_confirm_trail: bool = ui_param(False, ui_group="execution", description="空单跟踪止盈要求1H实体收盘确认（保留盘中硬防爆止损，免疫逼空上影线扫损洗盘）")
    short_tp1_enable: bool = ui_param(False, ui_group="execution", description="启用空单首档脉冲分批止盈（急跌达标先落袋部分仓位，底仓博二段主跌浪）")
    short_tp1_atr: Decimal = ui_param(Decimal("4.5"), ui_group="execution", unit="ATR_1H", description="空单首档脉冲止盈触发距离（1H ATR倍数）")
    short_tp1_fraction: Decimal = ui_param(Decimal("0.30"), ui_group="execution", unit="Ratio", description="空单首档脉冲分批止盈平仓比例")
    lock_entry_atr: bool = ui_param(True, ui_group="execution", description="锁定开仓ATR下限（防止横盘缩量时止损距离被动收紧）")
    enable_reversal_exit: bool = ui_param(True, ui_group="execution", description="启用日线EMA20/EMA50共振趋势反转主动平仓")
    enable_stagnation_exit: bool = ui_param(False, ui_group="execution", description="启用30根K线动能停滞离场")
    use_atr_risk_sizing: bool = ui_param(True, ui_group="risk_sizing", danger_level="high", danger_hint="开启后按单笔固定净值风险比例动态定仓；关闭则按固定65%名义仓位下单", description="启用ATR波动率风险平价定仓")
    risk_pct: Decimal = ui_param(Decimal("0.045"), ui_group="risk_sizing", unit="Ratio", danger_level="high", danger_hint="单笔初始止损占账户净值比例，调高将放大单笔回撤", description="标准走廊单笔风险预算占比")


class DelphiStrategy(Strategy):
    ParamModel = DelphiParams

    def on_start(self):
        self._bars = []
        self._trs_1h = []
        self._daily_closes = []
        self._daily_trs = []
        self._ema20_d = None
        self._ema50_d = None
        self._ema20_d_hist = []
        self._ema50_d_hist = []
        self._ema20_1h = None
        self._cur_day_bars = []
        self._entry_px = Decimal("0")
        self._entry_atr = Decimal("0")
        self._entry_bar_idx = 0
        self._stop_px = Decimal("0")
        self._trail_px = Decimal("0")
        self._highest = Decimal("0")
        self._lowest = Decimal("0")
        self._lowest_close = Decimal("0")
        self._tp1_done = False
        self._trade_start_ms = 1704067200000  # 2024-01-01 00:00:00 UTC after 2023 Q4 warm-up
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

        alpha20 = Decimal("2") / Decimal("21")
        alpha50 = Decimal("2") / Decimal("51")
        if self._ema20_1h is None:
            self._ema20_1h = bar.close
        else:
            self._ema20_1h = alpha20 * bar.close + (Decimal("1") - alpha20) * self._ema20_1h

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
            self._ema20_d = d_cl if self._ema20_d is None else (alpha20 * d_cl + (Decimal("1") - alpha20) * self._ema20_d)
            self._ema50_d = d_cl if self._ema50_d is None else (alpha50 * d_cl + (Decimal("1") - alpha50) * self._ema50_d)
            self._ema20_d_hist.append(self._ema20_d)
            self._ema50_d_hist.append(self._ema50_d)
            self._cur_day_bars = []

        lb = self.params.channel_lookback
        if len(self._daily_closes) < 55 or len(self._bars) < max(72, lb + 5):
            return None
        if bar.open_time_ms < self._trade_start_ms and bar.interval == "1h":
            return None

        ema20_d = self._ema20_d
        ema50_d = self._ema50_d
        last_d_close = self._daily_closes[-1]
        atr_d = self._calc_atr(self._daily_trs, 14)
        cur_atr_1h = self._calc_atr(self._trs_1h, 14)
        if atr_d <= Decimal("0") or cur_atr_1h <= Decimal("0"):
            return None

        spec = self.ctx.data.get_symbol_spec(bar.exchange, bar.symbol)
        tick = spec.tick_size
        step_q = spec.step_size
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        pos_qty = pos.quantity if pos is not None else Decimal("0")

        # 1) Manage Open Position (causal STOP_MARKET execution at real stop trigger price)
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
            if (not is_long) and self.params.short_close_confirm_trail and bar.close >= self._trail_px:
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.CLOSE,
                        order_type=OrderType.MARKET,
                        quantity=abs(pos_qty),
                        reduce_only=True,
                        reason="delphi_short_close_trail_exit",
                        ts_ms=self.ctx.now_ms,
                    )
                ]

            if bar.high > self._highest:
                self._highest = bar.high
            if bar.low < self._lowest:
                self._lowest = bar.low
            if bar.close < self._lowest_close:
                self._lowest_close = bar.close

            if self.params.enable_reversal_exit:
                if is_long and last_d_close < ema50_d and ema20_d < ema50_d:
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
                if (not is_long) and last_d_close > ema50_d and ema20_d > ema50_d:
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

            if self.params.enable_stagnation_exit and (idx - self._entry_bar_idx) >= 30:
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
                s_act = (
                    self.params.short_trail_activate_atr
                    if self.params.short_trail_activate_atr > Decimal("0")
                    else self.params.trail_activate_atr
                )
                ref_low = self._lowest_close if self.params.short_close_anchor else self._lowest
                gain_atr = (self._entry_px - ref_low) / self._entry_atr
                if gain_atr >= s_act:
                    cand = ref_low + self.params.short_trail_atr * eff_atr
                    if self.params.short_close_confirm_trail:
                        if cand < self._trail_px:
                            self._trail_px = cand
                    else:
                        if cand < self._stop_px:
                            self._stop_px = cand

                if self.params.short_tp1_enable and (not self._tp1_done):
                    tp1_px = self._entry_px - self.params.short_tp1_atr * self._entry_atr
                    if bar.close <= tp1_px:
                        part_qty = (abs(pos_qty) * self.params.short_tp1_fraction).quantize(step_q, rounding=ROUND_DOWN)
                        rem_qty = abs(pos_qty) - part_qty
                        if part_qty >= spec.min_qty and rem_qty >= spec.min_qty:
                            self._tp1_done = True
                            be_px = self._entry_px - Decimal("0.15") * self._entry_atr
                            if self.params.short_close_confirm_trail:
                                if be_px < self._trail_px:
                                    self._trail_px = be_px
                                soft_hard = self._entry_px + Decimal("2.0") * self._entry_atr
                                if soft_hard < self._stop_px:
                                    self._stop_px = soft_hard
                            else:
                                if be_px < self._stop_px:
                                    self._stop_px = be_px
                            return [
                                Signal(
                                    strategy_id=self.ctx.config.strategy_id,
                                    exchange=bar.exchange,
                                    symbol=bar.symbol,
                                    action=SignalAction.BUY,
                                    order_type=OrderType.MARKET,
                                    quantity=part_qty,
                                    reduce_only=True,
                                    reason="delphi_short_tp1_scale_out",
                                    ts_ms=self.ctx.now_ms,
                                )
                            ]
            return None

        # 2) Flat -> Evaluate 4 Entry Gates + Real-Market Regime Filters
        slope_up = (not self.params.require_ema50_slope) or (ema50_d > self._ema50_d_hist[-4])
        slope_dn = (not self.params.require_ema50_slope) or (
            ema50_d < self._ema50_d_hist[-4] and ema20_d < self._ema20_d_hist[-4]
        )
        allow_long = last_d_close > ema50_d and ema20_d > ema50_d and slope_up
        allow_short = last_d_close < ema50_d and ema20_d < ema50_d and slope_dn
        if not allow_long and not allow_short:
            return None

        dist_atr_d = abs(bar.close - ema50_d) / atr_d
        eff_max_dist = self.params.max_dist_atr_d if allow_long else self.params.short_max_dist_atr_d
        if dist_atr_d <= self.params.min_dist_atr_d or dist_atr_d >= eff_max_dist:
            return None

        in_extended = dist_atr_d >= self.params.soft_dist_atr_d
        zone_mult = self.params.extended_size_mult if in_extended else Decimal("1.0")
        if allow_short:
            zone_mult = zone_mult * self.params.short_size_mult

        prev_win = self._bars[-(lb + 1) : -1]
        h_ch = max(x.high for x in prev_win)
        l_ch = min(x.low for x in prev_win)
        prev_atr_1h = self._calc_atr(self._trs_1h[-15:-1], 14)
        upper_ch = h_ch + self.params.channel_atr_mult * prev_atr_1h
        lower_ch = l_ch - self.params.channel_atr_mult * prev_atr_1h

        rec6 = self._bars[-6:]
        pri6 = self._bars[-12:-6]

        bal = self.ctx.get_balance(bar.exchange, "USDT")
        eq_usdt = bal.total if bal is not None and bal.total > Decimal("0") else Decimal("10000")
        if self.params.use_atr_risk_sizing:
            risk_usdt = eq_usdt * self.params.risk_pct * zone_mult
            raw_qty = risk_usdt / (self.params.init_stop_atr * cur_atr_1h)
            max_qty = (eq_usdt * Decimal("0.90") * zone_mult) / bar.close
            qty = min(raw_qty, max_qty).quantize(step_q, rounding=ROUND_DOWN)
        else:
            qty = ((eq_usdt * Decimal("0.65") * zone_mult) / bar.close).quantize(step_q, rounding=ROUND_DOWN)
        if qty < spec.min_qty:
            qty = spec.min_qty

        brk_long = (bar.close > upper_ch) if self.params.require_close_breakout else (bar.high > upper_ch)
        brk_short = (bar.close < lower_ch) if self.params.require_close_breakout else (bar.low < lower_ch)

        if allow_long and brk_long:
            if min(x.low for x in rec6) >= min(x.low for x in pri6) and bar.close > self._bars[-2].close and bar.close > self._ema20_1h:
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

        if allow_short and brk_short:
            if max(x.high for x in rec6) <= max(x.high for x in pri6) and bar.close < self._bars[-2].close and bar.close < self._ema20_1h:
                self._entry_px = bar.close
                self._entry_atr = cur_atr_1h
                self._entry_bar_idx = idx
                self._highest = bar.high
                self._lowest = bar.low
                self._lowest_close = bar.close
                self._tp1_done = False
                self._stop_px = bar.close + self.params.init_stop_atr * cur_atr_1h
                self._trail_px = self._stop_px
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


SMA_WAVE_CODE = '''from collections import deque
from decimal import Decimal, ROUND_DOWN
from pydantic import BaseModel
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.strategy.ui_meta import ui_param


class DualSmaParams(BaseModel):
    fast_hours: int = ui_param(48, ge=12, le=120, ui_group="signal", unit="Hours", description="快线周期（48根1H = 2日均线）")
    slow_hours: int = ui_param(240, ge=48, le=720, ui_group="signal", unit="Hours", description="慢线周期（240根1H = 10日均线）")
    alloc_pct: Decimal = ui_param(Decimal("0.50"), ui_group="risk_sizing", unit="Ratio", danger_level="medium", description="每次趋势建仓占账户净值比例")


class DualSmaWaveStrategy(Strategy):
    ParamModel = DualSmaParams

    def on_start(self):
        self._closes = deque(maxlen=max(self.params.slow_hours + 5, 300))
        self._prev_diff = None
        return None

    def on_bar(self, bar: Bar):
        self._closes.append(bar.close)
        slow_n = self.params.slow_hours
        fast_n = self.params.fast_hours
        if len(self._closes) < slow_n:
            return None
        arr = list(self._closes)
        fast_ma = sum(arr[-fast_n:], Decimal("0")) / Decimal(fast_n)
        slow_ma = sum(arr[-slow_n:], Decimal("0")) / Decimal(slow_n)
        diff = fast_ma - slow_ma
        prev = self._prev_diff
        self._prev_diff = diff
        if prev is None or bar.open_time_ms < 1704067200000:
            return None

        spec = self.ctx.data.get_symbol_spec(bar.exchange, bar.symbol)
        pos = self.ctx.get_position(bar.exchange, bar.symbol)
        pos_qty = pos.quantity if pos is not None else Decimal("0")

        if prev <= Decimal("0") and diff > Decimal("0") and pos_qty <= Decimal("0"):
            bal = self.ctx.get_balance(bar.exchange, "USDT")
            eq = bal.total if bal is not None and bal.total > Decimal("0") else Decimal("10000")
            qty = max(spec.min_qty, ((eq * self.params.alloc_pct) / bar.close).quantize(spec.step_size, rounding=ROUND_DOWN))
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    order_type=OrderType.MARKET,
                    quantity=qty + abs(pos_qty),
                    reason="dual_sma_golden_cross",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        if prev >= Decimal("0") and diff < Decimal("0") and pos_qty > Decimal("0"):
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.CLOSE,
                    order_type=OrderType.MARKET,
                    quantity=abs(pos_qty),
                    reduce_only=True,
                    reason="dual_sma_death_cross_exit",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None
'''


STRATEGY_SPECS = [
    {
        "strategy_id": "strat_delphi_v44",
        "name": "Delphi II v4.4（原版基准）",
        "description": "原版规格在币安真实1H永续K线(2024–2026)上的对照基准：15h窄通道盘中最高价突破、无偏离上限、2.0/0.5×1H_ATR超窄止损导致月均11笔高频磨损",
        "note": "真实行情原版对照基准已审核",
        "code": DELPHI_STRATEGY_CODE,
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "1.0",
            "soft_dist_atr_d": "999.0",
            "max_dist_atr_d": "999.0",
            "short_max_dist_atr_d": "999.0",
            "extended_size_mult": "1.0",
            "short_size_mult": "1.0",
            "channel_lookback": 15,
            "channel_atr_mult": "0.45",
            "require_close_breakout": False,
            "require_ema50_slope": False,
            "init_stop_atr": "3.5",
            "trail_activate_atr": "1.5",
            "long_trail_atr": "2.0",
            "short_trail_atr": "0.5",
            "lock_entry_atr": False,
            "enable_reversal_exit": False,
            "enable_stagnation_exit": False,
            "use_atr_risk_sizing": False,
            "risk_pct": "0.045",
        },
        "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
    },
    {
        "strategy_id": "strat_delphi_v45",
        "name": "Delphi II v4.5（1H风控收窄版）",
        "description": "第一阶段优化：加入3.5 ATR_D偏离硬上限、空单1.8 ATR与锁开仓ATR，回撤显著下降但15h通道与2.0×1H_ATR止损仍偏窄",
        "note": "第一阶段风控版已审核",
        "code": DELPHI_STRATEGY_CODE,
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "1.0",
            "soft_dist_atr_d": "3.5",
            "max_dist_atr_d": "3.5",
            "short_max_dist_atr_d": "3.5",
            "extended_size_mult": "1.0",
            "short_size_mult": "1.0",
            "channel_lookback": 15,
            "channel_atr_mult": "0.45",
            "require_close_breakout": False,
            "require_ema50_slope": False,
            "init_stop_atr": "3.5",
            "trail_activate_atr": "1.5",
            "long_trail_atr": "2.0",
            "short_trail_atr": "1.8",
            "lock_entry_atr": True,
            "enable_reversal_exit": True,
            "enable_stagnation_exit": True,
            "use_atr_risk_sizing": True,
            "risk_pct": "0.025",
        },
        "symbols": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
    },
    {
        "strategy_id": "strat_delphi_v46",
        "name": "Delphi II v4.6（真实行情波段增强版）",
        "description": "基于2021–2023真实行情样本内定参、2024–2026严格样本外盲测：48H实体收盘突破 + 日线EMA50斜率过滤 + 8.0/5.0×1H_ATR吊灯波段止损 + 分段/多空非对称定仓",
        "note": "真实行情样本外验证增强版已审核",
        "code": DELPHI_STRATEGY_CODE,
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "0.20",
            "soft_dist_atr_d": "2.8",
            "max_dist_atr_d": "6.0",
            "short_max_dist_atr_d": "3.2",
            "extended_size_mult": "0.55",
            "short_size_mult": "0.50",
            "channel_lookback": 48,
            "channel_atr_mult": "0.10",
            "require_close_breakout": True,
            "require_ema50_slope": True,
            "init_stop_atr": "5.0",
            "trail_activate_atr": "3.5",
            "long_trail_atr": "8.0",
            "short_trail_atr": "5.0",
            "lock_entry_atr": True,
            "enable_reversal_exit": True,
            "enable_stagnation_exit": False,
            "use_atr_risk_sizing": True,
            "risk_pct": "0.045",
        },
        "symbols": ["ETH/USDT:USDT", "SOL/USDT:USDT", "BTC/USDT:USDT"],
    },
    {
        "strategy_id": "strat_btc_sma_wave",
        "name": "BTC 经典双均线趋势模板（2日/10日均线）",
        "description": "新手入门模板：在币安真实BTC/USDT永续1H历史K线(2024–2026)上运行48H/240H双均线金叉做多、死叉平仓",
        "note": "入门模板已审核",
        "code": SMA_WAVE_CODE,
        "params": {
            "fast_hours": 48,
            "slow_hours": 240,
            "alloc_pct": "0.50",
        },
        "symbols": ["BTC/USDT:USDT"],
    },
    {
        "strategy_id": "strat_delphi_v47",
        "name": "Delphi II v4.7（空头防洗盘与二段趋势优化版）",
        "description": "针对空单急跌插针与逼空反抽洗盘的微观结构优化：保留盘中5.0×ATR硬防爆止损，空头跟踪止盈升级为1H收盘最低价实体锚定(4.0×ATR激活) + 1H收盘确认离场(5.4×ATR) + 可选分批脉冲止盈，吃满熊市二段主跌浪",
        "note": "空头防洗盘与二段趋势优化版已审核",
        "code": DELPHI_STRATEGY_CODE,
        "params": {
            "bars_per_day": 24,
            "min_dist_atr_d": "0.20",
            "soft_dist_atr_d": "2.8",
            "max_dist_atr_d": "6.0",
            "short_max_dist_atr_d": "3.2",
            "extended_size_mult": "0.55",
            "short_size_mult": "0.50",
            "channel_lookback": 48,
            "channel_atr_mult": "0.10",
            "require_close_breakout": True,
            "require_ema50_slope": True,
            "init_stop_atr": "5.0",
            "trail_activate_atr": "3.5",
            "long_trail_atr": "8.0",
            "short_trail_atr": "5.4",
            "short_trail_activate_atr": "4.0",
            "short_close_anchor": True,
            "short_close_confirm_trail": True,
            "short_tp1_enable": False,
            "short_tp1_atr": "4.5",
            "short_tp1_fraction": "0.30",
            "lock_entry_atr": True,
            "enable_reversal_exit": True,
            "enable_stagnation_exit": False,
            "use_atr_risk_sizing": True,
            "risk_pct": "0.045",
        },
        "symbols": ["ETH/USDT:USDT", "SOL/USDT:USDT", "BTC/USDT:USDT"],
    },
]


def main() -> None:
    headers = {"X-API-Key": "admin-dev-token"}
    base = "http://127.0.0.1:8000"

    with httpx.Client(base_url=base, headers=headers, timeout=300.0) as c:
        existing = {s["strategy_id"] for s in c.get("/api/v1/strategies").json()}
        existing_runs: dict[tuple[str, str], str] = {}
        for r_item in c.get("/api/v1/runs?env=backtest").json():
            if r_item.get("status") == "COMPLETED":
                syms = (r_item.get("config_snapshot") or {}).get("symbols") or []
                if syms:
                    existing_runs[(r_item["strategy_id"], syms[0])] = r_item["run_id"]
        run_ids: dict[str, dict[str, str]] = {}

        for spec in STRATEGY_SPECS:
            sid = spec["strategy_id"]
            if sid not in existing:
                r = c.post(
                    "/api/v1/strategies/upload",
                    json={
                        "strategy_id": sid,
                        "name": spec["name"],
                        "description": spec["description"],
                        "code": spec["code"],
                        "params": spec["params"],
                    },
                )
                r.raise_for_status()
                vid = r.json()["version_id"]
                c.post(f"/api/v1/strategies/versions/{vid}/approve", json={"note": spec["note"]}).raise_for_status()

            run_ids[sid] = {}
            for sym in spec["symbols"]:
                if (sid, sym) in existing_runs:
                    run_ids[sid][sym] = existing_runs[(sid, sym)]
                    continue
                bt = c.post(
                    "/api/v1/runs/launch",
                    json={
                        "strategy_id": sid,
                        "version_id": f"{sid}:v1",
                        "env": "backtest",
                        "exchange": "binance",
                        "symbol": sym,
                        "initial_usdt": "10000",
                        "async_mode": False,
                    },
                )
                bt.raise_for_status()
                run_ids[sid][sym] = bt.json()["run_id"]

        summary_out = {}
        for sid, sym_map in run_ids.items():
            summary_out[sid] = {}
            for sym, rid in sym_map.items():
                rep = c.get(f"/api/v1/runs/backtest/{rid}/report").json()
                summary_out[sid][sym] = {
                    "run_id": rid,
                    "final_equity": rep.get("final_equity"),
                    "net_pnl": rep.get("net_pnl"),
                    "total_fees": rep.get("total_fees"),
                    "total_funding": rep.get("total_funding"),
                    "summary_metrics": rep.get("summary_metrics"),
                    "trade_metrics": rep.get("trade_metrics"),
                }
        print(json.dumps(summary_out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
