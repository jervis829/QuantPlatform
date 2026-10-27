"""Verification tests for UI/UX Overhaul Control-Plane API Endpoints (Stage 1 Section 5)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quant_platform.api import create_app


def test_ui_control_plane_endpoints_end_to_end(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'ui_cp.db'}")
    client = TestClient(app)
    admin_hdr = {"X-API-Key": "admin-dev-token"}

    # 1. System overview, diagnostics, and versioned SymbolSpecs
    ov = client.get("/api/v1/system/overview", headers=admin_hdr)
    assert ov.status_code == 200
    ov_json = ov.json()
    assert "running_counts" in ov_json
    assert "exchange_connections" in ov_json
    assert "reconciliation_summary" in ov_json

    diag = client.get("/api/v1/system/diagnostics", headers=admin_hdr)
    assert diag.status_code == 200
    assert len(diag.json()["rate_limits"]) >= 1

    specs = client.get("/api/v1/specs", headers=admin_hdr)
    assert specs.status_code == 200
    assert any(s["symbol"] == "BTC/USDT:USDT" for s in specs.json())

    # 2. Generate NL Strategy (unapproved initially) & test Preflight blocking vs passing
    gen = client.post(
        "/api/v1/strategies/generate-nl",
        headers=admin_hdr,
        json={
            "strategy_id": "strat_cp_test",
            "name": "Control Plane SMA Test",
            "prompt": "当 BTC/USDT:USDT 4周期均线上穿 10周期均线时买入 0.05 BTC，下穿时平仓",
        },
    )
    assert gen.status_code == 200
    ver_id = gen.json()["version_id"]

    # Preflight LIVE while PENDING must report APPROVAL_REQUIRED blocking reason
    pf_blocked = client.post(
        "/api/v1/runs/preflight",
        headers=admin_hdr,
        json={
            "strategy_id": "strat_cp_test",
            "version_id": ver_id,
            "env": "live",
            "symbol": "BTC/USDT:USDT",
            "exchange": "binance",
            "initial_usdt": "10000",
        },
    )
    assert pf_blocked.status_code == 200
    pf_b_json = pf_blocked.json()
    assert pf_b_json["passed"] is False
    assert any(r["code"] == "APPROVAL_REQUIRED" for r in pf_b_json["blocking_reasons"])

    # Approve version -> Preflight LIVE now passes and returns est margin & leverage
    appr = client.post(
        f"/api/v1/strategies/versions/{ver_id}/approve",
        headers=admin_hdr,
        json={"note": "Approved for UI control-plane test"},
    )
    assert appr.status_code == 200

    pf_ok = client.post(
        "/api/v1/runs/preflight",
        headers=admin_hdr,
        json={
            "strategy_id": "strat_cp_test",
            "version_id": ver_id,
            "env": "live",
            "symbol": "BTC/USDT:USDT",
            "exchange": "binance",
            "initial_usdt": "10000",
        },
    )
    assert pf_ok.status_code == 200
    assert pf_ok.json()["passed"] is True
    assert "estimated_initial_margin_usdt" in pf_ok.json()["margin_and_risk_estimate"]
    assert "max_order_notional_limit_usdt" in pf_ok.json()["margin_and_risk_estimate"]

    # 3. Launch Backtest & verify chart-data, sensitivity, walk-forward, and run status
    bt = client.post(
        "/api/v1/runs/launch",
        headers=admin_hdr,
        json={
            "strategy_id": "strat_cp_test",
            "version_id": ver_id,
            "env": "backtest",
            "symbol": "BTC/USDT:USDT",
        },
    )
    assert bt.status_code == 200
    bt_run_id = bt.json()["run_id"]

    runs_list = client.get("/api/v1/runs?env=backtest", headers=admin_hdr)
    assert runs_list.status_code == 200
    assert any(r["run_id"] == bt_run_id for r in runs_list.json())

    st_res = client.get(f"/api/v1/runs/backtest/{bt_run_id}/status", headers=admin_hdr)
    assert st_res.status_code == 200
    assert st_res.json()["progress_pct"] == 100.0

    chart_res = client.get(f"/api/v1/runs/backtest/{bt_run_id}/chart-data", headers=admin_hdr)
    assert chart_res.status_code == 200
    c_json = chart_res.json()
    assert len(c_json["bars"]) > 10
    assert len(c_json["equity_series"]) > 0
    assert c_json["symbol_spec"]["symbol"] == "BTC/USDT:USDT"

    param_keys = list(gen.json()["params"].keys())
    px = param_keys[0]
    py = param_keys[1] if len(param_keys) > 1 else param_keys[0]
    vx = gen.json()["params"][px]
    vy = gen.json()["params"][py]

    sens_res = client.post(
        f"/api/v1/runs/backtest/{bt_run_id}/sensitivity",
        headers=admin_hdr,
        json={
            "param_x_name": px,
            "param_x_values": [vx],
            "param_y_name": py,
            "param_y_values": [vy],
        },
    )
    assert sens_res.status_code == 200
    assert len(sens_res.json()["cells"]) == 1

    wf_res = client.post(
        f"/api/v1/runs/backtest/{bt_run_id}/walk-forward",
        headers=admin_hdr,
        json={
            "train_size": 15,
            "test_size": 8,
            "param_candidates": [gen.json()["params"]],
        },
    )
    assert wf_res.status_code == 200
    assert wf_res.json()["window_count"] >= 1

    # 4. Multi-filter Ledger query + Order -> Fills drilldown + Global Search
    ord_q = client.get(
        f"/api/v1/ledger/orders/query?env=backtest&run_id={bt_run_id}",
        headers=admin_hdr,
    )
    assert ord_q.status_code == 200
    orders = ord_q.json()["items"]
    if orders:
        cid = orders[0]["client_order_id"]
        drill = client.get(
            f"/api/v1/ledger/orders/backtest/{bt_run_id}/{cid}/fills",
            headers=admin_hdr,
        )
        assert drill.status_code == 200
        assert drill.json()["client_order_id"] == cid

    srch = client.get("/api/v1/search?q=strat_cp_test", headers=admin_hdr)
    assert srch.status_code == 200
    assert any(h["id"] == "strat_cp_test" for h in srch.json()["hits"])

    # 5. Launch Live run, test Kill Switch preview, trigger & Risk Event ack + Reconciliation detail
    lv = client.post(
        "/api/v1/runs/launch",
        headers=admin_hdr,
        json={
            "strategy_id": "strat_cp_test",
            "version_id": ver_id,
            "env": "live",
            "symbol": "BTC/USDT:USDT",
        },
    )
    assert lv.status_code == 200
    lv_run_id = lv.json()["run_id"]

    ks_prev = client.get("/api/v1/risk/kill-switch/preview", headers=admin_hdr)
    assert ks_prev.status_code == 200
    assert "open_orders_count" in ks_prev.json()

    ks_exec = client.post(
        "/api/v1/risk/kill-switch",
        headers=admin_hdr,
        json={"close_positions": True, "reason": "ui_cp_test_kill"},
    )
    assert ks_exec.status_code == 200

    ev_list = client.get("/api/v1/risk/events", headers=admin_hdr).json()["items"]
    assert len(ev_list) >= 1
    first_ev_id = ev_list[0]["event_id"]
    ack_res = client.post(f"/api/v1/risk/events/{first_ev_id}/ack", headers=admin_hdr)
    assert ack_res.status_code == 200
    assert ack_res.json()["acknowledged"] is True

    recon = client.post(
        "/api/v1/reconciliation/run",
        headers=admin_hdr,
        json={"tier": "exchange_vs_local", "run_id": lv_run_id},
    )
    assert recon.status_code == 200
    rep_id = recon.json()["report_id"]
    recon_det = client.get(f"/api/v1/reconciliation/reports/{rep_id}", headers=admin_hdr)
    assert recon_det.status_code == 200
    assert recon_det.json()["report_id"] == rep_id

    # 6. Batch 4 Discrepancy Fault Drill Verification (Position + Missing Fill + Funding)
    drill = client.post("/api/v1/reconciliation/fault-drill", headers=admin_hdr)
    assert drill.status_code == 200
    d_json = drill.json()
    assert d_json["passed"] is False
    assert d_json["discrepancy_count"] == 3
    assert d_json["total_abs_drift_usdt"] == "11518.75"
    drill_types = [it["type"] for it in d_json["items"]]
    assert "POSITION_QTY_MISMATCH" in drill_types
    assert "MISSING_LOCAL_FILL" in drill_types
    assert "MISSING_LOCAL_FUNDING" in drill_types
    # Verify sorted by severity (CRITICAL items precede MEDIUM item)
    assert d_json["items"][0]["severity"] == "CRITICAL"
    assert d_json["items"][-1]["severity"] == "MEDIUM"
    missing_fill_item = next(it for it in d_json["items"] if it["type"] == "MISSING_LOCAL_FILL")
    assert missing_fill_item["order_id"] == "cid_drill_entry"
    assert missing_fill_item["fill_id"] == "exfill_tr_drill_9001"
    assert missing_fill_item["ts_ms"] > 0
