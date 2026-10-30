"""NL -> LLM -> Pydantic -> AST -> Sandbox Smoke -> Backtest Gate -> Mandatory Manual Approval -> Store (P10)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from quant_platform.backtest.engine import BacktestEngine
from quant_platform.backtest.report import compute_return_series_metrics
from quant_platform.core.decimal_utils import StrictDecimal, format_decimal
from quant_platform.core.enums import ApprovalStatus, MarginMode, StrategySourceType
from quant_platform.core.interfaces import Clock
from quant_platform.core.models import Bar, StrategyConfig, SymbolSpec
from quant_platform.data import HistoricalDataFeed, SimulatedClock
from quant_platform.db.schema import StrategyVersionRecord
from quant_platform.exceptions import ApprovalRequiredError, QuantPlatformError
from quant_platform.nl_generator.providers import LLMCodeGenerator
from quant_platform.nl_generator.spec_parser import NLStrategySpec, parse_nl_prompt_to_spec
from quant_platform.strategy.ast_checker import ASTSecurityChecker
from quant_platform.strategy.registry import compile_strategy_class, extract_param_schema_and_defaults
from quant_platform.strategy.version_store import StrategyVersionStore


class BacktestGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_trades: int = 0
    max_drawdown_pct: StrictDecimal = Decimal("0.50")
    initial_usdt: StrictDecimal = Decimal("10000")


class BacktestGateFailedError(QuantPlatformError):
    """Raised when an NL-generated strategy fails the automated historical backtest gate."""

    def __init__(self, reason: str, report: dict[str, Any]) -> None:
        super().__init__(reason, code="BACKTEST_GATE_FAILED", details=report)


class NLStrategyGenerationPipeline:
    """
    End-to-end Natural Language Strategy Generation pipeline sharing the exact security, sandbox,
    and approval gates as uploaded Python strategies (Red Lines 7 & 8).
    """

    def __init__(
        self,
        *,
        session: Session,
        version_store: StrategyVersionStore,
        llm_provider: LLMCodeGenerator,
        clock: Clock,
        max_repair_attempts: int = 2,
    ) -> None:
        self.session = session
        self.version_store = version_store
        self.llm_provider = llm_provider
        self.clock = clock
        self.max_repair_attempts = max_repair_attempts
        self._checker = ASTSecurityChecker()

    def generate_and_register(
        self,
        *,
        strategy_id: str,
        name: str,
        prompt: str,
        author: str,
        gate_bars: list[Bar],
        gate_spec: SymbolSpec,
        gate_config: BacktestGateConfig | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        spec: NLStrategySpec = parse_nl_prompt_to_spec(prompt)
        gate_cfg = gate_config or BacktestGateConfig()

        last_err: Exception | None = None
        prev_err_msg: str | None = None
        code = ""
        cls = None
        validated_params: dict[str, Any] = {}
        repair_attempts = 0

        for attempt in range(self.max_repair_attempts + 1):
            code = self.llm_provider.synthesize_code(spec, previous_error=prev_err_msg)
            try:
                # 1. Shared AST security + anti-lookahead check
                self._checker.assert_safe(code)
                # 2. Shared Pydantic ParamModel schema extraction
                cls = compile_strategy_class(code, checker=self._checker)
                _, validated_params = extract_param_schema_and_defaults(cls, None)
                # 3. Shared isolated sandbox smoke test
                self.version_store.sandbox.execute_isolated(
                    code,
                    {"action": "smoke_test", "params": validated_params},
                )
                repair_attempts = attempt
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                prev_err_msg = f"{type(exc).__name__}: {exc}"

        if last_err is not None or cls is None:
            assert last_err is not None
            raise last_err

        # 4. Automated Historical Backtest Gate
        bt_clock = SimulatedClock(gate_bars[0].open_time_ms if gate_bars else self.clock.now_ms())
        bt_feed = HistoricalDataFeed(clock=bt_clock)
        bt_feed.register_symbol_spec(gate_spec)
        if gate_bars:
            bt_feed.load_bars(gate_spec.exchange, gate_spec.symbol, gate_bars[0].interval, gate_bars)

        strat_cfg = StrategyConfig(
            strategy_id=strategy_id,
            strategy_version_id=f"{strategy_id}:gate",
            name=name,
            exchanges=[gate_spec.exchange],
            symbols=[gate_spec.symbol],
            bar_interval=gate_bars[0].interval if gate_bars else "1m",
            params=validated_params,
            margin_mode=MarginMode.CROSS,
            default_leverage=Decimal("3"),
            initial_balances={gate_spec.margin_asset: gate_cfg.initial_usdt},
        )
        bt_engine = BacktestEngine(
            db_session=self.session,
            clock=bt_clock,
            data_feed=bt_feed,
            strategy_cls=cls,
            strategy_code=code,
            config=strat_cfg,
        )
        bt_res = bt_engine.run()
        ret_metrics = compute_return_series_metrics(bt_res.equity_curve)
        max_dd_abs = Decimal(str(round(abs(ret_metrics["max_drawdown"]), 8)))
        total_trades = len(bt_res.fills)

        gate_report = {
            "passed": total_trades >= gate_cfg.min_trades and max_dd_abs <= gate_cfg.max_drawdown_pct,
            "run_id": bt_res.run_record.run_id,
            "total_trades": total_trades,
            "max_drawdown_pct": format_decimal(max_dd_abs),
            "net_pnl": format_decimal(bt_res.net_pnl),
            "sharpe": ret_metrics["sharpe"],
        }
        if not gate_report["passed"]:
            raise BacktestGateFailedError(
                f"NL strategy failed backtest gate (trades={total_trades}, min={gate_cfg.min_trades}; "
                f"max_dd={max_dd_abs}, limit={gate_cfg.max_drawdown_pct})",
                gate_report,
            )

        # 5. Persist via StrategyVersionStore with mandatory ApprovalStatus.PENDING (never auto-approved!)
        ver_record = self.version_store.submit_strategy_code(
            strategy_id=strategy_id,
            name=name,
            code=code,
            author=author,
            ts_ms=self.clock.now_ms(),
            description=description or f"Generated from NL prompt: {prompt}",
            params=validated_params,
            source_type=StrategySourceType.NL_GENERATED,
            generation_prompt=prompt,
            backtest_gate_report=gate_report,
        )
        return {
            "spec": spec,
            "version_record": ver_record,
            "backtest_gate": gate_report,
            "repair_attempts": repair_attempts,
        }

    def assert_approved_for_execution(self, version_id: str) -> StrategyVersionRecord:
        ver = self.version_store.get_version(version_id)
        if ver.approval_status != ApprovalStatus.APPROVED.value:
            raise ApprovalRequiredError(version_id=version_id, status=ver.approval_status)
        return ver
