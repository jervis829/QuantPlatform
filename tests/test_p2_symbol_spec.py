"""P2 Tests (Test-First): Versioned SymbolSpec service, exchange metadata parser, spec change detection/alerting, order normalization/validation, and cross-spec-version replay."""

from __future__ import annotations

from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from quant_platform.core import (
    ContractType,
    FeeTier,
    MarginBracket,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    RunEnv,
    SymbolSpec,
    ZERO,
)
from quant_platform.core.interfaces import NullBrokerAdapter
from quant_platform.data import SimulatedClock
from quant_platform.exceptions import SymbolSpecViolationError
from quant_platform.specs import (
    SpecEnforcedBroker,
    SymbolSpecService,
    normalize_and_validate_order,
    parse_binance_raw_symbol_spec,
)


class _RecordingTestBroker(NullBrokerAdapter):
    """Minimal recording broker double solely for P2 cross-version spec validation replay."""

    def __init__(self, env: RunEnv, run_id: str) -> None:
        super().__init__()
        self._ready = True
        self._env = env
        self._run_id = run_id
        self.accepted_orders: list[Order] = []

    def submit_order(self, request: OrderRequest) -> Order:
        order = Order(
            env=self._env,
            run_id=self._run_id,
            strategy_id="spec_replay_strat",
            client_order_id=request.client_order_id,
            exchange_order_id=f"ex_{len(self.accepted_orders) + 1}",
            exchange=request.exchange,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            price=request.price,
            quantity=request.quantity,
            filled_qty=ZERO,
            avg_fill_price=ZERO,
            remaining_qty=request.quantity,
            status=OrderStatus.NEW,
            created_ts_ms=request.created_ts_ms,
            updated_ts_ms=request.created_ts_ms,
            exchange_ts_ms=request.created_ts_ms,
            local_recv_ts_ms=request.created_ts_ms,
        )
        self.accepted_orders.append(order)
        return order


def test_parse_binance_raw_symbol_spec_without_float_conversion() -> None:
    """Verify raw Binance exchangeInfo string filters parse directly into exact Decimals."""
    raw_futures_info = {
        "symbol": "BTCUSDT",
        "contractType": "PERPETUAL",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "contractSize": "1",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }
    raw_brackets = [
        {
            "bracket": 1,
            "initialLeverage": 125,
            "notionalFloor": "0",
            "notionalCap": "50000",
            "maintMarginRatio": "0.004",
            "cum": "0",
        },
        {
            "bracket": 2,
            "initialLeverage": 100,
            "notionalFloor": "50000",
            "notionalCap": "250000",
            "maintMarginRatio": "0.005",
            "cum": "50",
        },
    ]
    spec = parse_binance_raw_symbol_spec(
        exchange="binance",
        unified_symbol="BTC/USDT:USDT",
        raw_symbol_info=raw_futures_info,
        raw_brackets=raw_brackets,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        effective_from_ms=1700000000000,
    )
    assert spec.contract_type == ContractType.PERPETUAL
    assert spec.tick_size == Decimal("0.10")
    assert spec.step_size == Decimal("0.001")
    assert spec.min_notional == Decimal("5")
    assert spec.max_leverage == Decimal("125")
    assert len(spec.margin_brackets) == 2
    assert spec.margin_brackets[1].maint_amount == Decimal("50")


