"""Pre-trade risk gates, post-trade alerts, and sub-second emergency Kill Switch (P8)."""

from __future__ import annotations

from collections import deque
from decimal import Decimal
import time
from typing import Any, Callable
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from quant_platform.core.decimal_utils import ONE, StrictDecimal, ZERO
from quant_platform.core.enums import OrderSide, OrderType, RunEnv
from quant_platform.core.interfaces import BrokerAdapter, Clock, RiskEngine
from quant_platform.core.models import Balance, Fill, OrderRequest, Position, SymbolSpec
from quant_platform.db.schema import RiskEventRecord
from quant_platform.db.session import append_audit_log
from quant_platform.exceptions import RiskRejectedError


class LiveRiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_order_notional: StrictDecimal = Decimal("100000")
    max_symbol_notional: StrictDecimal = Decimal("500000")
    max_total_leverage: StrictDecimal = Decimal("10")
    max_daily_loss_amount: StrictDecimal = Decimal("5000")
    max_drawdown_pct: StrictDecimal = Decimal("0.20")
    max_orders_per_minute: int = 60
    duplicate_window_ms: int = 2_000
    blacklisted_symbols: list[str] = Field(default_factory=list)
    max_slippage_bps: StrictDecimal = Decimal("50")
    liq_distance_warn_pct: StrictDecimal = Decimal("0.05")


