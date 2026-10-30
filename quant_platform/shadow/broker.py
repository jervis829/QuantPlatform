"""ShadowBroker: real-time market data + simulated orderbook/trade-queue fills isolated from Live keys (P7)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from quant_platform.backtest.broker import BacktestBroker, _RestingOrderEntry
from quant_platform.backtest.matching import SlippageModelConfig
from quant_platform.core.enums import RunEnv
from quant_platform.core.interfaces import Clock, DataFeed
from quant_platform.core.models import Fill, Order, OrderBookSnapshot, OrderRequest, Tick
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.shadow.params import ShadowSimulationParams, assert_no_live_credentials


class ShadowBroker(BacktestBroker):
    """
    Shadow environment BrokerAdapter (Red Line 4 & 5):
    - Shares exact Maker/Taker fee, slippage, margin, Mark-Price liquidation, and funding logic with BacktestBroker;
    - Persists all state under `RunEnv.SHADOW` and stores `ShadowSimulationParams` metadata;
    - Physically rejects any live trading API credentials (`ShadowIsolationError`);
    - Enforces simulated flight latency (`latency_ms`) before resting orders participate in queue drainage.
    """

    def __init__(
        self,
        *,
        run_id: str,
        strategy_id: str,
        clock: Clock,
        data_feed: DataFeed,
        ledger: UnifiedLedgerRepository | None = None,
        sim_params: ShadowSimulationParams | None = None,
        initial_balances: dict[str, Decimal] | None = None,
        default_exchange: str = "binance",
        forbidden_credentials: dict[str, Any] | None = None,
    ) -> None:
        assert_no_live_credentials(forbidden_credentials)
        self.sim_params = sim_params or ShadowSimulationParams()
        self._persisted_params_snapshot: dict[str, Any] = self.sim_params.model_dump(mode="json")
        self._order_activation_ts: dict[str, int] = {}

        super().__init__(
            env=RunEnv.SHADOW,
            run_id=run_id,
            strategy_id=strategy_id,
            clock=clock,
            data_feed=data_feed,
            ledger=ledger,
            initial_balances=initial_balances,
            default_exchange=default_exchange,
            vip_level=self.sim_params.vip_level,
            use_platform_token_discount=self.sim_params.use_platform_token_discount,
            slippage_config=SlippageModelConfig(
                queue_ahead_ratio=self.sim_params.queue_ahead_ratio,
                trade_participation_rate=self.sim_params.trade_participation_rate,
                extra_impact_bps=self.sim_params.extra_impact_bps,
            ),
        )

    def persisted_sim_params(self) -> dict[str, Any]:
        return dict(self._persisted_params_snapshot)

    def submit_order(self, request: OrderRequest) -> Order:
        submit_ts = self._clock.now_ms()
        self._order_activation_ts[request.client_order_id] = submit_ts + max(0, self.sim_params.latency_ms)
        return super().submit_order(request)

    def _with_latency_gated_resting(self, event_ts_ms: int, fn):
        in_flight: dict[str, _RestingOrderEntry] = {}
        for cid in list(self._resting.keys()):
            act_ts = self._order_activation_ts.get(cid, 0)
            if event_ts_ms < act_ts:
                in_flight[cid] = self._resting.pop(cid)
        try:
            return fn()
        finally:
            for cid, entry in in_flight.items():
                self._resting.setdefault(cid, entry)

    def on_orderbook(self, snapshot: OrderBookSnapshot) -> list[Fill]:
        return self._with_latency_gated_resting(
            snapshot.exchange_ts_ms,
            lambda: super(ShadowBroker, self).on_orderbook(snapshot),
        )

    def on_tick(self, tick: Tick) -> list[Fill]:
        return self._with_latency_gated_resting(
            tick.exchange_ts_ms,
            lambda: super(ShadowBroker, self).on_tick(tick),
        )
