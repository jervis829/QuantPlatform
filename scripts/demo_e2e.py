#!/usr/bin/env python3
"""1-Click End-to-End Self-Hosted Verification & Demo Script for QuantPlatform (P0-P12)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from quant_platform.api import create_app  # noqa: E402
from quant_platform.observability import DatabaseBackupManager, PlatformMetrics  # noqa: E402


def run_demo() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="quant_demo_") as tmp:
        tmp_dir = Path(tmp)
        db_file = tmp_dir / "demo_quant.db"
        metrics = PlatformMetrics()
        app = create_app(db_url=f"sqlite:///{db_file}", metrics=metrics)
        client = TestClient(app)

        headers_viewer = {"X-API-Key": "viewer-dev-token"}
        headers_researcher = {"X-API-Key": "researcher-dev-token"}
        headers_operator = {"X-API-Key": "operator-dev-token"}
        headers_admin = {"X-API-Key": "admin-dev-token"}

        # 1. NL Strategy Generation (shared security/sandbox/backtest gate -> PENDING)
        r_nl = client.post(
            "/api/v1/strategies/generate-nl",
            headers=headers_researcher,
            json={
                "strategy_id": "demo_btc_ema_cross",
                "name": "Demo BTC EMA Crossover",
                "prompt": "当 BTC/USDT:USDT 4周期均线上穿 10周期均线时买入 0.05 BTC，下穿时平仓",
            },
        )
        assert r_nl.status_code == 200, r_nl.text
        v_rec = r_nl.json()
        assert v_rec["approval_status"] == "PENDING"

        # 2. Mandatory Manual Approval by Operator
        r_approve = client.post(
            f"/api/v1/strategies/versions/{v_rec['version_id']}/approve",
            headers=headers_operator,
            json={"note": "Approved in 1-click E2E verification"},
        )
        assert r_approve.status_code == 200, r_approve.text
        assert r_approve.json()["approval_status"] == "APPROVED"

        # 3. Tri-Environment Runs: Backtest -> Shadow -> Live (Testnet/Sim)
        runs: dict[str, str] = {}
        for env_name in ("backtest", "shadow", "live"):
            r_launch = client.post(
                "/api/v1/runs/launch",
                headers=headers_operator if env_name != "live" else headers_admin,
                json={
                    "strategy_id": "demo_btc_ema_cross",
                    "version_id": v_rec["version_id"],
                    "env": env_name,
                    "symbol": "BTC/USDT:USDT",
                    "exchange": "binance",
                    "initial_usdt": "15000",
                },
            )
            assert r_launch.status_code == 200, r_launch.text
            runs[env_name] = r_launch.json()["run_id"]

        # 4. Backtest Report + 3-Tier Reconciliation
        r_bt_rep = client.get(
            f"/api/v1/runs/backtest/{runs['backtest']}/report",
            headers=headers_viewer,
        )
        assert r_bt_rep.status_code == 200, r_bt_rep.text
        bt_report = r_bt_rep.json()

        r_tier3 = client.post(
            "/api/v1/reconciliation/run",
            headers=headers_operator,
            json={
                "tier": "backtest_vs_shadow",
                "backtest_run_id": runs["backtest"],
                "shadow_run_id": runs["shadow"],
            },
        )
        assert r_tier3.status_code == 200, r_tier3.text

        # 5. Sub-second Kill Switch
        r_ks = client.post(
            "/api/v1/risk/kill-switch",
            headers=headers_operator,
            json={"close_positions": True, "reason": "E2E demo completion"},
        )
        assert r_ks.status_code == 200, r_ks.text
        ks_json = r_ks.json()

        # 6. Prometheus `/metrics` & Hot Backup/Restore verification
        r_metrics = client.get("/metrics")
        assert r_metrics.status_code == 200
        bkp_mgr = DatabaseBackupManager(tmp_dir / "backups")
        manifest = bkp_mgr.create_sqlite_backup(db_file, label="e2e")
        restored = bkp_mgr.restore_sqlite_backup(manifest, target_db_path=tmp_dir / "restored.db")
        assert restored.exists()

        summary = {
            "status": "E2E_DEMO_SUCCESS",
            "strategy_version": v_rec["version_id"],
            "runs": runs,
            "backtest_cumulative_return": bt_report["summary_metrics"]["cumulative_return"],
            "tier3_report_id": r_tier3.json()["report_id"],
            "kill_switch_elapsed_ms": ks_json["elapsed_ms"],
            "backup_sha256": manifest.sha256_hex,
        }
        print(json.dumps(summary, indent=2))
        return summary


if __name__ == "__main__":
    run_demo()