def test_symbol_spec_versioning_and_change_alert(db_session: Session) -> None:
    """Detect exchange precision change (tickSize 0.01 -> 0.10), emit alert, close v1 and persist v2."""
    alerts: list[dict[str, object]] = []
    svc = SymbolSpecService(db_session, alert_callbacks=[alerts.append])

    t_v1 = 1700000000000
    t_v2 = 1700086400000

    spec_v1 = SymbolSpec(
        exchange="binance",
        symbol="ETH/USDT:USDT",
        version=1,
        effective_from_ms=t_v1,
        contract_type=ContractType.PERPETUAL,
        base_asset="ETH",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        fee_tiers=[FeeTier(vip_level=0, maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.0004"))],
        max_leverage=Decimal("100"),
        margin_brackets=[
            MarginBracket(
                bracket=1,
                notional_floor=Decimal("0"),
                notional_cap=Decimal("100000"),
                max_leverage=Decimal("100"),
                maint_margin_rate=Decimal("0.005"),
            )
        ],
    )
    stored_v1, changed_1 = svc.sync_spec(spec_v1, synced_at_ms=t_v1)
    assert changed_1 is True
    assert stored_v1.version == 1
    assert alerts == []  # initial registration is not a precision mutation accident

    # Re-syncing identical spec is idempotent
    stored_same, changed_same = svc.sync_spec(spec_v1, synced_at_ms=t_v1 + 1000)
    assert changed_same is False
    assert stored_same.version == 1

    # Exchange changes tick_size from 0.01 -> 0.10 and min_notional from 5 -> 20 at t_v2!
    spec_update = spec_v1.model_copy(
        update={
            "spec_version_id": "",
            "spec_hash": "",
            "effective_from_ms": t_v2,
            "tick_size": Decimal("0.10"),
            "min_notional": Decimal("20"),
        }
    )
    stored_v2, changed_2 = svc.sync_spec(spec_update, synced_at_ms=t_v2)
    db_session.commit()

    assert changed_2 is True
    assert stored_v2.version == 2
    assert len(alerts) == 1
    assert alerts[0]["code"] == "SYMBOL_SPEC_CHANGED"
    changes = alerts[0]["changes"]
    assert isinstance(changes, dict)
    assert changes["tick_size"] == {"old": "0.01", "new": "0.1"}
    assert changes["min_notional"] == {"old": "5", "new": "20"}

    # Historical lookup returns v1 before t_v2 and v2 at/after t_v2
    hist_old = svc.get_spec("binance", "ETH/USDT:USDT", at_ts_ms=t_v1 + 3600_000)
    hist_new = svc.get_spec("binance", "ETH/USDT:USDT", at_ts_ms=t_v2 + 3600_000)
    assert hist_old.version == 1 and hist_old.tick_size == Decimal("0.01")
    assert hist_new.version == 2 and hist_new.tick_size == Decimal("0.10")


def test_cross_spec_change_replay_and_violation_reason_codes(db_session: Session) -> None:
    """
    Acceptance:
    1) Replay across a spec-change boundary: old order validated against v1 (tick=0.01, min_notional=5),
       new order validated against v2 (tick=0.10, min_notional=20).
    2) Precision / minQty / minNotional / leverage violations are rejected with structured reason codes.
    """
    svc = SymbolSpecService(db_session)
    t_v1 = 1700000000000
    t_v2 = 1700050000000

    v1 = SymbolSpec(
        exchange="binance",
        symbol="ETH/USDT:USDT",
        version=1,
        effective_from_ms=t_v1,
        contract_type=ContractType.PERPETUAL,
        base_asset="ETH",
        quote_asset="USDT",
        margin_asset="USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"),
        max_qty=Decimal("500"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("50"),
    )
    svc.sync_spec(v1, synced_at_ms=t_v1)

    v2 = v1.model_copy(
        update={
            "spec_version_id": "",
            "spec_hash": "",
            "effective_from_ms": t_v2,
            "tick_size": Decimal("0.10"),
            "step_size": Decimal("0.10"),
            "min_qty": Decimal("0.10"),
            "min_notional": Decimal("20"),
            "max_leverage": Decimal("20"),
        }
    )
    svc.sync_spec(v2, synced_at_ms=t_v2)
    db_session.commit()

    clock = SimulatedClock(t_v1 + 1000)
    inner = _RecordingTestBroker(env=RunEnv.BACKTEST, run_id="run_spec_replay")
    enforced_broker = SpecEnforcedBroker(inner=inner, spec_service=svc, clock=clock, auto_round=False)

    # At t_v1 + 1000 (v1 active): price=2000.05 (multiple of 0.01) & qty=0.05 (notional=100.0025 >= 5) -> ACCEPTED under v1!
    order_old = enforced_broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="ETH/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("2000.05"),
            quantity=Decimal("0.05"),
            leverage=Decimal("25"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert order_old.status == OrderStatus.NEW
    assert ":v1:" in order_old.symbol_spec_version

    # Advance clock across spec change boundary to t_v2 + 1000 (v2 active: tick=0.10, step=0.10, min_notional=20, max_lev=20)
    clock.advance_to(t_v2 + 1000)

    # Same price 2000.05 is NOW rejected with SPEC_PRICE_TICK_VIOLATION under v2!
    with pytest.raises(SymbolSpecViolationError) as exc_tick:
        enforced_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="ETH/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("2000.05"),
                quantity=Decimal("0.20"),
                leverage=Decimal("10"),
                created_ts_ms=clock.now_ms(),
            )
        )
    assert exc_tick.value.code == "SPEC_PRICE_TICK_VIOLATION"

    # Bad step size (0.15 when step=0.10) -> SPEC_QTY_STEP_VIOLATION
    with pytest.raises(SymbolSpecViolationError) as exc_step:
        enforced_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="ETH/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("2000.10"),
                quantity=Decimal("0.15"),
                leverage=Decimal("10"),
                created_ts_ms=clock.now_ms(),
            )
        )
    assert exc_step.value.code == "SPEC_QTY_STEP_VIOLATION"

    # Below minQty (0.00 when min_qty=0.10) -> SPEC_MIN_QTY_VIOLATION
    with pytest.raises(SymbolSpecViolationError) as exc_min_qty:
        enforced_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="ETH/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("2000.10"),
                quantity=Decimal("0"),
                leverage=Decimal("10"),
                created_ts_ms=clock.now_ms(),
            )
        )
    assert exc_min_qty.value.code == "SPEC_MIN_QTY_VIOLATION"

    # Below minNotional (price=100.0, qty=0.10 => notional=10 < 20) -> SPEC_MIN_NOTIONAL_VIOLATION
    with pytest.raises(SymbolSpecViolationError) as exc_notional:
        enforced_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="ETH/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("100.00"),
                quantity=Decimal("0.10"),
                leverage=Decimal("10"),
                created_ts_ms=clock.now_ms(),
            )
        )
    assert exc_notional.value.code == "SPEC_MIN_NOTIONAL_VIOLATION"

    # Above maxLeverage (25 > 20 under v2) -> SPEC_MAX_LEVERAGE_EXCEEDED
    with pytest.raises(SymbolSpecViolationError) as exc_lev:
        enforced_broker.submit_order(
            OrderRequest(
                exchange="binance",
                symbol="ETH/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                price=Decimal("2000.10"),
                quantity=Decimal("0.20"),
                leverage=Decimal("25"),
                created_ts_ms=clock.now_ms(),
            )
        )
    assert exc_lev.value.code == "SPEC_MAX_LEVERAGE_EXCEEDED"

    # Valid v2 order succeeds and links to v2 spec_version_id!
    order_new = enforced_broker.submit_order(
        OrderRequest(
            exchange="binance",
            symbol="ETH/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("2000.10"),
            quantity=Decimal("0.20"),
            leverage=Decimal("10"),
            created_ts_ms=clock.now_ms(),
        )
    )
    assert order_new.status == OrderStatus.NEW
    assert ":v2:" in order_new.symbol_spec_version

    # Also test auto_round=True helper for strategy intent conversion
    active_v2 = svc.get_spec("binance", "ETH/USDT:USDT", at_ts_ms=clock.now_ms())
    normalized = normalize_and_validate_order(
        OrderRequest(
            exchange="binance",
            symbol="ETH/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("2000.19"),
            quantity=Decimal("0.29"),
            leverage=Decimal("10"),
            created_ts_ms=clock.now_ms(),
        ),
        spec=active_v2,
        auto_round=True,
    )
    assert normalized.price == Decimal("2000.10")
    assert normalized.quantity == Decimal("0.20")
