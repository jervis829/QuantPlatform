"""P3 Tests (Test-First): Strategy lifecycle, Pydantic param schema, 10 malicious strategy AST blocks, sandbox timeout/OOM kill/network isolation, and version/audit store."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.db.schema import AuditLogRecord
from quant_platform.exceptions import ASTSecurityError, SandboxExecutionError
from quant_platform.strategy import (
    ASTSecurityChecker,
    ContainerStrategySandbox,
    StrategyRegistry,
    StrategyVersionStore,
)


VALID_MA_STRATEGY_CODE = '''
from decimal import Decimal
from pydantic import BaseModel, Field
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal
from quant_platform.core.enums import SignalAction, OrderType
from quant_platform.strategy.indicators import sma


class MovingAverageParams(BaseModel):
    fast_window: int = Field(default=3, ge=2, le=50, description="Fast SMA window")
    slow_window: int = Field(default=5, ge=3, le=200, description="Slow SMA window")
    trade_qty: Decimal = Field(default=Decimal("0.01"), gt=Decimal("0"), description="Order quantity")


class MovingAverageCrossStrategy(Strategy):
    ParamModel = MovingAverageParams

    def on_start(self):
        self.history = []
        return None

    def on_bar(self, bar: Bar):
        self.history.append(bar.close)
        if len(self.history) < self.params.slow_window:
            return None
        fast_val = sma(self.history, self.params.fast_window)
        slow_val = sma(self.history, self.params.slow_window)
        if fast_val > slow_val:
            return [
                Signal(
                    strategy_id=self.ctx.config.strategy_id,
                    exchange=bar.exchange,
                    symbol=bar.symbol,
                    action=SignalAction.BUY,
                    quantity=self.params.trade_qty,
                    order_type=OrderType.MARKET,
                    reason="fast_above_slow",
                    ts_ms=self.ctx.now_ms,
                )
            ]
        return None
'''


MALICIOUS_SAMPLES: list[tuple[str, str, str]] = [
    (
        "1_lookahead_shift_neg",
        '''
from quant_platform.core.interfaces import Strategy
class Bad1(Strategy):
    def on_bar(self, bar):
        future = df["close"].shift(-1)
        return None
''',
        "LOOKAHEAD_NEGATIVE_SHIFT",
    ),
    (
        "2_lookahead_iloc_future",
        '''
from quant_platform.core.interfaces import Strategy
class Bad2(Strategy):
    def on_bar(self, bar):
        nxt = bars.iloc[i + 1]
        return None
''',
        "LOOKAHEAD_FUTURE_INDEX",
    ),
    (
        "3_eval_exec",
        '''
from quant_platform.core.interfaces import Strategy
class Bad3(Strategy):
    def on_bar(self, bar):
        return eval("1 + 1")
''',
        "FORBIDDEN_BUILTIN",
    ),
    (
        "4_dunder_import",
        '''
from quant_platform.core.interfaces import Strategy
class Bad4(Strategy):
    def on_bar(self, bar):
        mod = __import__("os")
        return None
''',
        "FORBIDDEN_BUILTIN",
    ),
    (
        "5_os_subprocess_socket_requests",
        '''
import os, subprocess, socket, requests
from quant_platform.core.interfaces import Strategy
class Bad5(Strategy):
    pass
''',
        "FORBIDDEN_IMPORT",
    ),
    (
        "6_file_write",
        '''
from quant_platform.core.interfaces import Strategy
class Bad6(Strategy):
    def on_bar(self, bar):
        f = open("/tmp/hack.txt", "w")
        f.write("pwned")
        return None
''',
        "FORBIDDEN_BUILTIN",
    ),
    (
        "7_wallclock_datetime_now",
        '''
import datetime
from quant_platform.core.interfaces import Strategy
class Bad7(Strategy):
    def on_bar(self, bar):
        t = datetime.datetime.now()
        return None
''',
        "FORBIDDEN_IMPORT",
    ),
    (
        "8_infinite_while",
        '''
from quant_platform.core.interfaces import Strategy
class Bad8(Strategy):
    def on_bar(self, bar):
        while True:
            x = 1
        return None
''',
        "INFINITE_WHILE_LOOP",
    ),
    (
        "9_unbounded_recursion",
        '''
from quant_platform.core.interfaces import Strategy
class Bad9(Strategy):
    def recurse(self, n: int) -> int:
        return self.recurse(n + 1)
    def on_bar(self, bar):
        self.recurse(0)
        return None
''',
        "RECURSION_DETECTED",
    ),
    (
        "10_excessive_memory_alloc",
        '''
from quant_platform.core.interfaces import Strategy
class Bad10(Strategy):
    def on_bar(self, bar):
        huge = [0] * (10 ** 9)
        return None
''',
        "EXCESSIVE_MEMORY_ALLOCATION",
    ),
]


@pytest.mark.parametrize("sample_name,code,expected_rule", MALICIOUS_SAMPLES)
def test_10_malicious_strategy_samples_all_intercepted_with_reason(
    sample_name: str,
    code: str,
    expected_rule: str,
) -> None:
    """Acceptance ①: All 10 malicious/dangerous strategy samples are blocked with explicit rule & message."""
    checker = ASTSecurityChecker()
    with pytest.raises(ASTSecurityError) as exc_info:
        checker.assert_safe(code)
    rules = {v["rule"] for v in exc_info.value.violations}
    assert expected_rule in rules, f"Sample {sample_name} expected {expected_rule}, got {rules}"
    assert len(exc_info.value.message) > 10


def test_sandbox_timeout_and_memory_limit_and_no_network() -> None:
    """Acceptance ③: Sandbox forcibly terminates timeout & memory-exceeding code and isolates network."""
    sandbox = ContainerStrategySandbox(default_timeout_sec=0.5, default_max_memory_mb=128)

    # 1. CPU Timeout sample (bounded AST syntax, huge runtime nested iteration)
    cpu_hog_code = '''
from quant_platform.core.interfaces import Strategy
class CpuHog(Strategy):
    def on_start(self):
        acc = 0
        for i in range(50000):
            for j in range(50000):
                acc += (i ^ j)
        return None
'''
    with pytest.raises(SandboxExecutionError) as exc_timeout:
        sandbox.execute_isolated(
            cpu_hog_code,
            {"action": "smoke_test"},
            timeout_sec=0.4,
            max_memory_mb=192,
        )
    assert exc_timeout.value.code == "SANDBOX_TIMEOUT"

    # 2. Dynamic Memory OOM sample (grows list dynamically across loop steps to exceed 128 MB RLIMIT_AS)
    oom_code = '''
from quant_platform.core.interfaces import Strategy
class MemoryHog(Strategy):
    def on_start(self):
        chunks = []
        for _ in range(400):
            chunks.append(bytearray(1024 * 1024))
        return None
'''
    with pytest.raises(SandboxExecutionError) as exc_oom:
        sandbox.execute_isolated(
            oom_code,
            {"action": "smoke_test"},
            timeout_sec=4.0,
            max_memory_mb=128,
        )
    assert exc_oom.value.code == "SANDBOX_MEMORY_EXCEEDED"

    # 3. Verify valid strategy runs cleanly inside isolated network-free sandbox
    res = sandbox.execute_isolated(
        VALID_MA_STRATEGY_CODE,
        {"action": "smoke_test", "params": {"fast_window": 3, "slow_window": 5}},
        timeout_sec=4.0,
        max_memory_mb=256,
    )
    assert res["ok"] is True
    assert res["strategy_class"] == "MovingAverageCrossStrategy"
    assert res["network_isolated"] is True


def test_strategy_versioning_param_schema_diff_and_audit(db_session: Session) -> None:
    """Acceptance ② & Red Line 8: Pydantic schema export, parameter change creates v2 with diff + audit trail."""
    sandbox = ContainerStrategySandbox(default_timeout_sec=4.0, default_max_memory_mb=256)
    store = StrategyVersionStore(db_session, sandbox=sandbox)
    registry = StrategyRegistry()

    v1 = store.submit_strategy_code(
        strategy_id="ma_cross_btc",
        name="BTC MA Crossover",
        description="Dual SMA trend strategy",
        code=VALID_MA_STRATEGY_CODE,
        params={"fast_window": 4, "slow_window": 10, "trade_qty": "0.02"},
        author="quant_alice",
        ts_ms=1700000000000,
    )
    db_session.commit()

    assert v1.version == 1
    assert v1.approval_status == "PENDING"
    assert "fast_window" in v1.param_schema_json["properties"]
    assert v1.param_schema_json["properties"]["fast_window"]["minimum"] == 2

    # Register & discover in StrategyRegistry
    cls = registry.load_and_register("ma_cross_btc", VALID_MA_STRATEGY_CODE)
    assert cls.__name__ == "MovingAverageCrossStrategy"
    assert "ma_cross_btc" in registry.list_registered()

    # Modifying parameters ONLY MUST automatically generate version 2 with diff & audit log!
    v2 = store.update_strategy_params(
        strategy_id="ma_cross_btc",
        new_params={"fast_window": 8, "slow_window": 21, "trade_qty": "0.05"},
        author="quant_bob",
        ts_ms=1700000060000,
    )
    db_session.commit()

    assert v2.version == 2
    assert v2.code_hash == v1.code_hash
    assert "-  \"fast_window\": 4" in v2.diff_text
    assert "+  \"fast_window\": 8" in v2.diff_text

    # Manual approval updates status and appends audit log
    approved_v2 = store.approve_version(
        version_id=v2.version_id,
        approver="risk_officer_carol",
        note="Reviewed MA params",
        ts_ms=1700000120000,
    )
    db_session.commit()
    assert approved_v2.approval_status == "APPROVED"
    assert approved_v2.approved_by == "risk_officer_carol"

    logs = db_session.execute(select(AuditLogRecord).order_by(AuditLogRecord.id.asc())).scalars().all()
    actions = [lg.action for lg in logs]
    assert actions == ["STRATEGY_VERSION_CREATED", "STRATEGY_PARAMS_UPDATED", "STRATEGY_VERSION_APPROVED"]
