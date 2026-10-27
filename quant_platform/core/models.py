"""Core domain models shared identically across Backtest, Shadow, and Live."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_platform import ENGINE_VERSION
from quant_platform.core.decimal_utils import ONE, StrictDecimal, ZERO
from quant_platform.core.enums import (
    ContractType,
    InstrumentStatus,
    LiquidityRole,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    RunEnv,
    RunStatus,
    SignalAction,
    TimeInForce,
)
from quant_platform.core.hashing import canonical_json, sha256_hex


class DomainModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="forbid",
    )


class Instrument(DomainModel):
    exchange: str
    symbol: str
    base_asset: str
    quote_asset: str
    settle_asset: str
    contract_type: ContractType = ContractType.SPOT
    status: InstrumentStatus = InstrumentStatus.TRADING

    @property
    def instrument_key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


class FeeTier(DomainModel):
    vip_level: int = 0
    maker_fee: StrictDecimal
    taker_fee: StrictDecimal
    platform_token_discount: StrictDecimal = ZERO


class MarginBracket(DomainModel):
    bracket: int = 1
    notional_floor: StrictDecimal = ZERO
    notional_cap: StrictDecimal
    max_leverage: StrictDecimal
    maint_margin_rate: StrictDecimal
    maint_amount: StrictDecimal = ZERO


class SymbolSpec(DomainModel):
    spec_version_id: str = ""
    exchange: str
    symbol: str
    version: int = 1
    effective_from_ms: int = 0
    effective_to_ms: int | None = None
    contract_type: ContractType = ContractType.SPOT
    base_asset: str
    quote_asset: str
    margin_asset: str
    tick_size: StrictDecimal
    step_size: StrictDecimal
    min_qty: StrictDecimal
    max_qty: StrictDecimal = Decimal("1000000000")
    min_notional: StrictDecimal
    contract_size: StrictDecimal = ONE
    maker_fee: StrictDecimal = Decimal("0.001")
    taker_fee: StrictDecimal = Decimal("0.001")
    fee_tiers: list[FeeTier] = Field(default_factory=list)
    max_leverage: StrictDecimal = ONE
    margin_brackets: list[MarginBracket] = Field(default_factory=list)
    funding_interval_hours: int = 8
    settlement_ts_ms: int | None = None
    delisted_at_ms: int | None = None
    spec_hash: str = ""

    @model_validator(mode="after")
    def _finalize_spec(self) -> "SymbolSpec":
        if self.tick_size <= ZERO:
            raise ValueError("tick_size must be > 0")
        if self.step_size <= ZERO:
            raise ValueError("step_size must be > 0")
        if self.min_qty < ZERO or self.max_qty < self.min_qty:
            raise ValueError("Invalid min_qty / max_qty bounds")
        if self.contract_size <= ZERO:
            raise ValueError("contract_size must be > 0")
        if not self.spec_hash:
            payload = {
                "exchange": self.exchange,
                "symbol": self.symbol,
                "version": self.version,
                "effective_from_ms": self.effective_from_ms,
                "effective_to_ms": self.effective_to_ms,
                "contract_type": self.contract_type.value,
                "base_asset": self.base_asset,
                "quote_asset": self.quote_asset,
                "margin_asset": self.margin_asset,
                "tick_size": self.tick_size,
                "step_size": self.step_size,
                "min_qty": self.min_qty,
                "max_qty": self.max_qty,
                "min_notional": self.min_notional,
                "contract_size": self.contract_size,
                "maker_fee": self.maker_fee,
                "taker_fee": self.taker_fee,
                "fee_tiers": [t.model_dump(mode="json") for t in self.fee_tiers],
                "max_leverage": self.max_leverage,
                "margin_brackets": [b.model_dump(mode="json") for b in self.margin_brackets],
                "funding_interval_hours": self.funding_interval_hours,
                "settlement_ts_ms": self.settlement_ts_ms,
                "delisted_at_ms": self.delisted_at_ms,
            }
            self.spec_hash = sha256_hex(canonical_json(payload))
        if not self.spec_version_id:
            self.spec_version_id = f"{self.exchange}:{self.symbol}:v{self.version}:{self.spec_hash[:12]}"
        return self


class Bar(DomainModel):
    exchange: str
    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: StrictDecimal
    high: StrictDecimal
    low: StrictDecimal
    close: StrictDecimal
    volume: StrictDecimal
    quote_volume: StrictDecimal = ZERO
    trade_count: int = 0
    is_closed: bool = True
    exchange_ts_ms: int
    local_recv_ts_ms: int


class Tick(DomainModel):
    exchange: str
    symbol: str
    trade_id: str
    price: StrictDecimal
    quantity: StrictDecimal
    is_buyer_maker: bool = False
    seq_id: int | None = None
    exchange_ts_ms: int
    local_recv_ts_ms: int


class OrderBookLevel(DomainModel):
    price: StrictDecimal
    quantity: StrictDecimal


class OrderBookSnapshot(DomainModel):
    exchange: str
    symbol: str
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    last_update_id: int
    exchange_ts_ms: int
    local_recv_ts_ms: int

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / Decimal("2")
        return None


class OrderBookDelta(DomainModel):
    exchange: str
    symbol: str
    first_update_id: int
    final_update_id: int
    prev_final_update_id: int | None = None
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)
    exchange_ts_ms: int
    local_recv_ts_ms: int


class MarkIndexPrice(DomainModel):
    exchange: str
    symbol: str
    mark_price: StrictDecimal
    index_price: StrictDecimal
    estimated_settle_price: StrictDecimal | None = None
    next_funding_rate: StrictDecimal = ZERO
    next_funding_ts_ms: int | None = None
    exchange_ts_ms: int
    local_recv_ts_ms: int


class FundingRatePoint(DomainModel):
    exchange: str
    symbol: str
    funding_rate: StrictDecimal
    mark_price: StrictDecimal
    funding_ts_ms: int
    local_recv_ts_ms: int


class OrderRequest(DomainModel):
    client_order_id: str = Field(default_factory=lambda: f"cid_{uuid.uuid4().hex[:24]}")
    exchange: str
    symbol: str
    side: OrderSide
    position_side: PositionSide = PositionSide.NET
    order_type: OrderType = OrderType.LIMIT
    time_in_force: TimeInForce = TimeInForce.GTC
    quantity: StrictDecimal
    price: StrictDecimal | None = None
    stop_price: StrictDecimal | None = None
    post_only: bool = False
    reduce_only: bool = False
    leverage: StrictDecimal = ONE
    margin_mode: MarginMode = MarginMode.CROSS
    signal_id: str | None = None
    created_ts_ms: int = 0


class Order(DomainModel):
    env: RunEnv
    run_id: str
    strategy_id: str
    client_order_id: str
    exchange_order_id: str | None = None
    exchange: str
    symbol: str
    side: OrderSide
    position_side: PositionSide = PositionSide.NET
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    reduce_only: bool = False
    price: StrictDecimal | None = None
    stop_price: StrictDecimal | None = None
    quantity: StrictDecimal
    filled_qty: StrictDecimal = ZERO
    avg_fill_price: StrictDecimal = ZERO
    remaining_qty: StrictDecimal
    fee_total: StrictDecimal = ZERO
    fee_asset: str = ""
    leverage: StrictDecimal = ONE
    margin_mode: MarginMode = MarginMode.CROSS
    status: OrderStatus = OrderStatus.NEW
    reject_reason: str | None = None
    signal_id: str | None = None
    symbol_spec_version: str = ""
    created_ts_ms: int
    updated_ts_ms: int
    exchange_ts_ms: int
    local_recv_ts_ms: int


class Fill(DomainModel):
    fill_id: str = Field(default_factory=lambda: f"fill_{uuid.uuid4().hex[:20]}")
    env: RunEnv
    run_id: str
    strategy_id: str
    client_order_id: str
    exchange_order_id: str | None = None
    exchange_trade_id: str | None = None
    exchange: str
    symbol: str
    side: OrderSide
    liquidity: LiquidityRole
    price: StrictDecimal
    quantity: StrictDecimal
    quote_qty: StrictDecimal
    reference_price: StrictDecimal
    slippage_bps: StrictDecimal = ZERO
    fee: StrictDecimal
    fee_asset: str
    realized_pnl: StrictDecimal = ZERO
    mark_price: StrictDecimal | None = None
    snapshot_ref: str | None = None
    exchange_ts_ms: int
    local_recv_ts_ms: int


class Position(DomainModel):
    env: RunEnv
    run_id: str
    strategy_id: str
    exchange: str
    symbol: str
    position_side: PositionSide = PositionSide.NET
    margin_mode: MarginMode = MarginMode.CROSS
    leverage: StrictDecimal = ONE
    quantity: StrictDecimal = ZERO  # Signed for NET (+long, -short)
    entry_price: StrictDecimal = ZERO
    mark_price: StrictDecimal = ZERO
    liquidation_price: StrictDecimal | None = None
    unrealized_pnl: StrictDecimal = ZERO
    realized_pnl: StrictDecimal = ZERO
    isolated_margin: StrictDecimal = ZERO
    initial_margin: StrictDecimal = ZERO
    maintenance_margin: StrictDecimal = ZERO
    cumulative_funding: StrictDecimal = ZERO
    updated_ts_ms: int


class Balance(DomainModel):
    env: RunEnv
    run_id: str
    exchange: str
    account_id: str = "default"
    asset: str
    total: StrictDecimal
    available: StrictDecimal
    locked: StrictDecimal = ZERO
    borrowed: StrictDecimal = ZERO
    accrued_interest: StrictDecimal = ZERO
    unrealized_pnl: StrictDecimal = ZERO
    ts_ms: int


class FundingPayment(DomainModel):
    payment_id: str = Field(default_factory=lambda: f"fund_{uuid.uuid4().hex[:20]}")
    env: RunEnv
    run_id: str
    exchange: str
    symbol: str
    funding_rate: StrictDecimal
    mark_price: StrictDecimal
    position_qty: StrictDecimal
    amount: StrictDecimal  # Positive = received into account, Negative = paid out of account
    asset: str
    settlement_ts_ms: int
    local_recv_ts_ms: int


class EquityPoint(DomainModel):
    env: RunEnv
    run_id: str
    ts_ms: int
    equity: StrictDecimal
    cash: StrictDecimal
    position_value: StrictDecimal = ZERO
    unrealized_pnl: StrictDecimal = ZERO
    realized_pnl: StrictDecimal = ZERO
    cumulative_fees: StrictDecimal = ZERO
    cumulative_funding: StrictDecimal = ZERO
    cumulative_interest: StrictDecimal = ZERO
    drawdown_pct: StrictDecimal = ZERO
    gross_exposure: StrictDecimal = ZERO
    net_exposure: StrictDecimal = ZERO


class Signal(DomainModel):
    signal_id: str = Field(default_factory=lambda: f"sig_{uuid.uuid4().hex[:20]}")
    strategy_id: str
    exchange: str
    symbol: str
    action: SignalAction
    quantity: StrictDecimal
    limit_price: StrictDecimal | None = None
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    reduce_only: bool = False
    reason: str = ""
    ts_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class TargetPosition(DomainModel):
    signal_id: str = Field(default_factory=lambda: f"tpos_{uuid.uuid4().hex[:20]}")
    strategy_id: str
    exchange: str
    symbol: str
    target_qty: StrictDecimal  # Signed target position quantity
    limit_price: StrictDecimal | None = None
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    urgency: str = "normal"
    reason: str = ""
    ts_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


StrategyIntent = Signal | TargetPosition


class StrategyConfig(DomainModel):
    strategy_id: str
    strategy_version_id: str
    name: str
    exchanges: list[str]
    symbols: list[str]
    bar_interval: str = "1m"
    params: dict[str, Any] = Field(default_factory=dict)
    margin_mode: MarginMode = MarginMode.CROSS
    default_leverage: StrictDecimal = ONE
    vip_level: int = 0
    use_platform_token_discount: bool = False
    rng_seed: int = 42
    initial_balances: dict[str, StrictDecimal] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class RunRecord(DomainModel):
    run_id: str
    env: RunEnv
    strategy_id: str
    strategy_version_id: str
    strategy_code_hash: str
    config_snapshot: dict[str, Any]
    dataset_version: str
    symbol_spec_version: str
    engine_version: str = ENGINE_VERSION
    rng_seed: int = 42
    status: RunStatus = RunStatus.CREATED
    started_at_ms: int
    ended_at_ms: int | None = None
    error_message: str | None = None
    report: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
