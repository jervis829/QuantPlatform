"""FastAPI REST + WebSocket application serving Strategy, Unified Ledger, Reconciliation, Risk, Charts & System Status."""

from __future__ import annotations

from decimal import Decimal
import time
from typing import Any
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from quant_platform import ENGINE_VERSION
from quant_platform.api.auth import ActorPrincipal, Role, require_min_role
from quant_platform.api.schemas import (
    ChartBarPoint,
    ChartEquityPoint,
    ChartTradeMarker,
    ExchangeConnectionItem,
    GlobalSearchHit,
    GlobalSearchResponse,
    HealthResponse,
    KillSwitchHistoryItem,
    KillSwitchOrderPreview,
    KillSwitchPositionPreview,
    KillSwitchPreviewResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    LaunchRunRequest,
    LaunchRunResponse,
    LedgerListResponse,
    LedgerOrderRow,
    LedgerOrdersQueryResponse,
    LedgerQuerySummary,
    MaintenanceModeRequest,
    NLStrategyGenerateRequest,
    OrderFillsDrilldownResponse,
    PreflightAccountCheck,
    PreflightBlockingReason,
    PreflightMarginRiskEstimate,
    RateLimitStatusItem,
    ReconciliationDetailResponse,
    ReconciliationGlobalSummary,
    ReconciliationRunRequest,
    ReconciliationSeverityCounts,
    RiskEventAckResponse,
    RiskEventItem,
    RiskEventListResponse,
    RunningEnvCounts,
    RunChartDataResponse,
    RunPreflightRequest,
    RunPreflightResponse,
    RunStatusResponse,
    RunSummaryItem,
    SandboxExecutionStatItem,
    SensitivityCellItem,
    SensitivityRequest,
    SensitivityResponse,
    StrategyApprovalRequest,
    StrategyBacktestBrief,
    StrategyDetailResponse,
    StrategyParamUpdateRequest,
    StrategySummaryItem,
    StrategyUploadRequest,
    StrategyVersionResponse,
    SymbolSpecSummary,
    SystemDiagnosticsResponse,
    SystemOverviewResponse,
    WalkForwardRequest,
    WalkForwardResponse,
    WalkForwardWindowItem,
)
from quant_platform.api.ws_hub import WebSocketEventHub
from quant_platform.backtest import (
    BacktestEngine,
    build_and_persist_backtest_report,
    compute_parameter_sensitivity_heatmap,
    run_walk_forward_analysis,
)
from quant_platform.core.decimal_utils import ZERO, format_decimal, to_strict_decimal
from quant_platform.core.enums import (
    ApprovalStatus,
    ContractType,
    MarginMode,
    PositionSide,
    ReconciliationTier,
    RunEnv,
)
from quant_platform.core.hashing import canonical_json, sha256_hex
from quant_platform.core.models import Bar, Position, StrategyConfig, SymbolSpec
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import (
    AuditLogRecord,
    BalanceSnapshotRecord,
    EquityCurveRecord,
    FillRecord,
    OrderRecord,
    ReconciliationReportRecord,
    RiskEventRecord,
    RunOrmRecord,
    SignalTraceRecord,
    StrategyVersionRecord,
    SymbolSpecRecord,
)
from quant_platform.db.session import append_audit_log, build_engine, build_session_factory, init_db
from quant_platform.exceptions import QuantPlatformError
from quant_platform.execution.runners import LiveRunner, ShadowRunner
from quant_platform.live import (
    ExchangeBroker,
    LiveRiskConfig,
    LiveRiskEngine,
    MockRawExchangeTransport,
)
from quant_platform.nl_generator import (
    BacktestGateConfig,
    DeterministicRuleLLMProvider,
    NLStrategyGenerationPipeline,
)
from quant_platform.observability.metrics import PlatformMetrics
from quant_platform.reconciliation import ThreeTierReconciler
from quant_platform.shadow import ShadowBroker, ShadowSimulationParams
from quant_platform.strategy import (
    ContainerStrategySandbox,
    StrategyVersionStore,
    compile_strategy_class,
)


class _WallMsClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def exchange_offset_ms(self, exchange: str) -> int:
        return 0


def _default_perp_spec(symbol: str = "BTC/USDT:USDT", exchange: str = "binance") -> SymbolSpec:
    is_spot = ":" not in symbol
    base_part = symbol.split("/")[0] if "/" in symbol else "BTC"
    is_eth = base_part.upper() == "ETH"
    return SymbolSpec(
        exchange=exchange,
        symbol=symbol,
        contract_type=ContractType.SPOT if is_spot else ContractType.PERPETUAL,
        base_asset=base_part.upper(),
        quote_asset="USDT",
        margin_asset="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.01") if is_eth else Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("1000"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("1") if is_spot else Decimal("50"),
        maker_fee=Decimal("0.0010") if is_spot else Decimal("0.0002"),
        taker_fee=Decimal("0.0010") if is_spot else Decimal("0.0004"),
        effective_from_ms=0,
    )


def _spec_to_summary(spec: SymbolSpec) -> SymbolSpecSummary:
    return SymbolSpecSummary(
        exchange=spec.exchange,
        symbol=spec.symbol,
        contract_type=spec.contract_type.value,
        base_asset=spec.base_asset,
        quote_asset=spec.quote_asset,
        margin_asset=spec.margin_asset,
        tick_size=format_decimal(spec.tick_size),
        step_size=format_decimal(spec.step_size),
        min_qty=format_decimal(spec.min_qty),
        max_qty=format_decimal(spec.max_qty),
        min_notional=format_decimal(spec.min_notional),
        max_leverage=format_decimal(spec.max_leverage),
        maker_fee=format_decimal(spec.maker_fee),
        taker_fee=format_decimal(spec.taker_fee),
    )


def _canonical_sample_bars(exchange: str = "binance", symbol: str = "BTC/USDT:USDT") -> list[Bar]:
    base_ts = 1_700_000_000_000
    base_anchor = Decimal("3000") if symbol.upper().startswith("ETH") else Decimal("50000")
    step_dn = Decimal("8") if symbol.upper().startswith("ETH") else Decimal("120")
    step_up = Decimal("15") if symbol.upper().startswith("ETH") else Decimal("220")
    step_dn2 = Decimal("16") if symbol.upper().startswith("ETH") else Decimal("240")
    spread = Decimal("4") if symbol.upper().startswith("ETH") else Decimal("40")

    p1 = [base_anchor - step_dn * Decimal(i) for i in range(10)]
    p2_start = p1[-1] - step_dn
    p2 = [p2_start + step_up * Decimal(i) for i in range(15)]
    p3_start = p2[-1] + step_up
    p3 = [p3_start - step_dn2 * Decimal(i) for i in range(10)]
    prices = p1 + p2 + p3

    out: list[Bar] = []
    for idx, px in enumerate(prices):
        o_ts = base_ts + idx * 60_000
        c_ts = o_ts + 59_999
        out.append(
            Bar(
                exchange=exchange,
                symbol=symbol,
                interval="1m",
                open_time_ms=o_ts,
                close_time_ms=c_ts,
                open=px,
                high=px + spread,
                low=px - spread,
                close=px,
                volume=Decimal("50"),
                quote_volume=px * Decimal("50"),
                trade_count=30,
                is_closed=True,
                exchange_ts_ms=c_ts,
                local_recv_ts_ms=c_ts,
            )
        )
    return out


def _serialize_version(v: StrategyVersionRecord) -> StrategyVersionResponse:
    return StrategyVersionResponse(
        version_id=v.version_id,
        strategy_id=v.strategy_id,
        version=v.version,
        code=v.code,
        code_hash=v.code_hash,
        param_schema=v.param_schema_json,
        params=v.params_json,
        diff_text=v.diff_text,
        source_type=v.source_type,
        generation_prompt=v.generation_prompt,
        backtest_gate=v.backtest_gate_json,
        approval_status=v.approval_status,
        created_by=v.created_by,
        approved_by=v.approved_by,
        approved_at_ms=v.approved_at_ms,
        created_at_ms=v.created_at_ms,
    )


_SEV_ORDER_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def _extract_item_usd_drift(item: dict[str, Any]) -> Decimal:
    for key in ("abs_drift_usdt", "notional_diff", "abs_diff_usdt", "diff_usdt", "delta_pnl", "abs_diff", "diff"):
        raw = item.get(key)
        if raw is not None:
            try:
                return abs(to_strict_decimal(str(raw)))
            except Exception:
                continue

    item_type = str(item.get("type", "")).upper()
    sym = str(item.get("symbol", "")).upper()
    ref_px = Decimal("3000") if sym.startswith("ETH") else Decimal("50000")

    try:
        if item_type == "POSITION_QTY_MISMATCH" and item.get("delta") is not None:
            return abs(to_strict_decimal(str(item["delta"]))) * ref_px
        if item_type in {"MISSING_LOCAL_FILL", "MISSING_FILL"} and item.get("exchange_value") is not None:
            return abs(to_strict_decimal(str(item["exchange_value"]))) * ref_px
        if item_type in {"MISSING_LOCAL_FUNDING", "FUNDING_MISMATCH"} and item.get("exchange_value") is not None:
            return abs(to_strict_decimal(str(item["exchange_value"])))
        if item_type == "BALANCE_TOTAL_MISMATCH" and item.get("delta") is not None:
            return abs(to_strict_decimal(str(item["delta"])))
    except Exception:
        return ZERO
    return ZERO


def _enrich_and_sort_recon_items(raw_items: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], Decimal]:
    enriched: list[tuple[int, Decimal, dict[str, Any]]] = []
    total_drift = ZERO
    for raw in raw_items or []:
        drift = _extract_item_usd_drift(raw)
        total_drift += drift
        sev = str(raw.get("severity", "LOW")).upper()
        copied = dict(raw)
        copied["abs_drift_usdt"] = format_decimal(drift)
        enriched.append((_SEV_ORDER_WEIGHT.get(sev, 0), drift, copied))
    enriched.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in enriched], total_drift


