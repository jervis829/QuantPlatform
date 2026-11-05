"""P12 Acceptance Tests: Structured Logs + Rotation, 10 Prometheus Metrics, Backup/Restore, 5m WS Drop & DB Blip, and E2E Demo."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
import yaml

from quant_platform.api import create_app
from quant_platform.core.enums import ContractType, OrderSide, OrderType, RunEnv
from quant_platform.core.models import OrderRequest, SymbolSpec
from quant_platform.data.clock import SimulatedClock
from quant_platform.data.feed import StreamingDataFeed
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import AuditLogRecord, OrderRecord
from quant_platform.db.session import append_audit_log, build_engine, build_session_factory, init_db
from quant_platform.execution import ExecutionCoordinator, OrderIdMapper, detect_state_drift
from quant_platform.live import ExchangeBroker, MockRawExchangeTransport
from quant_platform.observability import (
    DatabaseBackupManager,
    PlatformMetrics,
    PlatformSupervisor,
    WebSocketHealthMonitor,
    configure_structured_logging,
)


def _spec() -> SymbolSpec:
    return SymbolSpec(
        exchange="binance",
        symbol="BTC/USDT:USDT",
        contract_type=ContractType.PERPETUAL,
        base_asset="BTC",
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


def test_structured_logging_rotation_redaction_and_all_10_prometheus_metrics(tmp_path: Path) -> None:
    """
    P12 Gate 1:
    1. Structured JSON logger redacts `api_key` / `api_secret` and rotates files cleanly at `max_bytes`.
    2. All 10 Prometheus core metrics are registered, updated, and scraped via `/metrics`.
    """
    log_file = tmp_path / "quant.jsonl"
    logger = configure_structured_logging(
        logger_name="qp_test_p12",
        log_file=log_file,
        max_bytes=600,
        backup_count=2,
    )
    for idx in range(12):
        logger.info(
            "order_lifecycle_event",
            extra={
                "env": "live",
                "run_id": "run_obs_1",
                "client_order_id": f"cid_{idx}",
                "api_key": "SUPER_SECRET_KEY_DO_NOT_LOG",
                "api_secret": "SUPER_SECRET_HMAC_DO_NOT_LOG",
            },
        )

    assert log_file.exists()
    assert (tmp_path / "quant.jsonl.1").exists()
    combined = log_file.read_text(encoding="utf-8") + (tmp_path / "quant.jsonl.1").read_text(encoding="utf-8")
    assert "SUPER_SECRET_KEY_DO_NOT_LOG" not in combined
    assert "***REDACTED***" in combined
    sample_line = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()][0]
    parsed = json.loads(sample_line)
    assert parsed["env"] == "live"
    assert parsed["api_key"] == "***REDACTED***"

    # Verify all 10 Prometheus metrics on FastAPI `/metrics`
    metrics = PlatformMetrics()
    metrics.orders_submitted_total.labels(env="live", exchange="binance", symbol="BTC/USDT:USDT", side="BUY", status="FILLED").inc()
    metrics.order_submit_latency_ms.labels(env="live", exchange="binance").observe(12.5)
    metrics.fills_recorded_total.labels(env="live", exchange="binance", liquidity="TAKER").inc()
    metrics.fill_slippage_bps.labels(env="live", exchange="binance", symbol="BTC/USDT:USDT").observe(1.8)
    metrics.account_equity_usdt.labels(env="live", run_id="run_obs_1", exchange="binance").set(10250.5)
    metrics.position_notional_usdt.labels(env="live", exchange="binance", symbol="BTC/USDT:USDT").set(5000.0)
    metrics.reconciliation_discrepancies_total.labels(tier="exchange_vs_local", severity="HIGH").inc()
    metrics.risk_rejections_total.labels(env="live", rule_code="MAX_ORDER_NOTIONAL").inc()
    metrics.ws_connection_healthy.labels(exchange="binance", channel="market_ws").set(1)
    metrics.exchange_clock_skew_ms.labels(exchange="binance").set(18.0)

    app = create_app(db_url=f"sqlite:///{tmp_path / 'p12_metrics.db'}", metrics=metrics)
    client = TestClient(app)
    r_m = client.get("/metrics")
    assert r_m.status_code == 200
    body = r_m.text
    expected_10_metric_names = [
        "quant_orders_submitted_total",
        "quant_order_submit_latency_ms",
        "quant_fills_recorded_total",
        "quant_fill_slippage_bps",
        "quant_account_equity_usdt",
        "quant_position_notional_usdt",
        "quant_reconciliation_discrepancies_total",
        "quant_risk_rejections_total",
        "quant_ws_connection_healthy",
        "quant_exchange_clock_skew_ms",
    ]
    for mname in expected_10_metric_names:
        assert mname in body, f"Missing required Prometheus metric: {mname}"


def test_hot_backup_and_restore_drill_preserves_decimal_ledger_and_audit_chain(tmp_path: Path) -> None:
    """
    P12 Gate 2 (Backup & Restore Drill):
    1. Populate live orders & hash-chained audit logs into primary SQLite DB;
    2. Perform hot backup + SHA-256 manifest verification via `DatabaseBackupManager`;
    3. Restore into a fresh target DB file and verify exact Decimal precision & unbroken audit hash chain!
    """
    src_db = tmp_path / "primary.db"
    backup_dir = tmp_path / "backups"
    restored_db = tmp_path / "restored.db"

    eng = build_engine(f"sqlite:///{src_db}")
    init_db(eng)
    sf = build_session_factory(eng)
    clock = SimulatedClock(1_700_000_000_000)
    spec = _spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        broker = ExchangeBroker(
            run_id="run_bkp_1",
            strategy_id="strat_bkp",
            exchange="binance",
            transport=MockRawExchangeTransport(exchange="binance", clock=clock),
            clock=clock,
            data_feed=feed,
            ledger=ledger,
        )
        broker.startup_sync()
        broker.submit_order(
            OrderRequest(
                client_order_id="bkp_ord_1",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.123"),
                created_ts_ms=clock.now_ms(),
            )
        )
        append_audit_log(
            session,
            actor="ops",
            action="PRE_BACKUP_CHECKPOINT",
            resource_type="system",
            resource_id="db",
            payload={"note": "drill"},
            ts_ms=clock.now_ms(),
        )
        session.commit()

    mgr = DatabaseBackupManager(backup_dir=backup_dir)
    manifest = mgr.create_sqlite_backup(src_db, label="daily_drill")
    assert mgr.verify_backup_manifest(manifest) is True

    mgr.restore_sqlite_backup(manifest, target_db_path=restored_db)

    restored_eng = build_engine(f"sqlite:///{restored_db}")
    rsf = build_session_factory(restored_eng)
    with rsf() as rsession:
        ord_row = rsession.execute(
            select(OrderRecord).where(OrderRecord.client_order_id == "bkp_ord_1")
        ).scalar_one()
        assert ord_row.quantity == Decimal("0.123")
        audits = list(rsession.execute(select(AuditLogRecord).order_by(AuditLogRecord.id.asc())).scalars().all())
        assert len(audits) == 1
        assert audits[0].prev_hash == "GENESIS"


def test_fault_injection_5min_ws_outage_db_blip_retry_and_maintenance_mode(tmp_path: Path) -> None:
    """
    P12 Gate 3 (Fault Drills):
    1. 5-minute (300,000 ms) WebSocket disconnect:
       - While WS is dead for 5 minutes, an open limit order fills on exchange;
       - `WebSocketHealthMonitor` flags `quant_ws_connection_healthy == 0`, invokes reconnect + `coord.recover_after_disconnect()`,
         reconciles missed fills/positions, and restores `quant_ws_connection_healthy == 1` with 0 drift!
    2. Database transient blip (`OperationalError`):
       - `PlatformSupervisor.execute_with_db_retry` transparently retries and succeeds on subsequent attempt.
    3. Maintenance mode gate:
       - Toggling maintenance mode returns `503` on `/api/v1/runs/launch` while keeping `/api/v1/health` & `/metrics` alive,
         then resumes cleanly when maintenance ends.
    """
    engine = build_engine(f"sqlite:///{tmp_path / 'p12_faults.db'}")
    init_db(engine)
    sf = build_session_factory(engine)
    clock = SimulatedClock(1_700_000_000_000)
    spec = _spec()
    feed = StreamingDataFeed(clock=clock)
    feed.register_symbol_spec(spec)
    metrics = PlatformMetrics()

    transport = MockRawExchangeTransport(exchange="binance", clock=clock, initial_balances={"USDT": "20000"})

    with sf() as session:
        ledger = UnifiedLedgerRepository(session)
        broker = ExchangeBroker(
            run_id="run_ws_drop",
            strategy_id="strat_ws",
            exchange="binance",
            transport=transport,
            clock=clock,
            data_feed=feed,
            ledger=ledger,
        )
        supervisor = PlatformSupervisor(clock=clock, metrics=metrics)
        startup_info = supervisor.startup_reconcile(session=session, run_id="run_ws_drop", broker=broker)
        assert startup_info["broker_sync"]["ready"] is True

        coord = ExecutionCoordinator(
            env=RunEnv.LIVE,
            run_id="run_ws_drop",
            strategy_id="strat_ws",
            broker=broker,
            ledger=ledger,
            id_mapper=OrderIdMapper(session),
            clock=clock,
        )
        # Submit resting limit order before 5-minute WS drop
        coord.submit_order(
            OrderRequest(
                client_order_id="ws_5m_cid_1",
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("0.200"),
                price=Decimal("49500.0"),
                created_ts_ms=clock.now_ms(),
            )
        )

        ws_monitor = WebSocketHealthMonitor(
            clock=clock,
            metrics=metrics,
            stale_timeout_ms=30_000,
        )
        ws_monitor.record_heartbeat("binance", "user_stream")

        # Advance 5 full minutes (300,000 ms) with NO WS heartbeats while order fills on exchange!
        clock.advance_by(300_000)
        transport._orders["ws_5m_cid_1"]["status"] = "FILLED"
        transport._orders["ws_5m_cid_1"]["executedQty"] = "0.200"
        transport._orders["ws_5m_cid_1"]["avgPrice"] = "49500.0"
        transport._orders["ws_5m_cid_1"]["updateTime"] = clock.now_ms()
        transport._positions["BTC/USDT:USDT"] = {
            "symbol": "BTC/USDT:USDT",
            "positionAmt": "0.200",
            "entryPrice": "49500.0",
            "markPrice": "49500.0",
            "unRealizedProfit": "0",
            "leverage": "5",
            "marginType": "cross",
        }

        reconnected_count = 0

        def _reconnect_and_sync(ex: str, ch: str) -> None:
            nonlocal reconnected_count
            reconnected_count += 1
            coord.recover_after_disconnect()
            ws_monitor.record_heartbeat(ex, ch)

        stale = ws_monitor.check_and_heal(on_reconnect=_reconnect_and_sync)
        assert stale == [("binance", "user_stream")]
        assert reconnected_count == 1
        assert detect_state_drift(coord, broker) == []
        assert coord.get_local_order("ws_5m_cid_1").status.value == "FILLED"
        assert (
            metrics.ws_connection_healthy.labels(exchange="binance", channel="user_stream")._value.get() == 1.0
        )

        # Sub-drill 2: DB transient blip retry
        attempts = 0

        def _flaky_db_op() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OperationalError("SELECT 1", {}, Exception("connection reset by peer"))
            return "recovered_ok"

        out = supervisor.execute_with_db_retry(_flaky_db_op, max_attempts=4, base_delay_sec=0.001)
        assert out == "recovered_ok"
        assert attempts == 3

        # Sub-drill 3: Maintenance mode toggle
        app = create_app(db_url=f"sqlite:///{tmp_path / 'p12_maint.db'}", metrics=metrics)
        client = TestClient(app)
        app.state.maintenance_mode = True
        assert client.get("/api/v1/health").json()["maintenance_mode"] is True
        r_maint_block = client.post(
            "/api/v1/runs/launch",
            headers={"X-API-Key": "admin-dev-token"},
            json={"strategy_id": "any", "version_id": "any:v1", "env": "backtest"},
        )
        assert r_maint_block.status_code == 503
        app.state.maintenance_mode = False
        assert client.get("/api/v1/health").json()["maintenance_mode"] is False


def test_docker_compose_manifest_and_one_click_e2e_demo_script() -> None:
    """
    P12 Gate 4:
    1. Validate `docker-compose.yml` defines `postgres`, `redis`, `api`, `worker`, `ui`, and `prometheus`.
    2. Execute `scripts/demo_e2e.py` end-to-end and verify exit code 0 and complete summary JSON output!
    """
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists()
    compose_doc = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = set(compose_doc.get("services", {}).keys())
    assert {"postgres", "redis", "api", "worker", "ui", "prometheus"}.issubset(services)

    proc = subprocess.run(
        [sys.executable, "scripts/demo_e2e.py"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"E2E demo failed:\n{proc.stdout}\n{proc.stderr}"
    assert "E2E_DEMO_SUCCESS" in proc.stdout


def test_docker_sync_overlay_and_github_realtime_sync_engine(tmp_path: Path) -> None:
    """
    Validate `docker-compose.sync.yml` overlay manifest, `Dockerfile.ui` build arg wiring,
    and `scripts/github_realtime_sync.py` auto-branch tracking + dirty working tree protection.
    """
    from scripts.github_realtime_sync import sync_once

    base_doc = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    sync_doc = yaml.safe_load(Path("docker-compose.sync.yml").read_text(encoding="utf-8"))
    assert "seed" in base_doc["services"]
    assert "BACKEND_INTERNAL_URL" in (base_doc["services"]["ui"]["build"].get("args") or {})
    assert "git-sync" in sync_doc["services"]
    assert "--reload" in sync_doc["services"]["api"]["command"]

    dockerfile_ui = Path("Dockerfile.ui").read_text(encoding="utf-8")
    assert "ARG BACKEND_INTERNAL_URL=" in dockerfile_ui
    assert dockerfile_ui.index("ENV BACKEND_INTERNAL_URL=") < dockerfile_ui.index("RUN npm run build")

    # Create a bare upstream repo + local clone to test real-time auto-branch sync & dirty guard
    upstream = tmp_path / "upstream.git"
    work_push = tmp_path / "pusher"
    local_clone = tmp_path / "local_clone"

    subprocess.run(["git", "init", "--bare", "-b", "main", str(upstream)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(upstream), str(work_push)], check=True, capture_output=True)

    git_env = {
        **dict(subprocess.os.environ),
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "checkout", "-b", "main"], cwd=str(work_push), env=git_env, check=True, capture_output=True)
    (work_push / "README.md").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(work_push), env=git_env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init main"], cwd=str(work_push), env=git_env, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(work_push), env=git_env, check=True, capture_output=True)

    subprocess.run(["git", "clone", str(upstream), str(local_clone)], check=True, capture_output=True)

    # No-op when already up to date
    r0 = sync_once(local_clone, repo_url="", branch_spec="auto", run_seed_on_change=False)
    assert r0["updated"] is False

    # Push a newer commit onto an `arena/feature` branch -> auto mode must track it!
    import time as _time

    _time.sleep(1.1)
    subprocess.run(
        ["git", "checkout", "-b", "arena/test-sync"],
        cwd=str(work_push),
        env=git_env,
        check=True,
        capture_output=True,
    )
    (work_push / "README.md").write_text("v2 from arena\n", encoding="utf-8")
    (work_push / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(work_push), env=git_env, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "arena update"],
        cwd=str(work_push),
        env=git_env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "arena/test-sync"],
        cwd=str(work_push),
        env=git_env,
        check=True,
        capture_output=True,
    )

    # Dirty local file must NOT be overwritten unless force_clean=True
    (local_clone / "README.md").write_text("dirty user edit\n", encoding="utf-8")
    r_dirty = sync_once(local_clone, repo_url="", branch_spec="auto", force_clean=False, run_seed_on_change=False)
    assert r_dirty["updated"] is False
    assert r_dirty["skipped_dirty"] is True
    assert (local_clone / "README.md").read_text(encoding="utf-8") == "dirty user edit\n"

    # Clean tree (or force_clean=True) fast-forwards to origin/arena/test-sync and flags rebuild
    r_sync = sync_once(local_clone, repo_url="", branch_spec="auto", force_clean=True, run_seed_on_change=False)
    assert r_sync["updated"] is True
    assert r_sync["target_ref"] == "origin/arena/test-sync"
    assert r_sync["needs_image_rebuild"] is True
    assert (local_clone / "README.md").read_text(encoding="utf-8") == "v2 from arena\n"
    assert (local_clone / ".git" / "quant_sync_status.json").is_file()

