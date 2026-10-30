"""Isomorphic UnifiedExecutionPipeline and BacktestRunner / ShadowRunner / LiveRunner (P6 / Red Line 2)."""

from __future__ import annotations

from decimal import Decimal
import random
from typing import Any, Callable, Iterable

from quant_platform import ENGINE_VERSION
from quant_platform.backtest.engine import convert_intent_to_order_request
from quant_platform.core.decimal_utils import ZERO
from quant_platform.core.enums import RunEnv, RunStatus
from quant_platform.core.hashing import sha256_hex
from quant_platform.core.interfaces import (
    BrokerAdapter,
    Clock,
    DataFeed,
    NullRiskEngine,
    RiskEngine,
    Runner,
    Strategy,
    StrategyContext,
)
from quant_platform.core.models import (
    Balance,
    Position,
    RunRecord,
    Signal,
    StrategyConfig,
    StrategyIntent,
)
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import SignalTraceRecord
from quant_platform.exceptions import BrokerNotReadyError
from quant_platform.execution.coordinator import ExecutionCoordinator
from quant_platform.execution.id_mapping import OrderIdMapper, generate_client_order_id
from quant_platform.specs import SymbolSpecService, normalize_and_validate_order

MarketEventTuple = tuple[str, Any]


class UnifiedExecutionPipeline:
    """
    Single environment-agnostic execution pipeline shared by BacktestRunner, ShadowRunner, and LiveRunner.
    Contains ZERO environment branches (Red Line 2).
    """

    def __init__(
        self,
        *,
        run_record: RunRecord,
        strategy_cls: type[Strategy],
        config: StrategyConfig,
        clock: Clock,
        data_feed: DataFeed,
        broker: BrokerAdapter,
        ledger: UnifiedLedgerRepository,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self.run_record = run_record
        self.strategy_cls = strategy_cls
        self.config = config
        self.clock = clock
        self.data_feed = data_feed
        self.broker = broker
        self.ledger = ledger
        self.risk_engine = risk_engine or NullRiskEngine()
        self._stopped = False
        self._seq = 0

        self.coordinator = ExecutionCoordinator(
            env=run_record.env,
            run_id=run_record.run_id,
            strategy_id=config.strategy_id,
            broker=broker,
            ledger=ledger,
            id_mapper=OrderIdMapper(ledger.session),
            clock=clock,
        )

    def stop(self) -> None:
        self._stopped = True

    def execute_stream(self, events: Iterable[MarketEventTuple]) -> RunRecord:
        self.broker.startup_sync()
        if not self.broker.is_ready():
            default_ex = self.config.exchanges[0] if self.config.exchanges else "unknown"
            raise BrokerNotReadyError(default_ex)

        running_rec = self.run_record.model_copy(
            update={"status": RunStatus.RUNNING, "started_at_ms": self.clock.now_ms()}
        )
        self.ledger.upsert_run(running_rec)

        def _pos_reader(ex: str, sym: str) -> Position | None:
            found = self.broker.query_positions(ex, sym)
            return found[0] if found else None

        def _bal_reader(ex: str, asset: str) -> Balance | None:
            for b in self.broker.query_balance(ex):
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

        def _handle_intents(returned: list[StrategyIntent] | None, snapshot_ref: str | None = None) -> None:
            intents: list[StrategyIntent] = []
            if returned:
                intents.extend(returned)
            intents.extend(strategy.drain_intents())
            for intent in intents:
                self._process_single_intent(intent, _pos_reader, snapshot_ref=snapshot_ref)

        self.coordinator.register_order_listener(lambda o: _handle_intents(strategy.on_order_update(o)))
        self.coordinator.register_fill_listener(
            lambda f: (
                self.risk_engine.check_post_trade(f, mark_price=f.mark_price),
                _handle_intents(strategy.on_fill(f), snapshot_ref=f.snapshot_ref),
            )
        )

        try:
            _handle_intents(strategy.on_start())
            for kind, payload in events:
                if self._stopped:
                    break
                if kind == "bar":
                    _handle_intents(strategy.on_bar(payload), snapshot_ref=f"bar:{payload.open_time_ms}")
                elif kind == "tick":
                    _handle_intents(strategy.on_tick(payload), snapshot_ref=f"tick:{payload.trade_id}")
                elif kind == "orderbook":
                    _handle_intents(strategy.on_orderbook(payload), snapshot_ref=f"ob:{payload.last_update_id}")
                elif kind == "funding":
                    _handle_intents(strategy.on_funding(payload), snapshot_ref=f"fund:{payload.payment_id}")

            _handle_intents(strategy.on_stop())
            final_status = RunStatus.STOPPED if self._stopped else RunStatus.COMPLETED
            done_rec = running_rec.model_copy(
                update={"status": final_status, "ended_at_ms": self.clock.now_ms()}
            )
            self.ledger.upsert_run(done_rec)
            return done_rec
        except Exception as exc:
            fail_rec = running_rec.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "ended_at_ms": self.clock.now_ms(),
                    "error_message": f"{type(exc).__name__}: {exc}",
                }
            )
            self.ledger.upsert_run(fail_rec)
            raise

    def _process_single_intent(
        self,
        intent: StrategyIntent,
        pos_reader: Callable[[str, str], Position | None],
        snapshot_ref: str | None = None,
    ) -> None:
        now = self.clock.now_ms()
        cur_pos = pos_reader(intent.exchange, intent.symbol)
        cur_qty = cur_pos.quantity if cur_pos else ZERO

        raw_req = convert_intent_to_order_request(
            intent=intent,
            current_pos_qty=cur_qty,
            config=self.config,
            now_ms=now,
        )
        if raw_req is None:
            return

        self._seq += 1
        cid = generate_client_order_id(self.run_record.run_id, self.config.strategy_id, self._seq)
        raw_req = raw_req.model_copy(update={"client_order_id": cid})

        spec = self.data_feed.get_symbol_spec(raw_req.exchange, raw_req.symbol, at_ts_ms=now)
        ob = self.data_feed.get_orderbook(raw_req.exchange, raw_req.symbol, at_ts_ms=now)
        ref_px: Decimal | None = ob.mid_price if ob else None
        if ref_px is None:
            mp = self.data_feed.get_mark_price(raw_req.exchange, raw_req.symbol, at_ts_ms=now)
            ref_px = mp.mark_price if mp else None
        if ref_px is None:
            bars = self.data_feed.get_bars(raw_req.exchange, raw_req.symbol, self.config.bar_interval, end_ts_ms=now, limit=1)
            ref_px = bars[-1].close if bars else None

        norm_req = normalize_and_validate_order(
            raw_req,
            spec,
            reference_price=ref_px,
            auto_round=True,
            now_ms=now,
        )

        # Record end-to-end Signal -> Order trace (P7/P9)
        trace = SignalTraceRecord(
            signal_id=intent.signal_id,
            env=self.run_record.env.value,
            run_id=self.run_record.run_id,
            strategy_id=self.config.strategy_id,
            exchange=intent.exchange,
            symbol=intent.symbol,
            intent_type="Signal" if isinstance(intent, Signal) else "TargetPosition",
            payload_json=intent.model_dump(mode="json"),
            client_order_id=cid,
            snapshot_ref=snapshot_ref or (f"ob:{ob.last_update_id}" if ob else None),
            signal_ts_ms=intent.ts_ms,
            order_ts_ms=now,
        )
        self.ledger.session.add(trace)
        self.ledger.session.flush()

        self.risk_engine.check_pre_trade(
            request=norm_req,
            spec=spec,
            positions=self.broker.query_positions(),
            balances=self.broker.query_balance(),
            now_ms=now,
        )
        self.coordinator.submit_order(norm_req)


