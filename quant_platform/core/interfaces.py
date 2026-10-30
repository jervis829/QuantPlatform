"""Eight core architectural interfaces enforcing Red Lines 1–10."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import random
from typing import Any, Callable

from pydantic import BaseModel

from quant_platform.core.models import (
    Balance,
    Bar,
    Fill,
    FundingPayment,
    FundingRatePoint,
    MarkIndexPrice,
    Order,
    OrderBookSnapshot,
    OrderRequest,
    Position,
    RunRecord,
    Signal,
    StrategyConfig,
    StrategyIntent,
    SymbolSpec,
    TargetPosition,
    Tick,
)

OrderUpdateCallback = Callable[[Order], None]
FillCallback = Callable[[Fill], None]


class Clock(ABC):
    """Unified UTC millisecond clock injected into Strategy, DataFeed, and Execution."""

    @abstractmethod
    def now_ms(self) -> int:
        """Current UTC timestamp in integer milliseconds."""
        raise NotImplementedError

    def exchange_offset_ms(self, exchange: str) -> int:
        """Estimated (exchange_server_time_ms - local_clock_ms)."""
        return 0

    def synced_exchange_now_ms(self, exchange: str) -> int:
        return self.now_ms() + self.exchange_offset_ms(exchange)


class DataFeed(ABC):
    """Unified market data access layer prohibiting lookahead bias (Red Line 3)."""

    @abstractmethod
    def cursor_ts_ms(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def dataset_version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_bars(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
        include_unclosed: bool = False,
    ) -> list[Bar]:
        raise NotImplementedError

    @abstractmethod
    def get_ticks(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
    ) -> list[Tick]:
        raise NotImplementedError

    @abstractmethod
    def get_orderbook(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> OrderBookSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    def get_mark_price(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> MarkIndexPrice | None:
        raise NotImplementedError

    @abstractmethod
    def get_funding_rates(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> list[FundingRatePoint]:
        raise NotImplementedError

    @abstractmethod
    def get_symbol_spec(
        self,
        exchange: str,
        symbol: str,
        at_ts_ms: int | None = None,
    ) -> SymbolSpec:
        raise NotImplementedError


class BrokerAdapter(ABC):
    """Unified execution abstraction across BacktestBroker, ShadowBroker, ExchangeBroker (Red Line 4)."""

    @abstractmethod
    def is_ready(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def startup_sync(self) -> dict[str, Any]:
        """Synchronize authoritative exchange state before allowing strategy execution (Red Line 10)."""
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> Order:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        raise NotImplementedError

    @abstractmethod
    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        raise NotImplementedError

    @abstractmethod
    def query_open_orders(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> list[Order]:
        raise NotImplementedError

    @abstractmethod
    def query_positions(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        raise NotImplementedError

    @abstractmethod
    def query_funding_payments(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        since_ms: int | None = None,
    ) -> list[FundingPayment]:
        raise NotImplementedError

    @abstractmethod
    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        raise NotImplementedError

    @abstractmethod
    def register_fill_callback(self, callback: FillCallback) -> None:
        raise NotImplementedError


class StrategyContext:
    """
    Environment-agnostic context injected into Strategy (Red Lines 1 & 2).
    Deliberately exposes NO environment tag, NO BrokerAdapter, NO file/network handles.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        data_feed: DataFeed,
        config: StrategyConfig,
        rng: random.Random,
        position_reader: Callable[[str, str], Position | None] | None = None,
        balance_reader: Callable[[str, str], Balance | None] | None = None,
    ) -> None:
        self._clock = clock
        self._data_feed = data_feed
        self._config = config
        self._rng = rng
        self._position_reader = position_reader
        self._balance_reader = balance_reader

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def now_ms(self) -> int:
        return self._clock.now_ms()

    @property
    def data(self) -> DataFeed:
        return self._data_feed

    @property
    def config(self) -> StrategyConfig:
        return self._config

    @property
    def rng(self) -> random.Random:
        return self._rng

    def get_position(self, exchange: str, symbol: str) -> Position | None:
        if self._position_reader is None:
            return None
        return self._position_reader(exchange, symbol)

    def get_balance(self, exchange: str, asset: str) -> Balance | None:
        if self._balance_reader is None:
            return None
        return self._balance_reader(exchange, asset)


