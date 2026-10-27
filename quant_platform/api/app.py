"""FastAPI REST + WebSocket application serving Strategy, Unified Ledger, Reconciliation, Risk & Kill Switch (P11)."""

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
    HealthResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    LaunchRunRequest,
    LaunchRunResponse,
    LedgerListResponse,
    NLStrategyGenerateRequest,
    ReconciliationRunRequest,
    StrategyApprovalRequest,
    StrategyDetailResponse,
    StrategyParamUpdateRequest,
    StrategySummaryItem,
    StrategyUploadRequest,
    StrategyVersionResponse,
)
from quant_platform.api.ws_hub import WebSocketEventHub
from quant_platform.backtest import BacktestEngine, build_and_persist_backtest_report
from quant_platform.core.decimal_utils import format_decimal, to_strict_decimal
from quant_platform.core.enums import (
    ApprovalStatus,
    ContractType,
    MarginMode,
    ReconciliationTier,
    RunEnv,
)
from quant_platform.core.models import Bar, StrategyConfig, SymbolSpec
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import (
    AuditLogRecord,
    BalanceSnapshotRecord,
    EquityCurveRecord,
    ReconciliationReportRecord,
    RiskEventRecord,
    RunOrmRecord,
    StrategyVersionRecord,
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
    base = symbol.split("/")[0] if "/" in symbol else "BTC"
    return SymbolSpec(
        exchange=exchange,
        symbol=symbol,
        contract_type=ContractType.PERPETUAL,
        base_asset=base,
        quote_asset="USDT",
        margin_asset="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("1000"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("50"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        effective_from_ms=0,
    )


def _canonical_sample_bars(exchange: str = "binance", symbol: str = "BTC/USDT:USDT") -> list[Bar]:
    base_ts = 1_700_000_000_000
    prices = (
        [Decimal("50000") - Decimal(i * 120) for i in range(10)]
        + [Decimal("48800") + Decimal(i * 220) for i in range(15)]
        + [Decimal("52100") - Decimal(i * 240) for i in range(10)]
    )
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
                high=px + Decimal("40"),
                low=px - Decimal("40"),
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
    app.state.active_live_broker = None

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

    @app.get("/api/v1/strategies", response_model=list[StrategySummaryItem])
    def list_strategies(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> list[StrategySummaryItem]:
        with sf() as session:
            store = StrategyVersionStore(session=session, sandbox=sandbox)
            return [
                StrategySummaryItem(
                    strategy_id=m.strategy_id,
                    name=m.name,
                    description=m.description,
                    current_version=m.current_version,
                    source_type=m.source_type,
                    created_by=m.created_by,
                    updated_at_ms=m.updated_at_ms,
                )
                for m in store.list_strategies()
            ]

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
            runs = [
                {
                    "run_id": r.run_id,
                    "env": r.env,
                    "strategy_version_id": r.strategy_version_id,
                    "status": r.status,
                    "started_at_ms": r.started_at_ms,
                    "ended_at_ms": r.ended_at_ms,
                }
                for r in run_rows
            ]
            return StrategyDetailResponse(
                meta=StrategySummaryItem(
                    strategy_id=meta.strategy_id,
                    name=meta.name,
                    description=meta.description,
                    current_version=meta.current_version,
                    source_type=meta.source_type,
                    created_by=meta.created_by,
                    updated_at_ms=meta.updated_at_ms,
                ),
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
            cfg = StrategyConfig(
                strategy_id=req.strategy_id,
                strategy_version_id=ver.version_id,
                name=meta.name,
                exchanges=[req.exchange],
                symbols=[req.symbol],
                params=ver.params_json,
                margin_mode=MarginMode.CROSS,
                default_leverage=Decimal("3"),
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
                build_and_persist_backtest_report(db_session=session, result=bt_res)
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
                payload={"env": target_env.value, "version_id": ver.version_id},
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
                "status": resp.status,
            }
        )
        return resp

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
            return {
                "items": [
                    {
                        "report_id": r.report_id,
                        "tier": r.tier,
                        "env": r.env,
                        "run_id": r.run_id,
                        "reference_run_id": r.reference_run_id,
                        "passed": r.passed,
                        "discrepancy_count": r.discrepancy_count,
                        "max_severity": r.max_severity,
                        "summary": r.summary_json,
                        "items": r.items_json,
                        "created_ts_ms": r.created_ts_ms,
                    }
                    for r in rows
                ]
            }

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

    @app.get("/api/v1/risk/events")
    def list_risk_events(
        _: ActorPrincipal = Depends(require_min_role(Role.VIEWER)),
    ) -> dict[str, Any]:
        with sf() as session:
            rows = list(
                session.execute(select(RiskEventRecord).order_by(RiskEventRecord.ts_ms.desc()).limit(200))
                .scalars()
                .all()
            )
            return {
                "items": [
                    {
                        "event_id": r.event_id,
                        "env": r.env,
                        "run_id": r.run_id,
                        "strategy_id": r.strategy_id,
                        "rule_code": r.rule_code,
                        "severity": r.severity,
                        "action": r.action,
                        "symbol": r.symbol,
                        "reason": r.reason,
                        "ts_ms": r.ts_ms,
                    }
                    for r in rows
                ]
            }

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
                # Initialize clean standby Testnet broker if none active yet
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