class _BaseIsomorphicRunner(Runner):
    """Thin composition wiring (DataFeed + BrokerAdapter + RiskEngine) into UnifiedExecutionPipeline."""

    ENV: RunEnv = RunEnv.BACKTEST

    def __init__(
        self,
        *,
        run_id: str,
        strategy_cls: type[Strategy],
        strategy_code: str,
        config: StrategyConfig,
        clock: Clock,
        data_feed: DataFeed,
        broker: BrokerAdapter,
        ledger: UnifiedLedgerRepository,
        event_source: Callable[[], Iterable[MarketEventTuple]],
        risk_engine: RiskEngine | None = None,
    ) -> None:
        specs = []
        for ex in config.exchanges:
            for sym in config.symbols:
                try:
                    specs.append(data_feed.get_symbol_spec(ex, sym, at_ts_ms=clock.now_ms()))
                except KeyError:
                    pass
        spec_ver = SymbolSpecService.composite_version_hash(specs) if specs else "specs_default"

        record = RunRecord(
            run_id=run_id,
            env=self.ENV,
            strategy_id=config.strategy_id,
            strategy_version_id=config.strategy_version_id,
            strategy_code_hash=sha256_hex(strategy_code),
            config_snapshot=config.model_dump(mode="json"),
            dataset_version=data_feed.dataset_version(),
            symbol_spec_version=spec_ver,
            engine_version=ENGINE_VERSION,
            rng_seed=config.rng_seed,
            status=RunStatus.CREATED,
            started_at_ms=clock.now_ms(),
        )
        self._event_source = event_source
        self.pipeline = UnifiedExecutionPipeline(
            run_record=record,
            strategy_cls=strategy_cls,
            config=config,
            clock=clock,
            data_feed=data_feed,
            broker=broker,
            ledger=ledger,
            risk_engine=risk_engine,
        )

    def run(self) -> RunRecord:
        return self.pipeline.execute_stream(self._event_source())

    def stop(self) -> None:
        self.pipeline.stop()


class BacktestRunner(_BaseIsomorphicRunner):
    ENV = RunEnv.BACKTEST


class ShadowRunner(_BaseIsomorphicRunner):
    ENV = RunEnv.SHADOW


class LiveRunner(_BaseIsomorphicRunner):
    ENV = RunEnv.LIVE
