"""Event-driven BacktestEngine driving Strategy + HistoricalDataFeed + BacktestBroker (P4)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random
from typing import Any, Callable
import uuid

from sqlalchemy.orm import Session

from quant_platform import ENGINE_VERSION
from quant_platform.backtest.broker import BacktestBroker
from quant_platform.backtest.matching import SlippageModelConfig
from quant_platform.core.decimal_utils import ZERO
from quant_platform.core.enums import OrderSide, OrderType, RunEnv, RunStatus, SignalAction
from quant_platform.core.hashing import compute_run_id, sha256_hex
from quant_platform.core.interfaces import Strategy, StrategyContext
from quant_platform.core.models import (
    Balance,
    EquityPoint,
    Fill,
    Order,
    OrderRequest,
    Position,
    RunRecord,
    Signal,
    StrategyConfig,
    StrategyIntent,
    TargetPosition,
)
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import SignalTraceRecord
from quant_platform.specs import SymbolSpecService, normalize_and_validate_order


@dataclass
class BacktestRunResult:
    run_record: RunRecord
    initial_equity: Decimal
    final_equity: Decimal
    net_pnl: Decimal
    total_fees: Decimal
    total_funding: Decimal
    total_interest: Decimal
    equity_curve: list[EquityPoint]
    orders: list[Order]
    fills: list[Fill]


def convert_intent_to_order_request(
    *,
    intent: StrategyIntent,
    current_pos_qty: Decimal,
    config: StrategyConfig,
    now_ms: int,
) -> OrderRequest | None:
    """Environment-agnostic translation of Signal / TargetPosition into OrderRequest."""
    if isinstance(intent, Signal):
        if intent.action == SignalAction.HOLD:
            return None
        if intent.action == SignalAction.CLOSE:
            if current_pos_qty == ZERO:
                return None
            side = OrderSide.SELL if current_pos_qty > ZERO else OrderSide.BUY
            qty = abs(current_pos_qty)
            reduce_only = True
        else:
            side = OrderSide.BUY if intent.action == SignalAction.BUY else OrderSide.SELL
            qty = intent.quantity
            reduce_only = intent.reduce_only
        if qty <= ZERO:
            return None
        return OrderRequest(
            client_order_id=f"cid_{uuid.uuid4().hex[:20]}",
            exchange=intent.exchange,
            symbol=intent.symbol,
            side=side,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            quantity=qty,
            price=intent.limit_price,
            stop_price=intent.stop_price,
            post_only=intent.post_only,
            reduce_only=reduce_only,
            leverage=config.default_leverage,
            margin_mode=config.margin_mode,
            signal_id=intent.signal_id,
            created_ts_ms=now_ms,
        )

    if isinstance(intent, TargetPosition):
        delta = intent.target_qty - current_pos_qty
        if delta == ZERO:
            return None
        side = OrderSide.BUY if delta > ZERO else OrderSide.SELL
        return OrderRequest(
            client_order_id=f"cid_{uuid.uuid4().hex[:20]}",
            exchange=intent.exchange,
            symbol=intent.symbol,
            side=side,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            quantity=abs(delta),
            price=intent.limit_price,
            post_only=intent.post_only,
            reduce_only=(intent.target_qty == ZERO),
            leverage=config.default_leverage,
            margin_mode=config.margin_mode,
            signal_id=intent.signal_id,
            created_ts_ms=now_ms,
        )
    return None


class BacktestEngine:
    """Deterministic event-driven backtest engine."""

    def __init__(
        self,
        *,
        db_session: Session,
        clock: SimulatedClock,
        data_feed: HistoricalDataFeed,
        strategy_cls: type[Strategy],
        strategy_code: str,
        config: StrategyConfig,
        slippage_config: SlippageModelConfig | None = None,
        liquidation_penalty_rate: Decimal = Decimal("0.005"),
        allow_spot_borrow: bool = False,
        hourly_borrow_rates: dict[str, Decimal] | None = None,
        maintenance_windows: list[tuple[int, int]] | None = None,
    ) -> None:
        self.db_session = db_session
        self.clock = clock
        self.data_feed = data_feed
        self.strategy_cls = strategy_cls
        self.strategy_code = strategy_code
        self.config = config
        self.slippage_config = slippage_config or SlippageModelConfig()
        self.liquidation_penalty_rate = liquidation_penalty_rate
        self.allow_spot_borrow = allow_spot_borrow
        self.hourly_borrow_rates = hourly_borrow_rates
        self.maintenance_windows = maintenance_windows

    def run(
        self,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestRunResult:
        ledger = UnifiedLedgerRepository(self.db_session)
        timeline = self.data_feed.all_timeline_timestamps()
        start_ts = timeline[0] if timeline else self.clock.now_ms()
        if start_ts > self.clock.now_ms():
            self.clock.advance_to(start_ts)

        active_specs = []
        for ex in self.config.exchanges:
            for sym in self.config.symbols:
                try:
                    active_specs.append(self.data_feed.get_symbol_spec(ex, sym, at_ts_ms=start_ts))
                except KeyError:
                    pass
        spec_ver_hash = SymbolSpecService.composite_version_hash(active_specs) if active_specs else "specs_none"
        ds_ver = self.data_feed.dataset_version()

        cfg_snapshot = {
            **self.config.model_dump(mode="json"),
            "slippage_model": self.slippage_config.model_dump(mode="json"),
        }
        run_id = compute_run_id(
            strategy_code=self.strategy_code,
            config_snapshot=cfg_snapshot,
            dataset_version=ds_ver,
            symbol_spec_version=spec_ver_hash,
            engine_version=ENGINE_VERSION,
        )

        init_bals = dict(self.config.initial_balances) or {"USDT": Decimal("10000")}
        initial_equity = init_bals.get("USDT", Decimal("10000"))
        default_ex = self.config.exchanges[0] if self.config.exchanges else "binance"

        broker = BacktestBroker(
            env=RunEnv.BACKTEST,
            run_id=run_id,
            strategy_id=self.config.strategy_id,
            clock=self.clock,
            data_feed=self.data_feed,
            ledger=ledger,
            initial_balances=init_bals,
            default_exchange=default_ex,
            vip_level=self.config.vip_level,
            use_platform_token_discount=self.config.use_platform_token_discount,
            slippage_config=self.slippage_config,
            liquidation_penalty_rate=self.liquidation_penalty_rate,
            allow_spot_borrow=self.allow_spot_borrow,
            hourly_borrow_rates=self.hourly_borrow_rates,
            maintenance_windows=self.maintenance_windows,
        )
        broker.startup_sync()

        run_rec = RunRecord(
            run_id=run_id,
            env=RunEnv.BACKTEST,
            strategy_id=self.config.strategy_id,
            strategy_version_id=self.config.strategy_version_id,
            strategy_code_hash=sha256_hex(self.strategy_code),
            config_snapshot=cfg_snapshot,
            dataset_version=ds_ver,
            symbol_spec_version=spec_ver_hash,
            engine_version=ENGINE_VERSION,
            rng_seed=self.config.rng_seed,
            status=RunStatus.RUNNING,
            started_at_ms=start_ts,
        )
        ledger.upsert_run(run_rec, created_at_ms=start_ts)

        def _pos_reader(ex: str, sym: str) -> Position | None:
            found = broker.query_positions(ex, sym)
            return found[0] if found else None

        def _bal_reader(ex: str, asset: str) -> Balance | None:
            for b in broker.query_balance(ex):
                if b.asset == asset:
                    return b
            return None

        ctx = StrategyContext(
            clock=self.clock,
            data_feed=self.data_feed,
            config=self.config,
            rng=random.Random(self.config.rng_seed),
            position_reader=_pos_reader,
            balance_reader=_bal_reader,
        )
        strategy = self.strategy_cls(ctx, self.config.params)

        def _dispatch_intents(returned: list[StrategyIntent] | None) -> None:
            queued: list[StrategyIntent] = []
            if returned:
                queued.extend(returned)
            queued.extend(strategy.drain_intents())
            for intent in queued:
                cur_pos = _pos_reader(intent.exchange, intent.symbol)
                cur_qty = cur_pos.quantity if cur_pos else ZERO
                raw_req = convert_intent_to_order_request(
                    intent=intent,
                    current_pos_qty=cur_qty,
                    config=self.config,
                    now_ms=self.clock.now_ms(),
                )
                if raw_req is None:
                    continue
                spec = self.data_feed.get_symbol_spec(raw_req.exchange, raw_req.symbol, at_ts_ms=self.clock.now_ms())
                ref_px = broker._reference_price(raw_req.exchange, raw_req.symbol)
                norm_req = normalize_and_validate_order(
                    raw_req,
                    spec,
                    reference_price=ref_px,
                    auto_round=True,
                    now_ms=self.clock.now_ms(),
                )
                trace = SignalTraceRecord(
                    signal_id=intent.signal_id,
                    env=RunEnv.BACKTEST.value,
                    run_id=run_id,
                    strategy_id=self.config.strategy_id,
                    exchange=intent.exchange,
                    symbol=intent.symbol,
                    intent_type="Signal" if isinstance(intent, Signal) else "TargetPosition",
                    payload_json=intent.model_dump(mode="json"),
                    client_order_id=norm_req.client_order_id,
                    snapshot_ref=f"ts:{self.clock.now_ms()}",
                    signal_ts_ms=intent.ts_ms,
                    order_ts_ms=self.clock.now_ms(),
                )
                self.db_session.add(trace)
                self.db_session.flush()
                broker.submit_order(norm_req)

        broker.register_order_callback(lambda o: _dispatch_intents(strategy.on_order_update(o)))
        broker.register_fill_callback(lambda f: _dispatch_intents(strategy.on_fill(f)))

        equity_points: list[EquityPoint] = []
        try:
            _dispatch_intents(strategy.on_start())

            # Index historical events by timestamp for exact chronological playback
            ob_by_ts: dict[int, list[Any]] = {}
            for obs in self.data_feed._orderbooks.values():
                for o in obs:
                    ob_by_ts.setdefault(o.exchange_ts_ms, []).append(o)

            mp_by_ts: dict[int, list[Any]] = {}
            for mps in self.data_feed._mark_prices.values():
                for m in mps:
                    mp_by_ts.setdefault(m.exchange_ts_ms, []).append(m)

            fr_by_ts: dict[int, list[Any]] = {}
            for frs in self.data_feed._funding_rates.values():
                for f in frs:
                    fr_by_ts.setdefault(f.funding_ts_ms, []).append(f)

            tk_by_ts: dict[int, list[Any]] = {}
            for tks in self.data_feed._ticks.values():
                for t in tks:
                    tk_by_ts.setdefault(t.exchange_ts_ms, []).append(t)

            bar_by_ts: dict[int, list[Any]] = {}
            for bars in self.data_feed._bars.values():
                for b in bars:
                    if b.is_closed:
                        bar_by_ts.setdefault(b.close_time_ms, []).append(b)

            total_steps = len(timeline)
            if progress_callback is not None:
                progress_callback(0, total_steps, "EVENT_REPLAY")

            canceled = False
            report_stride = max(1, total_steps // 20)
            for step_idx, ts_ms in enumerate(timeline, start=1):
                if cancel_check is not None and cancel_check():
                    canceled = True
                    break

                self.clock.advance_to(ts_ms)
                broker.accrue_borrow_interest(ts_ms)

                for ob in ob_by_ts.get(ts_ms, []):
                    broker.on_orderbook(ob)
                    _dispatch_intents(strategy.on_orderbook(ob))

                for mp in mp_by_ts.get(ts_ms, []):
                    broker.on_mark_price(mp)

                for fr in fr_by_ts.get(ts_ms, []):
                    payment = broker.on_funding_rate(fr)
                    if payment is not None:
                        _dispatch_intents(strategy.on_funding(payment))

                for tk in tk_by_ts.get(ts_ms, []):
                    broker.on_tick(tk)
                    _dispatch_intents(strategy.on_tick(tk))

                for b in bar_by_ts.get(ts_ms, []):
                    broker.on_bar(b)
                    _dispatch_intents(strategy.on_bar(b))

                eq_pt = broker.snapshot_equity(default_ex)
                equity_points.append(eq_pt)
                for bal in broker.query_balance(default_ex):
                    ledger.record_balance_snapshot(bal)

                if progress_callback is not None and (
                    step_idx % report_stride == 0 or step_idx == total_steps
                ):
                    progress_callback(step_idx, total_steps, "EVENT_REPLAY")

            _dispatch_intents(strategy.on_stop())
            final_pt = broker.snapshot_equity(default_ex)
            if not equity_points or equity_points[-1].ts_ms != final_pt.ts_ms or equity_points[-1].equity != final_pt.equity:
                equity_points.append(final_pt)

            final_status = RunStatus.STOPPED if canceled else RunStatus.COMPLETED
            if progress_callback is not None:
                progress_callback(
                    len(equity_points),
                    total_steps,
                    "STOPPED" if canceled else "REPORT_PERSIST",
                )

            run_rec = run_rec.model_copy(
                update={
                    "status": final_status,
                    "ended_at_ms": self.clock.now_ms(),
                }
            )
            ledger.upsert_run(run_rec)
        except Exception as exc:
            run_rec = run_rec.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "ended_at_ms": self.clock.now_ms(),
                    "error_message": f"{type(exc).__name__}: {exc}",
                }
            )
            ledger.upsert_run(run_rec)
            raise

        final_eq = equity_points[-1].equity if equity_points else initial_equity
        return BacktestRunResult(
            run_record=run_rec,
            initial_equity=initial_equity,
            final_equity=final_eq,
            net_pnl=final_eq - initial_equity,
            total_fees=broker._cumulative_fees,
            total_funding=broker._cumulative_funding,
            total_interest=broker._cumulative_interest,
            equity_curve=equity_points,
            orders=ledger.list_orders(RunEnv.BACKTEST, run_id),
            fills=ledger.list_fills(RunEnv.BACKTEST, run_id),
        )