class Strategy(ABC):
    """
    Base class for all trading strategies (Red Line 1).
    Strategies ONLY produce Signal or TargetPosition intents.
    """

    ParamModel: type[BaseModel] | None = None

    def __init__(self, context: StrategyContext, params: BaseModel | dict[str, Any] | None = None) -> None:
        self._ctx = context
        self._intent_buffer: list[StrategyIntent] = []
        if self.ParamModel is not None:
            if isinstance(params, self.ParamModel):
                self.params: Any = params
            elif isinstance(params, dict):
                self.params = self.ParamModel(**params)
            else:
                self.params = self.ParamModel()
        else:
            self.params = params or {}

    @property
    def ctx(self) -> StrategyContext:
        return self._ctx

    def emit_signal(self, signal: Signal) -> None:
        self._intent_buffer.append(signal)

    def emit_target_position(self, target: TargetPosition) -> None:
        self._intent_buffer.append(target)

    def drain_intents(self) -> list[StrategyIntent]:
        intents = list(self._intent_buffer)
        self._intent_buffer.clear()
        return intents

    def on_start(self) -> list[StrategyIntent] | None:
        return None

    def on_bar(self, bar: Bar) -> list[StrategyIntent] | None:
        return None

    def on_tick(self, tick: Tick) -> list[StrategyIntent] | None:
        return None

    def on_orderbook(self, orderbook: OrderBookSnapshot) -> list[StrategyIntent] | None:
        return None

    def on_fill(self, fill: Fill) -> list[StrategyIntent] | None:
        return None

    def on_order_update(self, order: Order) -> list[StrategyIntent] | None:
        return None

    def on_funding(self, payment: FundingPayment) -> list[StrategyIntent] | None:
        return None

    def on_stop(self) -> list[StrategyIntent] | None:
        return None


