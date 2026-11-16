"""
Shadow Performance Analyzer — 模拟盘交易记录分析器（持续进化闭环的「分析」环节）

Reads authentic fills / balance snapshots / equity curve from the unified ledger
(env + run_id) for a shadow (or live) run, reconstructs round-trip trades via
FIFO lot matching, and produces a structured diagnosis that feeds the next
evolution round of AutoResearchCommittee.

Diagnosis tags map 1:1 onto structural code mutations understood by
QuantCoderAgent:
    OVER_TRADING        -> WIDEN_HORIZON_COOLDOWN
    FEE_DRAG_HIGH       -> WIDEN_HORIZON_COOLDOWN
    DRAWDOWN_BREACH     -> ADD_MACRO_TREND_FILTER + ADD_STOP_AND_TRAILING
    WIN_RATE_LOW        -> ADD_MACRO_TREND_FILTER
    PROFIT_FACTOR_LOW   -> ADD_STOP_AND_TRAILING / WIDEN_PROFIT_TARGET
    HOLD_TOO_LONG       -> ADD_STOP_AND_TRAILING
    HEALTHY             -> explore next structural upgrade
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.backtest.report import _reconstruct_round_trip_trades
from quant_platform.core.models import Fill, LiquidityRole, OrderSide, RunEnv
from quant_platform.db.schema import (
    BalanceSnapshotRecord,
    EquityCurveRecord,
    FillRecord,
    RunOrmRecord,
)

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000


class ShadowPerformanceReport(BaseModel):
    run_id: str
    env: str
    strategy_id: str
    strategy_version_id: str
    symbol: str
    exchange: str
    status: str
    started_at_ms: int
    ended_at_ms: int | None = None
    observation_days: float = 0.0

    fill_count: int = 0
    closed_trade_count: int = 0
    total_realized_pnl_usdt: str = "0"
    total_fees_usdt: str = "0"
    total_funding_usdt: str = "0"
    fee_drag_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_usdt: float = 0.0
    avg_win_usdt: float = 0.0
    avg_loss_usdt: float = 0.0
    max_drawdown_pct: float = 0.0
    trades_per_day: float = 0.0
    avg_holding_hours: float = 0.0
    open_position_qty: str = "0"

    diagnosis_tags: list[str] = Field(default_factory=list)
    diagnosis_summary_zh: str = ""
    recommended_mutations: list[str] = Field(default_factory=list)
    closed_trades: list[dict[str, Any]] = Field(default_factory=list)


def _fill_record_to_domain(rec: FillRecord) -> Fill:
    return Fill(
        fill_id=rec.fill_id,
        env=RunEnv(rec.env),
        run_id=rec.run_id,
        strategy_id=rec.strategy_id,
        client_order_id=rec.client_order_id,
        exchange_order_id=rec.exchange_order_id,
        exchange_trade_id=rec.exchange_trade_id,
        exchange=rec.exchange,
        symbol=rec.symbol,
        side=OrderSide(rec.side),
        liquidity=LiquidityRole(rec.liquidity),
        price=rec.price,
        quantity=rec.quantity,
        quote_qty=rec.quote_qty,
        reference_price=rec.reference_price,
        slippage_bps=rec.slippage_bps,
        fee=rec.fee,
        fee_asset=rec.fee_asset,
        realized_pnl=rec.realized_pnl,
        mark_price=rec.mark_price,
        snapshot_ref=rec.snapshot_ref,
        exchange_ts_ms=rec.exchange_ts_ms,
        local_recv_ts_ms=rec.local_recv_ts_ms,
    )


class ShadowPerformanceAnalyzer:
    """Analyzes shadow/live run fills from the unified ledger into an actionable diagnosis."""

    def analyze(
        self,
        *,
        session: Session,
        run_id: str,
        env: str = "shadow",
    ) -> ShadowPerformanceReport:
        run_row = session.execute(
            select(RunOrmRecord).where(RunOrmRecord.env == env, RunOrmRecord.run_id == run_id)
        ).scalar_one_or_none()
        if run_row is None:
            raise KeyError(f"Run {env}:{run_id} not found")

        cfg_snap = run_row.config_snapshot_json or {}
        symbols = cfg_snap.get("symbols") or []
        exchanges = cfg_snap.get("exchanges") or []
        symbol = symbols[0] if symbols else ""
        exchange = exchanges[0] if exchanges else ""

        fill_recs = list(
            session.execute(
                select(FillRecord)
                .where(FillRecord.env == env, FillRecord.run_id == run_id)
                .order_by(FillRecord.exchange_ts_ms.asc())
            )
            .scalars()
            .all()
        )
        fills = [_fill_record_to_domain(r) for r in fill_recs]

        eq_rows = list(
            session.execute(
                select(EquityCurveRecord)
                .where(EquityCurveRecord.env == env, EquityCurveRecord.run_id == run_id)
                .order_by(EquityCurveRecord.ts_ms.asc())
            )
            .scalars()
            .all()
        )
        bal_rows = list(
            session.execute(
                select(BalanceSnapshotRecord)
                .where(BalanceSnapshotRecord.env == env, BalanceSnapshotRecord.run_id == run_id)
                .order_by(BalanceSnapshotRecord.ts_ms.asc())
            )
            .scalars()
            .all()
        )

        closed_trades = _reconstruct_round_trip_trades(fills) if fills else []

        total_realized = sum((f.realized_pnl for f in fills), Decimal("0"))
        total_fees = sum((f.fee for f in fills), Decimal("0"))
        total_funding = (
            Decimal(str(eq_rows[-1].cumulative_funding)) if eq_rows else Decimal("0")
        )

        # Equity curve -> max drawdown
        equities: list[float] = []
        if eq_rows:
            equities = [float(r.equity) for r in eq_rows]
        elif bal_rows:
            equities = [float(r.total) + float(r.unrealized_pnl) for r in bal_rows]
        max_dd_pct = 0.0
        if equities:
            peak = equities[0]
            for eq in equities:
                peak = max(peak, eq)
                if peak > 0:
                    max_dd_pct = max(max_dd_pct, (peak - eq) / peak)
        initial_equity = equities[0] if equities else 0.0
        fee_drag_ratio = (float(total_fees) / initial_equity) if initial_equity > 0 else 0.0

        # Trade-level stats
        net_pnls = [float(t["net_pnl"]) for t in closed_trades]
        wins = [p for p in net_pnls if p > 0]
        losses = [p for p in net_pnls if p < 0]
        win_rate = (len(wins) / len(net_pnls)) if net_pnls else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
        expectancy = (sum(net_pnls) / len(net_pnls)) if net_pnls else 0.0
        avg_win = (gross_win / len(wins)) if wins else 0.0
        avg_loss = (-gross_loss / len(losses)) if losses else 0.0
        avg_hold_hours = (
            sum(float(t["holding_ms"]) for t in closed_trades) / len(closed_trades) / MS_PER_HOUR
            if closed_trades
            else 0.0
        )

        # Observation window
        started = int(run_row.started_at_ms or 0)
        ended = int(run_row.ended_at_ms) if run_row.ended_at_ms else None
        if fills:
            last_fill_ts = max(f.exchange_ts_ms for f in fills)
            ended = max(ended or 0, last_fill_ts)
        obs_days = ((ended or started) - started) / MS_PER_DAY if started else 0.0
        obs_days = max(obs_days, 0.0)
        trades_per_day = (len(closed_trades) / obs_days) if obs_days > 0 else float(len(closed_trades))

        # Open position residual (unmatched lots)
        open_qty = Decimal("0")
        for f in fills:
            open_qty += f.quantity if f.side == OrderSide.BUY else -f.quantity

        # ---- Diagnosis ----
        tags: list[str] = []
        mutations: list[str] = []
        findings: list[str] = []

        if len(closed_trades) == 0:
            tags.append("NO_CLOSED_TRADES")
            findings.append(
                "模拟盘观察期内未完成完整闭环交易：信号可能过于保守或持仓未平仓，"
                "建议下一轮适度放宽信号灵敏度并检查止盈止损触发条件。"
            )
            mutations.append("WIDEN_PROFIT_TARGET")

        if trades_per_day > 6.0 or len(closed_trades) > 30:
            tags.append("OVER_TRADING")
            findings.append(
                f"模拟盘交易频率偏高（{trades_per_day:.1f} 笔/天，共 {len(closed_trades)} 笔闭环交易），"
                "短周期噪音导致频繁开平仓。"
            )
            if "WIDEN_HORIZON_COOLDOWN" not in mutations:
                mutations.append("WIDEN_HORIZON_COOLDOWN")

        if fee_drag_ratio > 0.08:
            tags.append("FEE_DRAG_HIGH")
            findings.append(
                f"手续费磨损占初始资金 {fee_drag_ratio * 100:.1f}%（累计 ${float(total_fees):,.1f}），"
                "双边 Taker 手续费持续侵蚀净值。"
            )
            if "WIDEN_HORIZON_COOLDOWN" not in mutations:
                mutations.append("WIDEN_HORIZON_COOLDOWN")

        if max_dd_pct > 0.20:
            tags.append("DRAWDOWN_BREACH")
            findings.append(
                f"模拟盘最大回撤 {max_dd_pct * 100:.1f}% 超出 20% 风控红线，逆势段缺乏保护性截断。"
            )
            for m in ("ADD_MACRO_TREND_FILTER", "ADD_STOP_AND_TRAILING"):
                if m not in mutations:
                    mutations.append(m)

        if closed_trades and win_rate < 0.35:
            tags.append("WIN_RATE_LOW")
            findings.append(
                f"模拟盘胜率仅 {win_rate * 100:.1f}%，入场过滤不足，逆势单较多。"
            )
            if "ADD_MACRO_TREND_FILTER" not in mutations:
                mutations.append("ADD_MACRO_TREND_FILTER")

        if closed_trades and profit_factor < 1.2:
            tags.append("PROFIT_FACTOR_LOW")
            findings.append(
                f"模拟盘盈亏因子 {profit_factor:.2f}x 偏低（平均盈利 ${avg_win:,.1f} / 平均亏损 ${avg_loss:,.1f}），"
                "右尾利润释放不足或止损过紧。"
            )
            for m in ("ADD_STOP_AND_TRAILING", "WIDEN_PROFIT_TARGET"):
                if m not in mutations:
                    mutations.append(m)

        if avg_hold_hours > 120.0:
            tags.append("HOLD_TOO_LONG")
            findings.append(
                f"平均持仓时长 {avg_hold_hours:.0f} 小时过长，资金占用效率低且易错过反转信号。"
            )
            if "ADD_STOP_AND_TRAILING" not in mutations:
                mutations.append("ADD_STOP_AND_TRAILING")

        if not tags:
            tags.append("HEALTHY")
            findings.append(
                f"模拟盘运行健康：累计已实现盈亏 ${float(total_realized):+,.1f}，"
                f"胜率 {win_rate * 100:.1f}%，盈亏因子 {profit_factor:.2f}x，最大回撤 {max_dd_pct * 100:.1f}%。"
            )

        summary_zh = "；".join(findings)

        return ShadowPerformanceReport(
            run_id=run_id,
            env=env,
            strategy_id=run_row.strategy_id,
            strategy_version_id=run_row.strategy_version_id,
            symbol=symbol,
            exchange=exchange,
            status=run_row.status,
            started_at_ms=started,
            ended_at_ms=ended,
            observation_days=round(obs_days, 3),
            fill_count=len(fills),
            closed_trade_count=len(closed_trades),
            total_realized_pnl_usdt=str(total_realized),
            total_fees_usdt=str(total_fees),
            total_funding_usdt=str(total_funding),
            fee_drag_ratio=round(fee_drag_ratio, 5),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 3),
            expectancy_usdt=round(expectancy, 2),
            avg_win_usdt=round(avg_win, 2),
            avg_loss_usdt=round(avg_loss, 2),
            max_drawdown_pct=round(max_dd_pct, 4),
            trades_per_day=round(trades_per_day, 2),
            avg_holding_hours=round(avg_hold_hours, 1),
            open_position_qty=str(open_qty),
            diagnosis_tags=tags,
            diagnosis_summary_zh=summary_zh,
            recommended_mutations=mutations[:3],
            closed_trades=closed_trades,
        )