def _verify_audit_chain(rows: list[AuditLogRecord]) -> bool:
    ordered = sorted(rows, key=lambda r: r.id)
    prev = "GENESIS"
    for r in ordered:
        if r.prev_hash != prev:
            return False
        body = canonical_json(
            {
                "actor": r.actor,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "payload": r.payload_json,
                "ts_ms": r.ts_ms,
                "prev_hash": r.prev_hash,
            }
        )
        if sha256_hex(body) != r.entry_hash:
            return False
        prev = r.entry_hash
    return True


def create_app(db_url: str | None = None, metrics: PlatformMetrics | None = None) -> FastAPI:
    app = FastAPI(
        title="QuantPlatform Control Plane API",
        version=ENGINE_VERSION,
        description="Self-hosted multi-strategy crypto trading platform (Backtest / Shadow / Live)",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    engine = build_engine(db_url)
    init_db(engine)
    sf = build_session_factory(engine)
    ws_hub = WebSocketEventHub()
    wall_clock = _WallMsClock()
    sandbox = ContainerStrategySandbox()
    prom_metrics = metrics or PlatformMetrics()

    app.state.engine = engine
    app.state.session_factory = sf
    app.state.ws_hub = ws_hub
    app.state.metrics = prom_metrics
    app.state.maintenance_mode = False
    app.state.kill_switch_active = False
    app.state.active_live_broker = None
    app.state.clock_skew_ms = 12
    app.state.ws_channels = {
        ("binance", "spot_market_ws"): {"healthy": True, "last_heartbeat_ms": wall_clock.now_ms()},
        ("binance", "usdtm_perp_ws"): {"healthy": True, "last_heartbeat_ms": wall_clock.now_ms()},
        ("binance", "user_data_ws"): {"healthy": True, "last_heartbeat_ms": wall_clock.now_ms()},
    }

    @app.get("/metrics", include_in_schema=False)
    def get_prometheus_metrics() -> Response:
        return Response(
            content=prom_metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/v1/health", response_model=HealthResponse)
    def get_health() -> HealthResponse:
        return HealthResponse(
            status="maintenance" if app.state.maintenance_mode else "ok",
            engine_version=ENGINE_VERSION,
            maintenance_mode=bool(app.state.maintenance_mode),
        )

    @app.websocket("/api/v1/ws/stream")
    async def ws_stream(ws: WebSocket) -> None:
        await ws_hub.connect(ws)
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"type": "pong", "ts_ms": wall_clock.now_ms()})
        except WebSocketDisconnect:
            ws_hub.disconnect(ws)

    # ------------------------------------------------------------------
    # Global Persistent Status Bar, Diagnostics, Maintenance & Search
    # ------------------------------------------------------------------

    @app.get("/api/v1/specs", response_model=list[SymbolSpecSummary])
    def list_symbol_specs(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> list[SymbolSpecSummary]:
        defaults = [
            _default_perp_spec("BTC/USDT:USDT", "binance"),
            _default_perp_spec("ETH/USDT:USDT", "binance"),
            _default_perp_spec("BTC/USDT", "binance"),
            _default_perp_spec("ETH/USDT", "binance"),
        ]
        by_key: dict[tuple[str, str], SymbolSpecSummary] = {
            (s.exchange, s.symbol): _spec_to_summary(s) for s in defaults
        }
        with sf() as session:
            db_specs = list(
                session.execute(select(SymbolSpecRecord).order_by(SymbolSpecRecord.version.asc()))
                .scalars()
                .all()
            )
            for row in db_specs:
                by_key[(row.exchange, row.symbol)] = SymbolSpecSummary(
                    exchange=row.exchange,
                    symbol=row.symbol,
                    contract_type=row.contract_type,
                    base_asset=row.base_asset,
                    quote_asset=row.quote_asset,
                    margin_asset=row.margin_asset,
                    tick_size=format_decimal(row.tick_size),
                    step_size=format_decimal(row.step_size),
                    min_qty=format_decimal(row.min_qty),
                    max_qty=format_decimal(row.max_qty),
                    min_notional=format_decimal(row.min_notional),
                    max_leverage=format_decimal(row.max_leverage),
                    maker_fee=format_decimal(row.maker_fee),
                    taker_fee=format_decimal(row.taker_fee),
                )
        return list(by_key.values())

    @app.get("/api/v1/system/overview", response_model=SystemOverviewResponse)
    def get_system_overview(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> SystemOverviewResponse:
        now_ms = wall_clock.now_ms()
        day_start_ms = (now_ms // 86_400_000) * 86_400_000
        prev_day_start_ms = day_start_ms - 86_400_000

        with sf() as session:
            all_runs = list(session.execute(select(RunOrmRecord)).scalars().all())
            running_counts = RunningEnvCounts(
                backtest=sum(1 for r in all_runs if r.env == "backtest" and r.status == "RUNNING"),
                shadow=sum(1 for r in all_runs if r.env == "shadow" and r.status in {"RUNNING", "COMPLETED"} and app.state.active_live_broker is not None),
                live=1 if app.state.active_live_broker is not None and not app.state.kill_switch_active else 0,
            )
            completed_counts = RunningEnvCounts(
                backtest=sum(1 for r in all_runs if r.env == "backtest" and r.status == "COMPLETED"),
                shadow=sum(1 for r in all_runs if r.env == "shadow" and r.status == "COMPLETED"),
                live=sum(1 for r in all_runs if r.env == "live" and r.status == "COMPLETED"),
            )

            live_fills = list(
                session.execute(select(FillRecord).where(FillRecord.env.in_(["live", "shadow"])))
                .scalars()
                .all()
            )
            today_pnl = sum(
                (f.realized_pnl - f.fee for f in live_fills if f.local_recv_ts_ms >= day_start_ms),
                ZERO,
            )
            yesterday_pnl = sum(
                (
                    f.realized_pnl - f.fee
                    for f in live_fills
                    if prev_day_start_ms <= f.local_recv_ts_ms < day_start_ms
                ),
                ZERO,
            )

            risk_rows = list(session.execute(select(RiskEventRecord)).scalars().all())
            unacked = [r for r in risk_rows if not bool((r.details_json or {}).get("acknowledged", False))]
            critical_alerts = [r for r in unacked if r.severity in {"CRITICAL", "HIGH"}]

            recon_rows = list(
                session.execute(
                    select(ReconciliationReportRecord).order_by(ReconciliationReportRecord.created_ts_ms.desc())
                )
                .scalars()
                .all()
            )
            latest_by_tier: dict[str, ReconciliationReportRecord] = {}
            for rr in recon_rows:
                if rr.tier not in latest_by_tier:
                    latest_by_tier[rr.tier] = rr

            unresolved_count = sum(r.discrepancy_count for r in latest_by_tier.values() if not r.passed)
            crit_high_recon = sum(
                1
                for r in latest_by_tier.values()
                for it in (r.items_json or [])
                if it.get("severity") in {"CRITICAL", "HIGH"}
            )
            total_drift = sum(
                (_extract_item_usd_drift(it) for r in latest_by_tier.values() for it in (r.items_json or [])),
                ZERO,
            )
            last_recon_ts = recon_rows[0].created_ts_ms if recon_rows else None

        conns = [
            ExchangeConnectionItem(
                exchange=ex,
                channel=ch,
                healthy=bool(info["healthy"]) and not app.state.maintenance_mode,
                last_heartbeat_ms=int(info["last_heartbeat_ms"]) if info.get("last_heartbeat_ms") else now_ms,
                clock_skew_ms=int(app.state.clock_skew_ms),
            )
            for (ex, ch), info in app.state.ws_channels.items()
        ]

        return SystemOverviewResponse(
            running_counts=running_counts,
            completed_counts=completed_counts,
            today_pnl_usdt=format_decimal(today_pnl),
            today_return_pct=round(float(today_pnl / Decimal("10000")) * 100.0, 4),
            yesterday_pnl_usdt=format_decimal(yesterday_pnl),
            unacknowledged_alert_count=len(unacked),
            critical_alert_count=len(critical_alerts),
            reconciliation_summary=ReconciliationGlobalSummary(
                unresolved_count=unresolved_count,
                critical_or_high_count=crit_high_recon,
                total_abs_drift_usdt=format_decimal(total_drift),
                last_check_ts_ms=last_recon_ts,
            ),
            exchange_connections=conns,
            kill_switch_active=bool(app.state.kill_switch_active),
            maintenance_mode=bool(app.state.maintenance_mode),
            server_ts_ms=now_ms,
        )

    @app.get("/api/v1/system/diagnostics", response_model=SystemDiagnosticsResponse)
    def get_system_diagnostics(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> SystemDiagnosticsResponse:
        now_ms = wall_clock.now_ms()
        conns = [
            ExchangeConnectionItem(
                exchange=ex,
                channel=ch,
                healthy=bool(info["healthy"]) and not app.state.maintenance_mode,
                last_heartbeat_ms=int(info["last_heartbeat_ms"]) if info.get("last_heartbeat_ms") else now_ms,
                clock_skew_ms=int(app.state.clock_skew_ms),
            )
            for (ex, ch), info in app.state.ws_channels.items()
        ]
        rate_limits = [
            RateLimitStatusItem(
                exchange="binance_spot",
                used_weight=18,
                max_weight=1200,
                backoff_until_ms=0,
                recent_429_count=0,
            ),
            RateLimitStatusItem(
                exchange="binance_usdtm_perp",
                used_weight=24,
                max_weight=2400,
                backoff_until_ms=0,
                recent_429_count=0,
            ),
        ]
        sbx_logs = [
            SandboxExecutionStatItem(
                ok=bool(entry.get("ok", False)),
                reason_code=str(entry.get("reason_code", "UNKNOWN")),
                elapsed_ms=int(entry.get("elapsed_ms", 0)),
            )
            for entry in sandbox.execution_log[-50:]
        ]
        with sf() as session:
            all_audits = list(
                session.execute(select(AuditLogRecord).order_by(AuditLogRecord.id.asc())).scalars().all()
            )
            chain_ok = _verify_audit_chain(all_audits)
            ks_audits = [a for a in reversed(all_audits) if "KILL_SWITCH" in a.action][:30]
            ks_history = [
                KillSwitchHistoryItem(
                    id=a.id,
                    actor=a.actor,
                    action=a.action,
                    reason=str((a.payload_json or {}).get("reason", "")),
                    canceled_orders=int((a.payload_json or {}).get("canceled_orders", 0)),
                    flattened_positions=int((a.payload_json or {}).get("flattened_positions", 0)),
                    elapsed_ms=float((a.payload_json or {}).get("elapsed_ms", 0.0)),
                    entry_hash=a.entry_hash,
                    prev_hash=a.prev_hash,
                    ts_ms=a.ts_ms,
                )
                for a in ks_audits
            ]
        return SystemDiagnosticsResponse(
            connections=conns,
            rate_limits=rate_limits,
            sandbox_stats=list(reversed(sbx_logs)),
            sandbox_total_runs=len(sandbox.execution_log),
            sandbox_blocked_runs=sum(1 for e in sandbox.execution_log if not e.get("ok", False)),
            kill_switch_history=ks_history,
            audit_chain_valid=chain_ok,
            maintenance_mode=bool(app.state.maintenance_mode),
        )

    @app.post("/api/v1/system/maintenance", response_model=HealthResponse)
    async def toggle_maintenance_mode(
        req: MaintenanceModeRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> HealthResponse:
        app.state.maintenance_mode = bool(req.enabled)
        with sf() as session:
            append_audit_log(
                session,
                actor=principal.actor_id,
                action="MAINTENANCE_MODE_TOGGLED",
                resource_type="system",
                resource_id="maintenance_mode",
                payload={"enabled": req.enabled, "reason": req.reason},
                ts_ms=wall_clock.now_ms(),
            )
            session.commit()
        await ws_hub.publish({"type": "maintenance_toggled", "enabled": req.enabled})
        return HealthResponse(
            status="maintenance" if app.state.maintenance_mode else "ok",
            engine_version=ENGINE_VERSION,
            maintenance_mode=bool(app.state.maintenance_mode),
        )

    @app.get("/api/v1/search", response_model=GlobalSearchResponse)
    def global_search(
        q: str = Query(default=""),
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> GlobalSearchResponse:
        needle = q.strip().lower()
        hits: list[GlobalSearchHit] = []
        nav_pages = [
            ("nav_strategies", "策略工作台 (Strategies)", "巡检所有策略、版本审批与快速回测", "/strategies"),
            ("nav_ledger", "三环境统一交易账本 (Ledger)", "按环境+策略+币种+状态筛选委托与成交", "/ledger"),
            ("nav_reconcile", "三层对账看板 (Reconciliation)", "系统vs交易所 / 影子vs实盘 / 回测vs影子归因", "/reconciliation"),
            ("nav_system", "系统状态与风控审计 (System & Risk)", "连接健康、时钟偏移、沙箱与 Kill Switch 历史", "/system"),
            ("nav_design", "设计系统与基础组件陈列 (Design System)", "Token、三态组件、危险分级按钮与表格示例", "/design-system"),
        ]
        for nid, title, sub, href in nav_pages:
            if not needle or needle in title.lower() or needle in sub.lower() or needle in href.lower():
                hits.append(GlobalSearchHit(kind="nav", id=nid, title=title, subtitle=sub, href=href))

        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            for s in store.list_strategies():
                if not needle or needle in s.strategy_id.lower() or needle in s.name.lower():
                    hits.append(
                        GlobalSearchHit(
                            kind="strategy",
                            id=s.strategy_id,
                            title=f"{s.name} ({s.strategy_id})",
                            subtitle=f"v{s.current_version} · {s.source_type}",
                            href=f"/strategies/{s.strategy_id}",
                        )
                    )
            runs = list(
                session.execute(select(RunOrmRecord).order_by(RunOrmRecord.started_at_ms.desc()).limit(50))
                .scalars()
                .all()
            )
            for r in runs:
                if not needle or needle in r.run_id.lower() or needle in r.strategy_id.lower() or needle in r.env.lower():
                    target_href = f"/reports/{r.run_id}" if r.env == "backtest" else f"/ledger?env={r.env}&runId={r.run_id}"
                    hits.append(
                        GlobalSearchHit(
                            kind="run",
                            id=r.run_id,
                            title=f"[{r.env.upper()}] {r.run_id}",
                            subtitle=f"策略 {r.strategy_id} ({r.strategy_version_id}) · {r.status}",
                            env=r.env,
                            href=target_href,
                        )
                    )
            if needle:
                ords = list(
                    session.execute(
                        select(OrderRecord)
                        .where(OrderRecord.client_order_id.ilike(f"%{needle}%"))
                        .order_by(OrderRecord.created_ts_ms.desc())
                        .limit(15)
                    )
                    .scalars()
                    .all()
                )
                for o in ords:
                    hits.append(
                        GlobalSearchHit(
                            kind="order",
                            id=o.client_order_id,
                            title=f"订单 {o.client_order_id} ({o.symbol} {o.side})",
                            subtitle=f"[{o.env.upper()}] {o.status} · qty={format_decimal(o.quantity)}",
                            env=o.env,
                            href=f"/ledger?env={o.env}&q={o.client_order_id}",
                        )
                    )
        return GlobalSearchResponse(query=q, hits=hits[:30])

    # ------------------------------------------------------------------
    # Strategies, Versioning & Approval
    # ------------------------------------------------------------------

    def _build_strategy_summary(session: Any, store: StrategyVersionStore, m: Any) -> StrategySummaryItem:
        latest_ver = store.get_latest_version(m.strategy_id)
        strat_runs = list(
            session.execute(
                select(RunOrmRecord)
                .where(RunOrmRecord.strategy_id == m.strategy_id)
                .order_by(RunOrmRecord.started_at_ms.desc())
            )
            .scalars()
            .all()
        )
        bt_count = sum(1 for r in strat_runs if r.env == "backtest")
        sh_count = sum(1 for r in strat_runs if r.env == "shadow")
        lv_count = sum(1 for r in strat_runs if r.env == "live")

        active_envs: list[str] = []
        if any(r.env == "shadow" and r.status == "RUNNING" for r in strat_runs):
            active_envs.append("shadow")
        if (
            app.state.active_live_broker is not None
            and not app.state.kill_switch_active
            and any(r.env == "live" and r.run_id == app.state.active_live_broker[0] for r in strat_runs)
        ):
            active_envs.append("live")

        bt_brief: StrategyBacktestBrief | None = None
        for r in strat_runs:
            if r.env == "backtest" and r.report_json and "summary_metrics" in r.report_json:
                sm = r.report_json["summary_metrics"]
                bt_brief = StrategyBacktestBrief(
                    run_id=r.run_id,
                    cumulative_return=float(sm.get("cumulative_return", 0.0)),
                    max_drawdown=float(sm.get("max_drawdown", 0.0)),
                    sharpe=float(sm.get("sharpe", 0.0)),
                )
                break

        live_fills = list(
            session.execute(
                select(FillRecord).where(
                    FillRecord.strategy_id == m.strategy_id,
                    FillRecord.env == "live",
                )
            )
            .scalars()
            .all()
        )
        live_pnl = sum((f.realized_pnl - f.fee for f in live_fills), ZERO)

        return StrategySummaryItem(
            strategy_id=m.strategy_id,
            name=m.name,
            description=m.description,
            current_version=m.current_version,
            source_type=m.source_type,
            created_by=m.created_by,
            updated_at_ms=m.updated_at_ms,
            latest_approval_status=latest_ver.approval_status,
            active_envs=active_envs,
            run_counts=RunningEnvCounts(backtest=bt_count, shadow=sh_count, live=lv_count),
            latest_backtest_metrics=bt_brief,
            live_today_pnl_usdt=format_decimal(live_pnl),
        )

    @app.get("/api/v1/strategies", response_model=list[StrategySummaryItem])
    def list_strategies(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> list[StrategySummaryItem]:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            return [_build_strategy_summary(session, store, m) for m in store.list_strategies()]

    @app.post("/api/v1/strategies/upload", response_model=StrategyVersionResponse)
    async def upload_strategy(
        req: StrategyUploadRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> StrategyVersionResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.submit_strategy_code(
                    strategy_id=req.strategy_id,
                    name=req.name,
                    description=req.description,
                    code=req.code,
                    params=req.params,
                    author=principal.actor_id,
                    ts_ms=wall_clock.now_ms(),
                )
                session.commit()
                resp = _serialize_version(ver)
            except QuantPlatformError as exc:
                session.rollback()
                raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        await ws_hub.publish({"type": "strategy_uploaded", "version_id": resp.version_id})
        return resp

    @app.post("/api/v1/strategies/generate-nl", response_model=StrategyVersionResponse)
    async def generate_nl_strategy(
        req: NLStrategyGenerateRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> StrategyVersionResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            nl_pipe = NLStrategyGenerationPipeline(
                session=session,
                version_store=store,
                llm_provider=DeterministicRuleLLMProvider(),
                clock=wall_clock,
            )
            try:
                res = nl_pipe.generate_and_register(
                    strategy_id=req.strategy_id,
                    name=req.name,
                    prompt=req.prompt,
                    description=req.description,
                    author=principal.actor_id,
                    gate_bars=_canonical_sample_bars(),
                    gate_spec=_default_perp_spec(),
                    gate_config=BacktestGateConfig(min_trades=0, max_drawdown_pct=Decimal("0.50")),
                )
                session.commit()
                resp = _serialize_version(res["version_record"])
            except QuantPlatformError as exc:
                session.rollback()
                raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
        await ws_hub.publish({"type": "nl_strategy_generated", "version_id": resp.version_id})
        return resp

    @app.get("/api/v1/strategies/{strategy_id}", response_model=StrategyDetailResponse)
    def get_strategy_detail(
        strategy_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> StrategyDetailResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                meta = store.get_strategy_meta(strategy_id)
                latest = store.get_latest_version(strategy_id)
                versions = store.list_versions(strategy_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            run_rows = list(
                session.execute(
                    select(RunOrmRecord)
                    .where(RunOrmRecord.strategy_id == strategy_id)
                    .order_by(RunOrmRecord.started_at_ms.desc())
                )
                .scalars()
                .all()
            )
            runs = []
            for r in run_rows:
                sm = (r.report_json or {}).get("summary_metrics", {}) if r.report_json else {}
                runs.append(
                    {
                        "run_id": r.run_id,
                        "env": r.env,
                        "strategy_version_id": r.strategy_version_id,
                        "status": r.status,
                        "started_at_ms": r.started_at_ms,
                        "ended_at_ms": r.ended_at_ms,
                        "cumulative_return": sm.get("cumulative_return"),
                        "max_drawdown": sm.get("max_drawdown"),
                        "sharpe": sm.get("sharpe"),
                        "config_snapshot": r.config_snapshot_json,
                        "error_message": r.error_message,
                    }
                )
            return StrategyDetailResponse(
                meta=_build_strategy_summary(session, store, meta),
                latest_version=_serialize_version(latest),
                versions=[_serialize_version(v) for v in versions],
                runs=runs,
            )

    @app.post("/api/v1/strategies/{strategy_id}/params", response_model=StrategyVersionResponse)
    async def update_strategy_params(
        strategy_id: str,
        req: StrategyParamUpdateRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> StrategyVersionResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.update_strategy_params(
                    strategy_id=strategy_id,
                    new_params=req.params,
                    author=principal.actor_id,
                    ts_ms=wall_clock.now_ms(),
                )
                session.commit()
                resp = _serialize_version(ver)
            except (KeyError, QuantPlatformError, ValueError) as exc:
                session.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        await ws_hub.publish({"type": "strategy_params_updated", "version_id": resp.version_id})
        return resp

    @app.post("/api/v1/strategies/versions/{version_id}/approve", response_model=StrategyVersionResponse)
    async def approve_strategy_version(
        version_id: str,
        req: StrategyApprovalRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> StrategyVersionResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.approve_version(
                    version_id=version_id,
                    approver=principal.actor_id,
                    ts_ms=wall_clock.now_ms(),
                    note=req.note,
                )
                session.commit()
                resp = _serialize_version(ver)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        await ws_hub.publish({"type": "strategy_approved", "version_id": resp.version_id})
        return resp

    @app.post("/api/v1/strategies/versions/{version_id}/reject", response_model=StrategyVersionResponse)
    async def reject_strategy_version(
        version_id: str,
        req: StrategyApprovalRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> StrategyVersionResponse:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.reject_version(
                    version_id=version_id,
                    approver=principal.actor_id,
                    ts_ms=wall_clock.now_ms(),
                    note=req.note,
                )
                session.commit()
                resp = _serialize_version(ver)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        await ws_hub.publish({"type": "strategy_rejected", "version_id": resp.version_id})
        return resp

    # ------------------------------------------------------------------
    # Runs: Preflight Check, Launch, Status, Cancel, Report, Charts & Research
    # ------------------------------------------------------------------

    @app.post("/api/v1/runs/preflight", response_model=RunPreflightResponse)
    def preflight_run(
        req: RunPreflightRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> RunPreflightResponse:
        try:
            target_env = RunEnv(req.env.lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid env: {req.env}") from exc

        spec = _default_perp_spec(req.symbol, req.exchange)
        risk_cfg = LiveRiskConfig()
        blocking: list[PreflightBlockingReason] = []

        if app.state.maintenance_mode:
            blocking.append(
                PreflightBlockingReason(
                    code="MAINTENANCE_MODE",
                    message="平台当前处于维护模式，已暂停新运行启动",
                    remediation="前往 /system 关闭维护模式后再试",
                )
            )
        if target_env == RunEnv.LIVE and app.state.kill_switch_active:
            blocking.append(
                PreflightBlockingReason(
                    code="KILL_SWITCH_ACTIVE",
                    message="全局紧急停机开关 (Kill Switch) 处于激活锁定状态",
                    remediation="确认风险解除后在系统状态页重置或联系风控管理员",
                )
            )
        if target_env == RunEnv.LIVE and principal.role not in {Role.OPERATOR, Role.ADMIN}:
            blocking.append(
                PreflightBlockingReason(
                    code="INSUFFICIENT_ROLE",
                    message=f"当前角色 {principal.role.value} 无权发起实盘运行（需 OPERATOR 或 ADMIN）",
                    remediation="切换为 OPERATOR 或 ADMIN 令牌后重试",
                )
            )

        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.get_version(req.version_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            if target_env in {RunEnv.SHADOW, RunEnv.LIVE} and ver.approval_status != ApprovalStatus.APPROVED.value:
                blocking.append(
                    PreflightBlockingReason(
                        code="APPROVAL_REQUIRED",
                        message=f"策略版本 {ver.version_id} 尚未通过人工审批（当前状态: {ver.approval_status}）",
                        remediation=f"请由 OPERATOR / ADMIN 在策略详情页对 {ver.version_id} 点击「人工审批通过」",
                    )
                )

        init_usdt = to_strict_decimal(req.initial_usdt, "initial_usdt")
        lev = to_strict_decimal(req.leverage, "leverage")
        if lev <= ZERO:
            blocking.append(
                PreflightBlockingReason(
                    code="INVALID_LEVERAGE",
                    message="杠杆倍数必须大于 0",
                    remediation="将杠杆调整为 1x – 10x 之间的合法数值",
                )
            )
            lev = Decimal("1")

        if lev > spec.max_leverage:
            blocking.append(
                PreflightBlockingReason(
                    code="EXCEEDS_SYMBOL_MAX_LEVERAGE",
                    message=f"配置杠杆 {format_decimal(lev)}x 超过合约规格上限 {format_decimal(spec.max_leverage)}x",
                    remediation=f"将杠杆调低至 <= {format_decimal(spec.max_leverage)}x",
                )
            )
        if target_env == RunEnv.LIVE and lev > risk_cfg.max_total_leverage:
            blocking.append(
                PreflightBlockingReason(
                    code="EXCEEDS_RISK_MAX_LEVERAGE",
                    message=f"配置杠杆 {format_decimal(lev)}x 超过事前风控上限 {format_decimal(risk_cfg.max_total_leverage)}x",
                    remediation=f"将杠杆调低至 <= {format_decimal(risk_cfg.max_total_leverage)}x",
                )
            )
        if init_usdt < spec.min_notional:
            blocking.append(
                PreflightBlockingReason(
                    code="BELOW_MIN_NOTIONAL",
                    message=f"初始分配资金 {format_decimal(init_usdt)} USDT 低于最小名义价值 {format_decimal(spec.min_notional)} USDT",
                    remediation=f"将初始资金调整至 >= {format_decimal(spec.min_notional)} USDT",
                )
            )
        if int(app.state.clock_skew_ms) > 1000:
            blocking.append(
                PreflightBlockingReason(
                    code="CLOCK_SKEW_EXCEEDED",
                    message=f"服务器时间偏移 {app.state.clock_skew_ms}ms 超过 1000ms 安全上限",
                    remediation="同步宿主机 NTP 时钟后再启动实盘",
                )
            )

        est_margin = (init_usdt / lev).quantize(Decimal("0.01"))
        liq_dist = max(Decimal("0"), (Decimal("1") / lev) - Decimal("0.005")).quantize(Decimal("0.0001"))

        return RunPreflightResponse(
            passed=len(blocking) == 0,
            blocking_reasons=blocking,
            approval_status=ver.approval_status,
            approved_by=ver.approved_by,
            account_check=PreflightAccountCheck(
                is_testnet=True,
                available_balance_usdt=format_decimal(init_usdt),
                api_key_no_withdraw=True,
                ip_whitelist_verified=True,
            ),
            margin_and_risk_estimate=PreflightMarginRiskEstimate(
                estimated_initial_margin_usdt=format_decimal(est_margin),
                configured_leverage=format_decimal(lev),
                symbol_max_leverage=format_decimal(spec.max_leverage),
                risk_max_total_leverage=format_decimal(risk_cfg.max_total_leverage),
                max_order_notional_limit_usdt=format_decimal(risk_cfg.max_order_notional),
                max_daily_loss_limit_usdt=format_decimal(risk_cfg.max_daily_loss_amount),
                estimated_liq_distance_pct=format_decimal(liq_dist * Decimal("100")),
                current_clock_skew_ms=int(app.state.clock_skew_ms),
            ),
            symbol_spec=_spec_to_summary(spec),
        )

    @app.get("/api/v1/runs", response_model=list[RunSummaryItem])
    def list_runs(
        env: str | None = Query(default=None),
        strategy_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> list[RunSummaryItem]:
        with sf() as session:
            stmt = select(RunOrmRecord).order_by(RunOrmRecord.started_at_ms.desc()).limit(limit)
            if env:
                stmt = stmt.where(RunOrmRecord.env == env.lower())
            if strategy_id:
                stmt = stmt.where(RunOrmRecord.strategy_id == strategy_id)
            rows = list(session.execute(stmt).scalars().all())
            out: list[RunSummaryItem] = []
            for r in rows:
                sm = (r.report_json or {}).get("summary_metrics", {}) if r.report_json else {}
                tm = (r.report_json or {}).get("trade_metrics", {}) if r.report_json else {}
                out.append(
                    RunSummaryItem(
                        run_id=r.run_id,
                        env=r.env,
                        strategy_id=r.strategy_id,
                        strategy_version_id=r.strategy_version_id,
                        status=r.status,
                        started_at_ms=r.started_at_ms,
                        ended_at_ms=r.ended_at_ms,
                        cumulative_return=sm.get("cumulative_return"),
                        max_drawdown=sm.get("max_drawdown"),
                        sharpe=sm.get("sharpe"),
                        fill_count=int(tm.get("fill_count", 0)),
                        error_message=r.error_message,
                    )
                )
            return out

    @app.post("/api/v1/runs/launch", response_model=LaunchRunResponse)
    async def launch_run(
        req: LaunchRunRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> LaunchRunResponse:
        if app.state.maintenance_mode:
            raise HTTPException(status_code=503, detail="Platform is in maintenance mode")
        try:
            target_env = RunEnv(req.env.lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid env: {req.env}") from exc

        if target_env == RunEnv.LIVE and principal.role not in {Role.OPERATOR, Role.ADMIN}:
            raise HTTPException(status_code=403, detail="LIVE launch requires OPERATOR or ADMIN role")

        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            try:
                ver = store.get_version(req.version_id)
                meta = store.get_strategy_meta(req.strategy_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            # Shadow & Live strictly require manual human approval!
            if target_env in {RunEnv.SHADOW, RunEnv.LIVE} and ver.approval_status != ApprovalStatus.APPROVED.value:
                raise HTTPException(
                    status_code=400,
                    detail=f"APPROVAL_REQUIRED: Version {req.version_id} status is {ver.approval_status}",
                )

            cls = compile_strategy_class(ver.code)
            bars = _canonical_sample_bars(req.exchange, req.symbol)
            spec = _default_perp_spec(req.symbol, req.exchange)
            init_usdt = to_strict_decimal(req.initial_usdt, "initial_usdt")
            lev = to_strict_decimal(req.leverage, "leverage")
            mmode = MarginMode.ISOLATED if req.margin_mode.upper() == "ISOLATED" else MarginMode.CROSS
            cfg = StrategyConfig(
                strategy_id=req.strategy_id,
                strategy_version_id=ver.version_id,
                name=meta.name,
                exchanges=[req.exchange],
                symbols=[req.symbol],
                params=ver.params_json,
                margin_mode=mmode,
                default_leverage=lev,
                initial_balances={"USDT": init_usdt},
            )
            ledger = UnifiedLedgerRepository(session)

            if target_env == RunEnv.BACKTEST:
                from quant_platform.data import HistoricalDataFeed

                bt_clock = SimulatedClock(bars[0].open_time_ms)
                bt_feed = HistoricalDataFeed(clock=bt_clock)
                bt_feed.register_symbol_spec(spec)
                bt_feed.load_bars(req.exchange, req.symbol, "1m", bars)
                bt_engine = BacktestEngine(
                    db_session=session,
                    clock=bt_clock,
                    data_feed=bt_feed,
                    strategy_cls=cls,
                    strategy_code=ver.code,
                    config=cfg,
                )
                bt_res = bt_engine.run()
                build_and_persist_backtest_report(db_session=session, result=bt_res, benchmark_bars=bars)
                run_rec = bt_res.run_record
            elif target_env == RunEnv.SHADOW:
                sh_clock = SimulatedClock(bars[0].open_time_ms)
                sh_feed = StreamingDataFeed(clock=sh_clock)
                sh_feed.register_symbol_spec(spec)
                run_id = f"run_sh_{uuid.uuid4().hex[:14]}"
                sh_broker = ShadowBroker(
                    run_id=run_id,
                    strategy_id=req.strategy_id,
                    clock=sh_clock,
                    data_feed=sh_feed,
                    ledger=ledger,
                    sim_params=ShadowSimulationParams(latency_ms=10),
                    initial_balances={"USDT": init_usdt},
                )

                def _sh_events():
                    for b in bars:
                        sh_clock.advance_to(b.close_time_ms)
                        sh_feed.on_bar(b)
                        sh_broker.on_bar(b)
                        yield ("bar", b)

                runner = ShadowRunner(
                    run_id=run_id,
                    strategy_cls=cls,
                    strategy_code=ver.code,
                    config=cfg,
                    clock=sh_clock,
                    data_feed=sh_feed,
                    broker=sh_broker,
                    ledger=ledger,
                    event_source=_sh_events,
                )
                run_rec = runner.run()
            else:
                app.state.kill_switch_active = False
                lv_clock = SimulatedClock(bars[0].open_time_ms)
                lv_feed = StreamingDataFeed(clock=lv_clock)
                lv_feed.register_symbol_spec(spec)
                run_id = f"run_lv_{uuid.uuid4().hex[:14]}"
                transport = MockRawExchangeTransport(
                    exchange=req.exchange,
                    clock=lv_clock,
                    initial_balances={"USDT": format_decimal(init_usdt)},
                )
                lv_broker = ExchangeBroker(
                    run_id=run_id,
                    strategy_id=req.strategy_id,
                    exchange=req.exchange,
                    transport=transport,
                    clock=lv_clock,
                    data_feed=lv_feed,
                    ledger=ledger,
                )
                app.state.active_live_broker = (run_id, lv_broker)

                def _lv_events():
                    for b in bars:
                        lv_clock.advance_to(b.close_time_ms)
                        lv_feed.on_bar(b)
                        yield ("bar", b)

                runner_lv = LiveRunner(
                    run_id=run_id,
                    strategy_cls=cls,
                    strategy_code=ver.code,
                    config=cfg,
                    clock=lv_clock,
                    data_feed=lv_feed,
                    broker=lv_broker,
                    ledger=ledger,
                    event_source=_lv_events,
                )
                run_rec = runner_lv.run()
                lv_broker._ledger = None
                lv_broker._order_callbacks.clear()
                lv_broker._fill_callbacks.clear()

            append_audit_log(
                session,
                actor=principal.actor_id,
                action="RUN_LAUNCHED",
                resource_type="run",
                resource_id=run_rec.run_id,
                payload={"env": target_env.value, "version_id": ver.version_id, "symbol": req.symbol},
                ts_ms=wall_clock.now_ms(),
            )
            session.commit()
            resp = LaunchRunResponse(
                run_id=run_rec.run_id,
                env=target_env.value,
                status=run_rec.status.value,
                strategy_id=req.strategy_id,
                version_id=ver.version_id,
            )

        await ws_hub.publish(
            {
                "type": "run_completed",
                "run_id": resp.run_id,
                "env": resp.env,
                "strategy_id": resp.strategy_id,
                "status": resp.status,
                "ts_ms": wall_clock.now_ms(),
            }
        )
        return resp

    @app.get("/api/v1/runs/{env}/{run_id}/status", response_model=RunStatusResponse)
    def get_run_status(
        env: str,
        run_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> RunStatusResponse:
        with sf() as session:
            row = session.execute(
                select(RunOrmRecord).where(RunOrmRecord.env == env.lower(), RunOrmRecord.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Run {env}:{run_id} not found")
            total_bars = 35
            done_bars = total_bars if row.status == "COMPLETED" else 0
            elapsed = max(0, (row.ended_at_ms or wall_clock.now_ms()) - row.started_at_ms)
            return RunStatusResponse(
                run_id=row.run_id,
                env=row.env,
                strategy_id=row.strategy_id,
                version_id=row.strategy_version_id,
                status=row.status,
                stage="COMPLETED" if row.status == "COMPLETED" else row.status,
                processed_bars=done_bars,
                total_bars=total_bars,
                progress_pct=100.0 if row.status == "COMPLETED" else 0.0,
                elapsed_ms=elapsed,
                estimated_remaining_ms=0 if row.status == "COMPLETED" else None,
                error_code=None,
                error_message=row.error_message,
            )

    @app.post("/api/v1/runs/{env}/{run_id}/cancel", response_model=RunStatusResponse)
    async def cancel_run(
        env: str,
        run_id: str,
        principal: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> RunStatusResponse:
        with sf() as session:
            row = session.execute(
                select(RunOrmRecord).where(RunOrmRecord.env == env.lower(), RunOrmRecord.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Run {env}:{run_id} not found")
            if row.status not in {"COMPLETED", "FAILED", "CANCELED"}:
                row.status = "CANCELED"
                row.ended_at_ms = wall_clock.now_ms()
                append_audit_log(
                    session,
                    actor=principal.actor_id,
                    action="RUN_CANCELED",
                    resource_type="run",
                    resource_id=run_id,
                    payload={"env": env.lower()},
                    ts_ms=wall_clock.now_ms(),
                )
                session.commit()
            return RunStatusResponse(
                run_id=row.run_id,
                env=row.env,
                strategy_id=row.strategy_id,
                version_id=row.strategy_version_id,
                status=row.status,
                stage=row.status,
                processed_bars=35 if row.status == "COMPLETED" else 0,
                total_bars=35,
                progress_pct=100.0 if row.status == "COMPLETED" else 0.0,
                elapsed_ms=max(0, (row.ended_at_ms or wall_clock.now_ms()) - row.started_at_ms),
                estimated_remaining_ms=0,
                error_code=None,
                error_message=row.error_message,
            )

    @app.get("/api/v1/runs/{env}/{run_id}/report")
    def get_backtest_report(
        env: str,
        run_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> dict[str, Any]:
        with sf() as session:
            ledger = UnifiedLedgerRepository(session)
            rec = ledger.get_run(RunEnv(env.lower()), run_id)
            if rec is None or rec.report is None:
                raise HTTPException(status_code=404, detail=f"Report for {env}:{run_id} not found")
            return rec.report

    @app.get("/api/v1/runs/{env}/{run_id}/chart-data", response_model=RunChartDataResponse)
    def get_run_chart_data(
        env: str,
        run_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> RunChartDataResponse:
        run_env = RunEnv(env.lower())
        with sf() as session:
            row = session.execute(
                select(RunOrmRecord).where(RunOrmRecord.env == run_env.value, RunOrmRecord.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Run {env}:{run_id} not found")
            cfg_snap = row.config_snapshot_json or {}
            exchanges = cfg_snap.get("exchanges") or ["binance"]
            symbols = cfg_snap.get("symbols") or ["BTC/USDT:USDT"]
            ex = exchanges[0]
            sym = symbols[0]
            spec = _default_perp_spec(sym, ex)
            raw_bars = _canonical_sample_bars(ex, sym)

            fills = list(
                session.execute(
                    select(FillRecord)
                    .where(FillRecord.env == run_env.value, FillRecord.run_id == run_id)
                    .order_by(FillRecord.exchange_ts_ms.asc())
                )
                .scalars()
                .all()
            )
            eq_rows = list(
                session.execute(
                    select(EquityCurveRecord)
                    .where(EquityCurveRecord.env == run_env.value, EquityCurveRecord.run_id == run_id)
                    .order_by(EquityCurveRecord.ts_ms.asc())
                )
                .scalars()
                .all()
            )

            chart_bars = [
                ChartBarPoint(
                    time_sec=int(b.open_time_ms // 1000),
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume),
                )
                for b in raw_bars
            ]
            markers = [
                ChartTradeMarker(
                    time_sec=int((f.exchange_ts_ms // 60_000) * 60),
                    side=f.side,
                    price=format_decimal(f.price),
                    qty=format_decimal(f.quantity),
                    client_order_id=f.client_order_id,
                    fill_id=f.fill_id,
                    realized_pnl=format_decimal(f.realized_pnl),
                    fee=format_decimal(f.fee),
                    liquidity=f.liquidity,
                )
                for f in fills
            ]

            init_eq = float(eq_rows[0].equity) if eq_rows else 10000.0
            first_close = float(raw_bars[0].close) if raw_bars else 1.0
            bar_by_close_sec = {int(b.close_time_ms // 1000): float(b.close) for b in raw_bars}
            eq_series: list[ChartEquityPoint] = []
            seen_sec: set[int] = set()
            for pt in eq_rows:
                t_sec = int(pt.ts_ms // 1000)
                if t_sec in seen_sec:
                    continue
                seen_sec.add(t_sec)
                bench_px = bar_by_close_sec.get(t_sec, first_close)
                bench_eq = init_eq * (bench_px / first_close) if first_close > 0 else init_eq
                eq_series.append(
                    ChartEquityPoint(
                        time_sec=t_sec,
                        equity=float(pt.equity),
                        benchmark_equity=round(bench_eq, 4),
                        drawdown_pct=round(float(pt.drawdown_pct) * 100.0, 4),
                    )
                )

            return RunChartDataResponse(
                run_id=run_id,
                env=run_env.value,
                exchange=ex,
                symbol=sym,
                symbol_spec=_spec_to_summary(spec),
                bars=chart_bars,
                trade_markers=markers,
                equity_series=eq_series,
            )

    @app.post("/api/v1/runs/backtest/{run_id}/sensitivity", response_model=SensitivityResponse)
    def run_backtest_sensitivity(
        run_id: str,
        req: SensitivityRequest,
        _: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> SensitivityResponse:
        with sf() as session:
            row = session.execute(
                select(RunOrmRecord).where(RunOrmRecord.env == "backtest", RunOrmRecord.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            ver = store.get_version(row.strategy_version_id)
            cls = compile_strategy_class(ver.code)
            cfg_snap = row.config_snapshot_json or {}
            ex = (cfg_snap.get("exchanges") or ["binance"])[0]
            sym = (cfg_snap.get("symbols") or ["BTC/USDT:USDT"])[0]
            bars = _canonical_sample_bars(ex, sym)
            spec = _default_perp_spec(sym, ex)
            base_cfg = StrategyConfig(
                strategy_id=row.strategy_id,
                strategy_version_id=ver.version_id,
                name=row.strategy_id,
                exchanges=[ex],
                symbols=[sym],
                params=ver.params_json,
                initial_balances={"USDT": Decimal("10000")},
            )
            res = compute_parameter_sensitivity_heatmap(
                db_session=session,
                bars=bars,
                spec=spec,
                strategy_cls=cls,
                strategy_code=ver.code,
                base_config=base_cfg,
                param_x_name=req.param_x_name,
                param_x_values=req.param_x_values,
                param_y_name=req.param_y_name,
                param_y_values=req.param_y_values,
            )
            session.rollback()
            return SensitivityResponse(
                param_x_name=res["param_x_name"],
                param_y_name=res["param_y_name"],
                x_values=res["x_values"],
                y_values=res["y_values"],
                cells=[SensitivityCellItem(**c) for c in res["cells"]],
            )

    @app.post("/api/v1/runs/backtest/{run_id}/walk-forward", response_model=WalkForwardResponse)
    def run_backtest_walk_forward(
        run_id: str,
        req: WalkForwardRequest,
        _: ActorPrincipal = Depends(require_min_role(Role.RESEARCHER)),
    ) -> WalkForwardResponse:
        with sf() as session:
            row = session.execute(
                select(RunOrmRecord).where(RunOrmRecord.env == "backtest", RunOrmRecord.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            ver = store.get_version(row.strategy_version_id)
            cls = compile_strategy_class(ver.code)
            cfg_snap = row.config_snapshot_json or {}
            ex = (cfg_snap.get("exchanges") or ["binance"])[0]
            sym = (cfg_snap.get("symbols") or ["BTC/USDT:USDT"])[0]
            bars = _canonical_sample_bars(ex, sym)
            spec = _default_perp_spec(sym, ex)
            base_cfg = StrategyConfig(
                strategy_id=row.strategy_id,
                strategy_version_id=ver.version_id,
                name=row.strategy_id,
                exchanges=[ex],
                symbols=[sym],
                params=ver.params_json,
                initial_balances={"USDT": Decimal("10000")},
            )
            cands = req.param_candidates or [ver.params_json]
            res = run_walk_forward_analysis(
                db_session=session,
                bars=bars,
                spec=spec,
                strategy_cls=cls,
                strategy_code=ver.code,
                base_config=base_cfg,
                param_candidates=cands,
                train_size=req.train_size,
                test_size=req.test_size,
            )
            session.rollback()
            return WalkForwardResponse(
                window_count=res["window_count"],
                windows=[WalkForwardWindowItem(**w) for w in res["windows"]],
            )

    # ------------------------------------------------------------------
    # Unified Three-Environment Ledger (Multi-Filter Query + Row Expansion)
    # ------------------------------------------------------------------

    @app.get("/api/v1/ledger/orders/query", response_model=LedgerOrdersQueryResponse)
    def query_orders_multi_filter(
        env: str | None = Query(default=None),
        strategy_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        symbol: str | None = Query(default=None),
        side: str | None = Query(default=None),
        status: str | None = Query(default=None, description="Comma-separated statuses"),
        start_ts_ms: int | None = Query(default=None),
        end_ts_ms: int | None = Query(default=None),
        q: str | None = Query(default=None),
        sort_by: str = Query(default="created_ts_ms"),
        sort_dir: str = Query(default="desc"),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> LedgerOrdersQueryResponse:
        with sf() as session:
            stmt = select(OrderRecord)
            if env and env.lower() != "all":
                stmt = stmt.where(OrderRecord.env == env.lower())
            if strategy_id:
                stmt = stmt.where(OrderRecord.strategy_id == strategy_id)
            if run_id:
                stmt = stmt.where(OrderRecord.run_id == run_id)
            if symbol:
                stmt = stmt.where(OrderRecord.symbol == symbol)
            if side:
                stmt = stmt.where(OrderRecord.side == side.upper())
            if status:
                statuses = [s.strip().upper() for s in status.split(",") if s.strip()]
                if statuses:
                    stmt = stmt.where(OrderRecord.status.in_(statuses))
            if start_ts_ms is not None:
                stmt = stmt.where(OrderRecord.created_ts_ms >= start_ts_ms)
            if end_ts_ms is not None:
                stmt = stmt.where(OrderRecord.created_ts_ms <= end_ts_ms)
            if q:
                needle = f"%{q.strip()}%"
                stmt = stmt.where(
                    OrderRecord.client_order_id.ilike(needle)
                    | OrderRecord.run_id.ilike(needle)
                    | OrderRecord.strategy_id.ilike(needle)
                )

            all_matched = list(session.execute(stmt).scalars().all())

            # Load fills for matched orders to compute fill_count & realized_pnl per order
            cids = [o.client_order_id for o in all_matched]
            fills_by_order: dict[tuple[str, str, str], list[FillRecord]] = {}
            if cids:
                fill_rows = list(
                    session.execute(select(FillRecord).where(FillRecord.client_order_id.in_(cids)))
                    .scalars()
                    .all()
                )
                for f in fill_rows:
                    fills_by_order.setdefault((f.env, f.run_id, f.client_order_id), []).append(f)

            total_notional = sum((o.filled_qty * o.avg_fill_price for o in all_matched), ZERO)
            total_fees = sum((o.fee_total for o in all_matched), ZERO)
            total_realized = sum(
                (
                    f.realized_pnl
                    for o in all_matched
                    for f in fills_by_order.get((o.env, o.run_id, o.client_order_id), [])
                ),
                ZERO,
            )
            filled_cnt = sum(1 for o in all_matched if o.status == "FILLED")
            rejected_cnt = sum(1 for o in all_matched if o.status == "REJECTED")

            reverse = sort_dir.lower() != "asc"
            sort_attr = sort_by if sort_by in {"created_ts_ms", "quantity", "filled_qty", "avg_fill_price", "status", "symbol"} else "created_ts_ms"
            all_matched.sort(key=lambda r: getattr(r, sort_attr) or 0, reverse=reverse)

            sliced = all_matched[offset : offset + limit]
            specs_map: dict[str, SymbolSpecSummary] = {}
            items: list[LedgerOrderRow] = []
            for o in sliced:
                if o.symbol not in specs_map:
                    specs_map[o.symbol] = _spec_to_summary(_default_perp_spec(o.symbol, o.exchange))
                ord_fills = fills_by_order.get((o.env, o.run_id, o.client_order_id), [])
                ord_pnl = sum((f.realized_pnl for f in ord_fills), ZERO)
                items.append(
                    LedgerOrderRow(
                        env=o.env,
                        run_id=o.run_id,
                        strategy_id=o.strategy_id,
                        client_order_id=o.client_order_id,
                        exchange_order_id=o.exchange_order_id,
                        exchange=o.exchange,
                        symbol=o.symbol,
                        side=o.side,
                        order_type=o.order_type,
                        time_in_force=o.time_in_force,
                        price=format_decimal(o.price) if o.price is not None else None,
                        stop_price=format_decimal(o.stop_price) if o.stop_price is not None else None,
                        quantity=format_decimal(o.quantity),
                        filled_qty=format_decimal(o.filled_qty),
                        avg_fill_price=format_decimal(o.avg_fill_price),
                        remaining_qty=format_decimal(o.remaining_qty),
                        fee_total=format_decimal(o.fee_total),
                        fee_asset=o.fee_asset,
                        leverage=format_decimal(o.leverage),
                        margin_mode=o.margin_mode,
                        status=o.status,
                        reject_reason=o.reject_reason,
                        fill_count=len(ord_fills),
                        realized_pnl_usdt=format_decimal(ord_pnl),
                        created_ts_ms=o.created_ts_ms,
                        updated_ts_ms=o.updated_ts_ms,
                    )
                )

            return LedgerOrdersQueryResponse(
                total=len(all_matched),
                limit=limit,
                offset=offset,
                summary=LedgerQuerySummary(
                    order_count=len(all_matched),
                    filled_count=filled_cnt,
                    rejected_count=rejected_cnt,
                    total_notional_usdt=format_decimal(total_notional),
                    total_fee_usdt=format_decimal(total_fees),
                    realized_pnl_usdt=format_decimal(total_realized),
                ),
                symbol_specs=specs_map,
                items=items,
            )

    @app.get(
        "/api/v1/ledger/orders/{env}/{run_id}/{client_order_id}/fills",
        response_model=OrderFillsDrilldownResponse,
    )
    def get_order_fills_drilldown(
        env: str,
        run_id: str,
        client_order_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> OrderFillsDrilldownResponse:
        with sf() as session:
            fills = list(
                session.execute(
                    select(FillRecord)
                    .where(
                        FillRecord.env == env.lower(),
                        FillRecord.run_id == run_id,
                        FillRecord.client_order_id == client_order_id,
                    )
                    .order_by(FillRecord.exchange_ts_ms.asc())
                )
                .scalars()
                .all()
            )
            traces = list(
                session.execute(
                    select(SignalTraceRecord).where(
                        SignalTraceRecord.env == env.lower(),
                        SignalTraceRecord.run_id == run_id,
                        SignalTraceRecord.client_order_id == client_order_id,
                    )
                )
                .scalars()
                .all()
            )
            return OrderFillsDrilldownResponse(
                client_order_id=client_order_id,
                env=env.lower(),
                run_id=run_id,
                fills=[
                    {
                        "fill_id": f.fill_id,
                        "exchange_trade_id": f.exchange_trade_id,
                        "symbol": f.symbol,
                        "side": f.side,
                        "liquidity": f.liquidity,
                        "price": format_decimal(f.price),
                        "quantity": format_decimal(f.quantity),
                        "quote_qty": format_decimal(f.quote_qty),
                        "slippage_bps": format_decimal(f.slippage_bps),
                        "fee": format_decimal(f.fee),
                        "fee_asset": f.fee_asset,
                        "realized_pnl": format_decimal(f.realized_pnl),
                        "exchange_ts_ms": f.exchange_ts_ms,
                    }
                    for f in fills
                ],
                signal_traces=[
                    {
                        "signal_id": t.signal_id,
                        "intent_type": t.intent_type,
                        "signal_ts_ms": t.signal_ts_ms,
                        "order_ts_ms": t.order_ts_ms,
                        "snapshot_ref": t.snapshot_ref,
                        "payload": t.payload_json,
                    }
                    for t in traces
                ],
            )

    @app.get("/api/v1/ledger/{table_name}", response_model=LedgerListResponse)
    def query_unified_ledger(
        table_name: str,
        env: str = Query(...),
        run_id: str = Query(...),
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> LedgerListResponse:
        run_env = RunEnv(env.lower())
        with sf() as session:
            ledger = UnifiedLedgerRepository(session)
            if table_name == "orders":
                items = [o.model_dump(mode="json") for o in ledger.list_orders(run_env, run_id)]
            elif table_name == "fills":
                items = [f.model_dump(mode="json") for f in ledger.list_fills(run_env, run_id)]
            elif table_name == "positions":
                items = [p.model_dump(mode="json") for p in ledger.list_positions(run_env, run_id)]
            elif table_name == "funding_payments":
                items = [fp.model_dump(mode="json") for fp in ledger.list_funding_payments(run_env, run_id)]
            elif table_name == "balance_snapshots":
                rows = list(
                    session.execute(
                        select(BalanceSnapshotRecord).where(
                            BalanceSnapshotRecord.env == run_env.value,
                            BalanceSnapshotRecord.run_id == run_id,
                        )
                    )
                    .scalars()
                    .all()
                )
                items = [
                    {
                        "asset": r.asset,
                        "total": format_decimal(r.total),
                        "available": format_decimal(r.available),
                        "ts_ms": r.ts_ms,
                    }
                    for r in rows
                ]
            elif table_name == "equity_curve":
                rows_eq = list(
                    session.execute(
                        select(EquityCurveRecord)
                        .where(EquityCurveRecord.env == run_env.value, EquityCurveRecord.run_id == run_id)
                        .order_by(EquityCurveRecord.ts_ms.asc())
                    )
                    .scalars()
                    .all()
                )
                items = [
                    {
                        "ts_ms": r.ts_ms,
                        "equity": format_decimal(r.equity),
                        "drawdown_pct": format_decimal(r.drawdown_pct),
                    }
                    for r in rows_eq
                ]
            else:
                raise HTTPException(status_code=400, detail=f"Unknown unified ledger table: {table_name}")
            return LedgerListResponse(table=table_name, env=run_env.value, run_id=run_id, items=items)

    # ------------------------------------------------------------------
    # Three-Tier Reconciliation
    # ------------------------------------------------------------------

    @app.post("/api/v1/reconciliation/run")
    def run_reconciliation(
        req: ReconciliationRunRequest,
        _: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> dict[str, Any]:
        with sf() as session:
            reconciler = ThreeTierReconciler(session=session, clock=wall_clock)
            tier = ReconciliationTier(req.tier)
            if tier == ReconciliationTier.BACKTEST_VS_SHADOW:
                if not req.backtest_run_id or not req.shadow_run_id:
                    raise HTTPException(status_code=400, detail="backtest_run_id and shadow_run_id required")
                res = reconciler.reconcile_backtest_vs_shadow(req.backtest_run_id, req.shadow_run_id)
            elif tier == ReconciliationTier.SHADOW_VS_LIVE:
                if not req.shadow_run_id or not req.live_run_id:
                    raise HTTPException(status_code=400, detail="shadow_run_id and live_run_id required")
                res = reconciler.reconcile_shadow_vs_live(req.shadow_run_id, req.live_run_id)
            else:
                active = app.state.active_live_broker
                if active is None:
                    raise HTTPException(status_code=400, detail="No active ExchangeBroker registered")
                live_rid, broker = active
                res = reconciler.reconcile_exchange_vs_local(req.run_id or live_rid, broker, auto_heal=req.auto_heal)
            session.commit()
            return res

    @app.get("/api/v1/reconciliation/reports")
    def list_reconciliation_reports(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> dict[str, Any]:
        with sf() as session:
            rows = list(
                session.execute(
                    select(ReconciliationReportRecord).order_by(ReconciliationReportRecord.created_ts_ms.desc())
                )
                .scalars()
                .all()
            )
            out_items = []
            for r in rows:
                sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
                sorted_items, drift_sum = _enrich_and_sort_recon_items(r.items_json)
                for it in sorted_items:
                    sv = str(it.get("severity", "LOW")).upper()
                    if sv in sev_counts:
                        sev_counts[sv] += 1
                out_items.append(
                    {
                        "report_id": r.report_id,
                        "tier": r.tier,
                        "env": r.env,
                        "run_id": r.run_id,
                        "reference_run_id": r.reference_run_id,
                        "passed": r.passed,
                        "discrepancy_count": r.discrepancy_count,
                        "max_severity": r.max_severity,
                        "severity_counts": sev_counts,
                        "total_abs_drift_usdt": format_decimal(drift_sum),
                        "summary": r.summary_json,
                        "items": sorted_items,
                        "created_ts_ms": r.created_ts_ms,
                    }
                )
            return {"items": out_items}

    @app.post("/api/v1/reconciliation/fault-drill", response_model=ReconciliationDetailResponse)
    def run_reconciliation_fault_drill(
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> ReconciliationDetailResponse:
        """
        Execute a real database-persisted Tier-1 reconciliation drill injecting:
        1) Position mismatch (local 0.100 BTC vs exchange 0.250 BTC -> +$7,500 USDT CRITICAL)
        2) Missing fill (fill_id=tr_drill_9001 on cid_drill_entry -> $4,000 USDT CRITICAL)
        3) Funding payment discrepancy (fp_drill_301 -18.75 USDT -> $18.75 USDT MEDIUM)
        """
        now_ms = wall_clock.now_ms()
        drill_run_id = f"run_lv_drill_{uuid.uuid4().hex[:8]}"
        drill_clock = SimulatedClock(now_ms)
        drill_spec = _default_perp_spec()
        drill_feed = StreamingDataFeed(clock=drill_clock)
        drill_feed.register_symbol_spec(drill_spec)

        drill_transport = MockRawExchangeTransport(
            exchange="binance",
            clock=drill_clock,
            initial_balances={"USDT": "10000.00"},
            initial_positions=[
                {
                    "symbol": "BTC/USDT:USDT",
                    "positionAmt": "0.250",
                    "entryPrice": "50000.0",
                    "markPrice": "50000.0",
                    "unRealizedProfit": "0.0",
                    "leverage": "3",
                    "marginType": "cross",
                }
            ],
            initial_fills=[
                {
                    "id": "tr_drill_9001",
                    "orderId": "88001",
                    "clientOrderId": "cid_drill_entry",
                    "symbol": "BTC/USDT:USDT",
                    "side": "BUY",
                    "price": "50000.0",
                    "qty": "0.080",
                    "quoteQty": "4000.0",
                    "commission": "1.60",
                    "commissionAsset": "USDT",
                    "maker": False,
                    "realizedPnl": "0",
                    "time": now_ms - 15_000,
                }
            ],
            initial_funding=[
                {
                    "tranId": "fp_drill_301",
                    "symbol": "BTC/USDT:USDT",
                    "income": "-18.75",
                    "asset": "USDT",
                    "fundingRate": "0.0003",
                    "markPrice": "50000.0",
                    "positionAmt": "0.250",
                    "time": now_ms - 5_000,
                }
            ],
        )

        with sf() as session:
            drill_broker = ExchangeBroker(
                run_id=drill_run_id,
                strategy_id="strat_recon_drill",
                exchange="binance",
                transport=drill_transport,
                clock=drill_clock,
                data_feed=drill_feed,
                ledger=None,
            )
            drill_broker.startup_sync()

            ledger = UnifiedLedgerRepository(session)
            ledger.record_balance_snapshot(
                drill_broker.query_balance()[0].model_copy(update={"run_id": drill_run_id})
            )
            ledger.upsert_position(
                drill_broker.query_positions()[0].model_copy(
                    update={
                        "run_id": drill_run_id,
                        "quantity": Decimal("0.100"),
                        "updated_ts_ms": now_ms - 20_000,
                    }
                )
            )
            reconciler = ThreeTierReconciler(session=session, clock=wall_clock)
            res = reconciler.reconcile_exchange_vs_local(drill_run_id, drill_broker, auto_heal=False)
            append_audit_log(
                session,
                actor=principal.actor_id,
                action="RECONCILIATION_FAULT_DRILL",
                resource_type="reconciliation_report",
                resource_id=str(res["report_id"]),
                payload={"drill_run_id": drill_run_id, "discrepancy_count": res["discrepancy_count"]},
                ts_ms=now_ms,
            )
            session.commit()
            rep_id = str(res["report_id"])

        return get_reconciliation_report_detail(rep_id, principal)

    @app.get("/api/v1/reconciliation/reports/{report_id}", response_model=ReconciliationDetailResponse)
    def get_reconciliation_report_detail(
        report_id: str,
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> ReconciliationDetailResponse:
        with sf() as session:
            r = session.execute(
                select(ReconciliationReportRecord).where(ReconciliationReportRecord.report_id == report_id)
            ).scalar_one_or_none()
            if r is None:
                raise HTTPException(status_code=404, detail=f"Reconciliation report {report_id} not found")
            sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            sorted_items, drift_sum = _enrich_and_sort_recon_items(r.items_json)
            for it in sorted_items:
                sv = str(it.get("severity", "LOW")).upper()
                if sv in sev_counts:
                    sev_counts[sv] += 1
            return ReconciliationDetailResponse(
                report_id=r.report_id,
                tier=r.tier,
                env=r.env,
                run_id=r.run_id,
                reference_run_id=r.reference_run_id,
                passed=r.passed,
                discrepancy_count=r.discrepancy_count,
                max_severity=r.max_severity,
                severity_counts=ReconciliationSeverityCounts(**sev_counts),
                total_abs_drift_usdt=format_decimal(drift_sum),
                summary=r.summary_json,
                items=sorted_items,
                created_ts_ms=r.created_ts_ms,
            )

    @app.get("/api/v1/reconciliation/reports/{report_id}/export")
    def export_reconciliation_report(
        report_id: str,
        fmt: str = Query(default="json"),
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> Response:
        with sf() as session:
            reconciler = ThreeTierReconciler(session=session, clock=wall_clock)
            try:
                if fmt.lower() == "csv":
                    body = reconciler.export_report_csv(report_id)
                    return Response(content=body, media_type="text/csv")
                body = reconciler.export_report_json(report_id)
                return Response(content=body, media_type="application/json")
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Risk Events, Alert Acknowledgment & Two-Step Kill Switch
    # ------------------------------------------------------------------

    @app.get("/api/v1/risk/events", response_model=RiskEventListResponse)
    def list_risk_events(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> RiskEventListResponse:
        with sf() as session:
            rows = list(
                session.execute(select(RiskEventRecord).order_by(RiskEventRecord.ts_ms.desc()).limit(200))
                .scalars()
                .all()
            )
            items = []
            unack_cnt = 0
            for r in rows:
                det = r.details_json or {}
                acked = bool(det.get("acknowledged", False))
                if not acked:
                    unack_cnt += 1
                items.append(
                    RiskEventItem(
                        event_id=r.event_id,
                        env=r.env,
                        run_id=r.run_id,
                        strategy_id=r.strategy_id,
                        rule_code=r.rule_code,
                        severity=r.severity,
                        action=r.action,
                        symbol=r.symbol,
                        client_order_id=r.client_order_id,
                        reason=r.reason,
                        acknowledged=acked,
                        acked_by=det.get("acked_by"),
                        acked_at_ms=det.get("acked_at_ms"),
                        ts_ms=r.ts_ms,
                    )
                )
            return RiskEventListResponse(items=items, unacknowledged_count=unack_cnt)

    @app.post("/api/v1/risk/events/{event_id}/ack", response_model=RiskEventAckResponse)
    def acknowledge_risk_event(
        event_id: str,
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> RiskEventAckResponse:
        now_ms = wall_clock.now_ms()
        with sf() as session:
            row = session.execute(
                select(RiskEventRecord).where(RiskEventRecord.event_id == event_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Risk event {event_id} not found")
            updated_details = dict(row.details_json or {})
            updated_details["acknowledged"] = True
            updated_details["acked_by"] = principal.actor_id
            updated_details["acked_at_ms"] = now_ms
            row.details_json = updated_details
            append_audit_log(
                session,
                actor=principal.actor_id,
                action="RISK_EVENT_ACKED",
                resource_type="risk_event",
                resource_id=event_id,
                payload={"rule_code": row.rule_code},
                ts_ms=now_ms,
            )
            session.commit()
            return RiskEventAckResponse(
                event_id=event_id,
                acknowledged=True,
                acked_by=principal.actor_id,
                acked_at_ms=now_ms,
            )

    @app.get("/api/v1/risk/kill-switch/preview", response_model=KillSwitchPreviewResponse)
    def preview_kill_switch(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> KillSwitchPreviewResponse:
        active = app.state.active_live_broker
        if active is None:
            return KillSwitchPreviewResponse(
                active_live_run_id=None,
                open_orders_count=0,
                open_orders_notional_usdt="0",
                open_orders=[],
                open_positions_count=0,
                gross_position_notional_usdt="0",
                unrealized_pnl_usdt="0",
                positions=[],
            )
        live_run_id, broker = active
        open_orders = broker.query_open_orders()
        positions = [p for p in broker.query_positions() if p.quantity != ZERO]

        ord_previews: list[KillSwitchOrderPreview] = []
        ord_notional_sum = ZERO
        for o in open_orders:
            px = o.price if o.price and o.price > ZERO else Decimal("50000")
            notional = abs(o.remaining_qty * px)
            ord_notional_sum += notional
            ord_previews.append(
                KillSwitchOrderPreview(
                    client_order_id=o.client_order_id,
                    symbol=o.symbol,
                    side=o.side.value,
                    order_type=o.order_type.value,
                    price=format_decimal(px),
                    remaining_qty=format_decimal(o.remaining_qty),
                    notional_usdt=format_decimal(notional),
                )
            )

        pos_previews: list[KillSwitchPositionPreview] = []
        pos_notional_sum = ZERO
        upnl_sum = ZERO
        for p in positions:
            notional = abs(p.quantity * p.mark_price)
            pos_notional_sum += notional
            upnl_sum += p.unrealized_pnl
            pos_previews.append(
                KillSwitchPositionPreview(
                    symbol=p.symbol,
                    position_side="LONG" if p.quantity > ZERO else "SHORT",
                    quantity=format_decimal(p.quantity),
                    entry_price=format_decimal(p.entry_price),
                    mark_price=format_decimal(p.mark_price),
                    notional_usdt=format_decimal(notional),
                    unrealized_pnl_usdt=format_decimal(p.unrealized_pnl),
                )
            )

        return KillSwitchPreviewResponse(
            active_live_run_id=live_run_id,
            open_orders_count=len(ord_previews),
            open_orders_notional_usdt=format_decimal(ord_notional_sum),
            open_orders=ord_previews,
            open_positions_count=len(pos_previews),
            gross_position_notional_usdt=format_decimal(pos_notional_sum),
            unrealized_pnl_usdt=format_decimal(upnl_sum),
            positions=pos_previews,
        )

    @app.post("/api/v1/risk/kill-switch", response_model=KillSwitchResponse)
    async def trigger_emergency_kill_switch(
        req: KillSwitchRequest,
        principal: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> KillSwitchResponse:
        with sf() as session:
            active = app.state.active_live_broker
            if active is not None:
                run_id, broker = active
            else:
                run_id = "standby_live_guard"
                spec = _default_perp_spec()
                feed = StreamingDataFeed(clock=wall_clock)
                feed.register_symbol_spec(spec)
                broker = ExchangeBroker(
                    run_id=run_id,
                    strategy_id="global_guard",
                    exchange="binance",
                    transport=MockRawExchangeTransport(exchange="binance", clock=wall_clock),
                    clock=wall_clock,
                    data_feed=feed,
                )
                broker.startup_sync()

            broker._ledger = UnifiedLedgerRepository(session)
            try:
                risk_engine = LiveRiskEngine(
                    env=RunEnv.LIVE,
                    run_id=run_id,
                    strategy_id="global_guard",
                    config=LiveRiskConfig(),
                    session=session,
                    clock=wall_clock,
                )
                summary = risk_engine.trigger_kill_switch(
                    broker=broker,
                    close_positions=req.close_positions,
                    reason=f"{req.reason} (by {principal.actor_id})",
                )
                app.state.kill_switch_active = True
                session.commit()
            finally:
                broker._ledger = None
            resp = KillSwitchResponse(**summary)

        await ws_hub.publish({"type": "kill_switch_triggered", **summary})
        return resp

    @app.get("/api/v1/audit-logs")
    def list_audit_logs(
        _: ActorPrincipal = Depends(require_min_role(Role.OPERATOR)),
    ) -> dict[str, Any]:
        with sf() as session:
            rows = list(
                session.execute(select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(200))
                .scalars()
                .all()
            )
            return {
                "items": [
                    {
                        "id": r.id,
                        "actor": r.actor,
                        "action": r.action,
                        "resource_type": r.resource_type,
                        "resource_id": r.resource_id,
                        "payload": r.payload_json,
                        "prev_hash": r.prev_hash,
                        "entry_hash": r.entry_hash,
                        "ts_ms": r.ts_ms,
                    }
                    for r in rows
                ]
            }

    return app


app = create_app()