class LiveRiskEngine(RiskEngine):
    """
    Production pre-trade & post-trade RiskEngine with <1s emergency Kill Switch.
    Persists every rejection and alert to `risk_events` and records Kill Switch activations to `audit_logs`.
    """

    def __init__(
        self,
        *,
        env: RunEnv,
        run_id: str,
        strategy_id: str,
        config: LiveRiskConfig,
        session: Session,
        clock: Clock,
        alert_callbacks: list[Callable[[dict[str, Any]], None]] | None = None,
    ) -> None:
        self.env = env
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.config = config
        self.session = session
        self.clock = clock
        self._alerts = list(alert_callbacks or [])

        self._kill_switch_active = False
        self._daily_pnl = ZERO
        self._peak_equity = ZERO
        self._order_timestamps: deque[int] = deque()
        self._recent_fingerprints: dict[tuple[str, str, str, str, str], int] = {}
        self._tracked_positions: dict[tuple[str, str], Position] = {}

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def record_daily_pnl(self, pnl: Decimal) -> None:
        self._daily_pnl = pnl

    def record_equity_snapshot(self, equity: Decimal) -> None:
        if equity > self._peak_equity:
            self._peak_equity = equity

    def update_tracked_positions(self, positions: list[Position]) -> None:
        for p in positions:
            self._tracked_positions[(p.exchange, p.symbol)] = p

    def _emit_risk_event(
        self,
        *,
        rule_code: str,
        severity: str,
        action: str,
        reason: str,
        details: dict[str, Any],
        client_order_id: str | None = None,
        symbol: str | None = None,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        now = ts_ms if ts_ms is not None else self.clock.now_ms()
        ev_id = f"risk_{uuid.uuid4().hex[:16]}"
        row = RiskEventRecord(
            event_id=ev_id,
            env=self.env.value,
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            rule_code=rule_code,
            severity=severity,
            action=action,
            client_order_id=client_order_id,
            symbol=symbol,
            reason=reason,
            details_json=details,
            ts_ms=now,
        )
        self.session.add(row)
        self.session.flush()
        payload = {
            "event_id": ev_id,
            "rule_code": rule_code,
            "severity": severity,
            "action": action,
            "symbol": symbol,
            "client_order_id": client_order_id,
            "reason": reason,
            "details": details,
            "ts_ms": now,
        }
        for cb in self._alerts:
            cb(payload)
        return payload

    def _reject(
        self,
        rule_code: str,
        reason: str,
        details: dict[str, Any],
        request: OrderRequest,
        now_ms: int,
    ) -> None:
        self._emit_risk_event(
            rule_code=rule_code,
            severity="HIGH",
            action="REJECT_ORDER",
            reason=reason,
            details=details,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            ts_ms=now_ms,
        )
        raise RiskRejectedError(rule_code=rule_code, message=reason, details=details)

    def check_pre_trade(
        self,
        request: OrderRequest,
        spec: SymbolSpec,
        positions: list[Position],
        balances: list[Balance],
        now_ms: int,
    ) -> None:
        self.update_tracked_positions(positions)

        if self._kill_switch_active and not request.reduce_only:
            self._reject(
                "KILL_SWITCH_ACTIVE",
                "Emergency Kill Switch is active; new risk-increasing orders are blocked.",
                {},
                request,
                now_ms,
            )

        if request.symbol in self.config.blacklisted_symbols:
            self._reject(
                "SYMBOL_BLACKLISTED",
                f"Symbol {request.symbol} is on the risk blacklist.",
                {"symbol": request.symbol},
                request,
                now_ms,
            )

        pos_map = {(p.exchange, p.symbol): p for p in positions}
        cur_pos = pos_map.get((request.exchange, request.symbol))
        ref_px = request.price or request.stop_price or (cur_pos.mark_price if cur_pos and cur_pos.mark_price > ZERO else Decimal("50000"))
        order_notional = request.quantity * ref_px * spec.contract_size

        equity = sum((b.total for b in balances if b.asset == spec.margin_asset), ZERO)
        avail_margin = sum((b.available for b in balances if b.asset == spec.margin_asset), ZERO)
        if equity > self._peak_equity:
            self._peak_equity = equity

        if not request.reduce_only:
            if -self._daily_pnl > self.config.max_daily_loss_amount:
                self._reject(
                    "DAILY_LOSS_BREAKER",
                    f"Daily loss {-self._daily_pnl} exceeds limit {self.config.max_daily_loss_amount}.",
                    {"daily_pnl": str(self._daily_pnl), "limit": str(self.config.max_daily_loss_amount)},
                    request,
                    now_ms,
                )

            if self._peak_equity > ZERO:
                dd_pct = (self._peak_equity - equity) / self._peak_equity
                if dd_pct > self.config.max_drawdown_pct:
                    self._reject(
                        "MAX_DRAWDOWN_BREAKER",
                        f"Account drawdown {dd_pct:.4f} exceeds max_drawdown_pct {self.config.max_drawdown_pct}.",
                        {"drawdown_pct": str(dd_pct), "limit": str(self.config.max_drawdown_pct)},
                        request,
                        now_ms,
                    )

            if order_notional > self.config.max_order_notional:
                self._reject(
                    "MAX_ORDER_NOTIONAL",
                    f"Order notional {order_notional} exceeds max_order_notional {self.config.max_order_notional}.",
                    {"order_notional": str(order_notional), "limit": str(self.config.max_order_notional)},
                    request,
                    now_ms,
                )

            cur_qty = cur_pos.quantity if cur_pos else ZERO
            signed_delta = request.quantity if request.side == OrderSide.BUY else -request.quantity
            proj_qty = cur_qty + signed_delta
            proj_sym_notional = abs(proj_qty) * ref_px * spec.contract_size
            if proj_sym_notional > self.config.max_symbol_notional:
                self._reject(
                    "MAX_SYMBOL_NOTIONAL",
                    f"Projected symbol notional {proj_sym_notional} exceeds max_symbol_notional {self.config.max_symbol_notional}.",
                    {"projected_notional": str(proj_sym_notional), "limit": str(self.config.max_symbol_notional)},
                    request,
                    now_ms,
                )

            other_notional = sum(
                (
                    abs(p.quantity) * (p.mark_price if p.mark_price > ZERO else p.entry_price)
                    for (ex, sym), p in pos_map.items()
                    if (ex, sym) != (request.exchange, request.symbol)
                ),
                ZERO,
            )
            proj_gross = other_notional + proj_sym_notional
            if equity > ZERO and (proj_gross / equity) > self.config.max_total_leverage:
                self._reject(
                    "MAX_TOTAL_LEVERAGE",
                    f"Projected total leverage {proj_gross / equity:.4f} exceeds max_total_leverage {self.config.max_total_leverage}.",
                    {"projected_leverage": str(proj_gross / equity), "limit": str(self.config.max_total_leverage)},
                    request,
                    now_ms,
                )

            req_margin = order_notional / max(ONE, request.leverage)
            if req_margin > avail_margin:
                self._reject(
                    "INSUFFICIENT_MARGIN",
                    f"Required margin {req_margin} exceeds available margin {avail_margin}.",
                    {"required_margin": str(req_margin), "available_margin": str(avail_margin)},
                    request,
                    now_ms,
                )

        # Duplicate order guard within rolling duplicate_window_ms
        fp = (
            request.exchange,
            request.symbol,
            request.side.value,
            str(request.quantity),
            str(request.price),
        )
        prev_ts = self._recent_fingerprints.get(fp)
        if prev_ts is not None and 0 <= (now_ms - prev_ts) <= self.config.duplicate_window_ms:
            self._reject(
                "DUPLICATE_ORDER_GUARD",
                f"Duplicate order blocked within {self.config.duplicate_window_ms}ms window.",
                {"fingerprint": list(fp), "prev_ts_ms": prev_ts},
                request,
                now_ms,
            )

        # Order frequency rate limit (per 60s window)
        cutoff = now_ms - 60_000
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()
        if len(self._order_timestamps) >= self.config.max_orders_per_minute:
            self._reject(
                "ORDER_RATE_LIMIT",
                f"Order rate limit exceeded ({self.config.max_orders_per_minute}/min).",
                {"count_last_60s": len(self._order_timestamps)},
                request,
                now_ms,
            )

        self._recent_fingerprints[fp] = now_ms
        self._order_timestamps.append(now_ms)

    def check_post_trade(
        self,
        fill: Fill,
        mark_price: Decimal | None = None,
        funding_rate: Decimal | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        now = fill.local_recv_ts_ms

        if abs(fill.slippage_bps) > self.config.max_slippage_bps:
            events.append(
                self._emit_risk_event(
                    rule_code="ABNORMAL_SLIPPAGE_ALERT",
                    severity="MEDIUM",
                    action="ALERT",
                    reason=f"Fill slippage {fill.slippage_bps} bps exceeded threshold {self.config.max_slippage_bps} bps.",
                    details={"slippage_bps": str(fill.slippage_bps), "fill_id": fill.fill_id},
                    client_order_id=fill.client_order_id,
                    symbol=fill.symbol,
                    ts_ms=now,
                )
            )

        mp = mark_price or fill.mark_price
        pos = self._tracked_positions.get((fill.exchange, fill.symbol))
        if mp is not None and mp > ZERO and pos is not None and pos.liquidation_price is not None and pos.liquidation_price > ZERO:
            dist = abs(mp - pos.liquidation_price) / mp
            if dist <= self.config.liq_distance_warn_pct:
                events.append(
                    self._emit_risk_event(
                        rule_code="LIQUIDATION_PROXIMITY_ALERT",
                        severity="CRITICAL",
                        action="ALERT",
                        reason=f"Mark price {mp} within {dist:.4%} of liquidation price {pos.liquidation_price}.",
                        details={
                            "mark_price": str(mp),
                            "liquidation_price": str(pos.liquidation_price),
                            "distance_pct": str(dist),
                        },
                        client_order_id=fill.client_order_id,
                        symbol=fill.symbol,
                        ts_ms=now,
                    )
                )
        return events

    def trigger_kill_switch(
        self,
        broker: BrokerAdapter,
        close_positions: bool = False,
        reason: str = "manual",
    ) -> dict[str, Any]:
        """Emergency sub-second Kill Switch: latch block, cancel all open orders, optionally flatten all positions."""
        t0 = time.perf_counter()
        self._kill_switch_active = True
        now = self.clock.now_ms()

        open_orders = broker.query_open_orders()
        canceled = 0
        for ord_obj in open_orders:
            broker.cancel_order(ord_obj.client_order_id, ord_obj.exchange, ord_obj.symbol)
            canceled += 1

        flattened = 0
        if close_positions:
            for pos in broker.query_positions():
                if pos.quantity == ZERO:
                    continue
                close_side = OrderSide.SELL if pos.quantity > ZERO else OrderSide.BUY
                broker.submit_order(
                    OrderRequest(
                        client_order_id=f"ks_close_{uuid.uuid4().hex[:14]}",
                        exchange=pos.exchange,
                        symbol=pos.symbol,
                        side=close_side,
                        order_type=OrderType.MARKET,
                        quantity=abs(pos.quantity),
                        reduce_only=True,
                        created_ts_ms=now,
                    )
                )
                flattened += 1

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
        summary = {
            "kill_switch_active": True,
            "canceled_orders": canceled,
            "flattened_positions": flattened,
            "reason": reason,
            "elapsed_ms": elapsed_ms,
        }
        self._emit_risk_event(
            rule_code="KILL_SWITCH_TRIGGERED",
            severity="CRITICAL",
            action="KILL_SWITCH",
            reason=f"Emergency Kill Switch activated ({reason})",
            details=summary,
            ts_ms=now,
        )
        append_audit_log(
            self.session,
            actor="risk_engine",
            action="KILL_SWITCH_TRIGGERED",
            resource_type="run",
            resource_id=self.run_id,
            payload=summary,
            ts_ms=now,
        )
        return summary