class RiskEngine(ABC):
    """Pre-trade, post-trade, and emergency Kill Switch risk engine."""

    @abstractmethod
    def check_pre_trade(
        self,
        request: OrderRequest,
        spec: SymbolSpec,
        positions: list[Position],
        balances: list[Balance],
        now_ms: int,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def check_post_trade(
        self,
        fill: Fill,
        mark_price: Decimal | None = None,
        funding_rate: Decimal | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def trigger_kill_switch(
        self,
        broker: BrokerAdapter,
        close_positions: bool = False,
        reason: str = "manual",
    ) -> dict[str, Any]:
        raise NotImplementedError


class StrategySandbox(ABC):
    """AST static checker + isolated container/namespace execution sandbox (Red Line 7)."""

    @abstractmethod
    def validate_code(self, code: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute_isolated(
        self,
        code: str,
        entrypoint_payload: dict[str, Any],
        timeout_sec: float | None = None,
        max_memory_mb: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class Reconciler(ABC):
    """Three-tier reconciliation engine with Exchange as Single Source of Truth (Red Line 10)."""

    @abstractmethod
    def reconcile_exchange_vs_local(self, run_id: str, broker: BrokerAdapter) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def reconcile_shadow_vs_live(self, shadow_run_id: str, live_run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def reconcile_backtest_vs_shadow(self, backtest_run_id: str, shadow_run_id: str) -> dict[str, Any]:
        raise NotImplementedError


class Runner(ABC):
    """Isomorphic runner interface shared by BacktestRunner, ShadowRunner, LiveRunner (Red Line 2)."""

    @abstractmethod
    def run(self) -> RunRecord:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# P0 Null / Skeleton implementations verifying interface contracts cleanly
# ---------------------------------------------------------------------------


class NullClock(Clock):
    def __init__(self, initial_ts_ms: int = 0) -> None:
        self._ts_ms = initial_ts_ms

    def now_ms(self) -> int:
        return self._ts_ms

    def set_now_ms(self, ts_ms: int) -> None:
        self._ts_ms = ts_ms


class NullDataFeed(DataFeed):
    def __init__(self, clock: Clock, version: str = "empty") -> None:
        self._clock = clock
        self._version = version

    def cursor_ts_ms(self) -> int:
        return self._clock.now_ms()

    def dataset_version(self) -> str:
        return self._version

    def get_bars(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
        include_unclosed: bool = False,
    ) -> list[Bar]:
        return []

    def get_ticks(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        limit: int | None = None,
    ) -> list[Tick]:
        return []

    def get_orderbook(self, exchange: str, symbol: str, at_ts_ms: int | None = None) -> OrderBookSnapshot | None:
        return None

    def get_mark_price(self, exchange: str, symbol: str, at_ts_ms: int | None = None) -> MarkIndexPrice | None:
        return None

    def get_funding_rates(
        self,
        exchange: str,
        symbol: str,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> list[FundingRatePoint]:
        return []

    def get_symbol_spec(self, exchange: str, symbol: str, at_ts_ms: int | None = None) -> SymbolSpec:
        raise KeyError(f"No SymbolSpec loaded for {exchange}:{symbol}")


class NullBrokerAdapter(BrokerAdapter):
    def __init__(self) -> None:
        self._ready = False
        self._order_callbacks: list[OrderUpdateCallback] = []
        self._fill_callbacks: list[FillCallback] = []

    def is_ready(self) -> bool:
        return self._ready

    def startup_sync(self) -> dict[str, Any]:
        self._ready = True
        return {"ready": True, "synced_orders": 0, "synced_positions": 0}

    def submit_order(self, request: OrderRequest) -> Order:
        raise NotImplementedError("NullBrokerAdapter does not execute orders")

    def cancel_order(self, client_order_id: str, exchange: str, symbol: str) -> Order:
        raise NotImplementedError("NullBrokerAdapter does not cancel orders")

    def query_order(self, client_order_id: str, exchange: str, symbol: str) -> Order | None:
        return None

    def query_open_orders(self, exchange: str | None = None, symbol: str | None = None) -> list[Order]:
        return []

    def query_positions(self, exchange: str | None = None, symbol: str | None = None) -> list[Position]:
        return []

    def query_balance(self, exchange: str | None = None) -> list[Balance]:
        return []

    def query_funding_payments(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        since_ms: int | None = None,
    ) -> list[FundingPayment]:
        return []

    def register_order_callback(self, callback: OrderUpdateCallback) -> None:
        self._order_callbacks.append(callback)

    def register_fill_callback(self, callback: FillCallback) -> None:
        self._fill_callbacks.append(callback)


class NoOpStrategy(Strategy):
    """Empty skeleton Strategy producing zero signals."""


class NullRiskEngine(RiskEngine):
    def check_pre_trade(
        self,
        request: OrderRequest,
        spec: SymbolSpec,
        positions: list[Position],
        balances: list[Balance],
        now_ms: int,
    ) -> None:
        return None

    def check_post_trade(
        self,
        fill: Fill,
        mark_price: Decimal | None = None,
        funding_rate: Decimal | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def trigger_kill_switch(
        self,
        broker: BrokerAdapter,
        close_positions: bool = False,
        reason: str = "manual",
    ) -> dict[str, Any]:
        return {"triggered": True, "canceled_orders": 0, "closed_positions": 0, "reason": reason}


class NullStrategySandbox(StrategySandbox):
    def validate_code(self, code: str) -> dict[str, Any]:
        return {"passed": True, "violations": []}

    def execute_isolated(
        self,
        code: str,
        entrypoint_payload: dict[str, Any],
        timeout_sec: float | None = None,
        max_memory_mb: int | None = None,
    ) -> dict[str, Any]:
        return {"ok": True, "output": None}


class NullReconciler(Reconciler):
    def reconcile_exchange_vs_local(self, run_id: str, broker: BrokerAdapter) -> dict[str, Any]:
        return {"run_id": run_id, "discrepancies": []}

    def reconcile_shadow_vs_live(self, shadow_run_id: str, live_run_id: str) -> dict[str, Any]:
        return {"shadow_run_id": shadow_run_id, "live_run_id": live_run_id, "discrepancies": []}

    def reconcile_backtest_vs_shadow(self, backtest_run_id: str, shadow_run_id: str) -> dict[str, Any]:
        return {"backtest_run_id": backtest_run_id, "shadow_run_id": shadow_run_id, "discrepancies": []}


class NullRunner(Runner):
    def __init__(self, record: RunRecord) -> None:
        self._record = record
        self._stopped = False

    def run(self) -> RunRecord:
        return self._record

    def stop(self) -> None:
        self._stopped = True
