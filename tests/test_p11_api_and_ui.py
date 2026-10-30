"""P11 Acceptance Tests: FastAPI REST/WS, RBAC Authz + Audit Logs, OpenAPI TS generation, and Next.js TS verification."""

from __future__ import annotations

from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from quant_platform.api import create_app, generate_typescript_types_from_openapi


SAMPLE_UPLOAD_CODE = '''from decimal import Decimal
from pydantic import BaseModel, Field
from quant_platform.core.enums import OrderType, SignalAction
from quant_platform.core.interfaces import Strategy
from quant_platform.core.models import Bar, Signal


class SimpleBreakoutParams(BaseModel):
    lookback: int = Field(default=3, ge=2, le=50)
    qty: Decimal = Field(default=Decimal("0.05"))


class SimpleBreakoutStrategy(Strategy):
    ParamModel = SimpleBreakoutParams

    def on_start(self):
        self._highs = []
        return None

    def on_bar(self, bar: Bar):
        if len(self._highs) >= self.params.lookback:
            prev_max = max(self._highs[-self.params.lookback:])
            pos = self.ctx.get_position(bar.exchange, bar.symbol)
            cur = pos.quantity if pos is not None else Decimal("0")
            if bar.close > prev_max and cur == Decimal("0"):
                self._highs.append(bar.high)
                return [
                    Signal(
                        strategy_id=self.ctx.config.strategy_id,
                        exchange=bar.exchange,
                        symbol=bar.symbol,
                        action=SignalAction.BUY,
                        order_type=OrderType.MARKET,
                        quantity=self.params.qty,
                        reason="breakout_up",
                        ts_ms=self.ctx.now_ms,
                    )
                ]
        self._highs.append(bar.high)
        return None
'''


def test_rbac_authz_and_end_to_end_rest_ws_workflow(tmp_path: Path) -> None:
    """
    P11 Gates 1 & 2:
    - Unauthenticated -> 401; VIEWER mutating -> 403; RESEARCHER approving/live -> 403;
    - Strategy upload & NL generation -> detail w/ Pydantic param_schema & diff -> param update v2;
    - 1-click Backtest / Shadow / Live launch (enforcing manual approval on Shadow/Live);
    - Unified 3-env ledger queries (`orders`, `fills`, `positions`), Backtest report, 3-tier reconciliation + CSV export;
    - Real-time WebSocket stream (`/api/v1/ws/stream`) + <1s Kill Switch + hash-chained audit logs!
    """
    app = create_app(db_url=f"sqlite:///{tmp_path / 'p11_api.db'}")
    client = TestClient(app)

    viewer_hdr = {"X-API-Key": "viewer-dev-token"}
    researcher_hdr = {"X-API-Key": "researcher-dev-token"}
    admin_hdr = {"X-API-Key": "admin-dev-token"}

    # 1. Health & Auth checks
    r_health = client.get("/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    assert client.get("/api/v1/strategies").status_code == 401
    assert client.get("/api/v1/strategies", headers=viewer_hdr).status_code == 200

    # VIEWER cannot upload
    r_forbidden = client.post(
        "/api/v1/strategies/upload",
        headers=viewer_hdr,
        json={"strategy_id": "s1", "name": "S1", "code": SAMPLE_UPLOAD_CODE},
    )
    assert r_forbidden.status_code == 403

    # 2. RESEARCHER uploads Python strategy & generates NL strategy
    r_up = client.post(
        "/api/v1/strategies/upload",
        headers=researcher_hdr,
        json={"strategy_id": "strat_breakout", "name": "Breakout v1", "code": SAMPLE_UPLOAD_CODE},
    )
    assert r_up.status_code == 200, r_up.text
    ver_id_v1 = r_up.json()["version_id"]
    assert r_up.json()["approval_status"] == "PENDING"

    # RESEARCHER updates parameters -> creates v2 with unified diff!
    r_param = client.post(
        "/api/v1/strategies/strat_breakout/params",
        headers=researcher_hdr,
        json={"params": {"lookback": 5, "qty": "0.08"}},
    )
    assert r_param.status_code == 200
    ver_id_v2 = r_param.json()["version_id"]
    assert ver_id_v2 == "strat_breakout:v2"
    assert "+  \"lookback\": 5" in r_param.json()["diff_text"]

    # Fetch strategy detail (metadata, param_schema for dynamic form, version history, diffs)
    r_detail = client.get("/api/v1/strategies/strat_breakout", headers=viewer_hdr)
    assert r_detail.status_code == 200
    detail = r_detail.json()
    assert "lookback" in detail["latest_version"]["param_schema"]["properties"]
    assert len(detail["versions"]) == 2

    # RESEARCHER generates NL strategy
    r_nl = client.post(
        "/api/v1/strategies/generate-nl",
        headers=researcher_hdr,
        json={
            "strategy_id": "strat_nl_api",
            "name": "NL API Crossover",
            "prompt": "当 BTC/USDT:USDT 4周期均线上穿 10周期均线时买入 0.05 BTC，下穿时平仓",
        },
    )
    assert r_nl.status_code == 200, r_nl.text
    assert r_nl.json()["approval_status"] == "PENDING"

    # RESEARCHER cannot approve version (403) and cannot launch unapproved Shadow/Live (400/403)
    assert (
        client.post(
            f"/api/v1/strategies/versions/{ver_id_v1}/approve",
            headers=researcher_hdr,
            json={"note": "self-approve attempt"},
        ).status_code
        == 403
    )
    r_unapproved_sh = client.post(
        "/api/v1/runs/launch",
        headers=researcher_hdr,
        json={"strategy_id": "strat_breakout", "version_id": ver_id_v1, "env": "shadow"},
    )
    assert r_unapproved_sh.status_code == 400
    assert "APPROVAL_REQUIRED" in r_unapproved_sh.text

    # 3. Connect WebSocket stream & ADMIN approves version + launches Backtest, Shadow, and Live!
    with client.websocket_connect("/api/v1/ws/stream") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"

        r_app = client.post(
            f"/api/v1/strategies/versions/{ver_id_v1}/approve",
            headers=admin_hdr,
            json={"note": "Approved for Shadow & Testnet Live"},
        )
        assert r_app.status_code == 200
        assert r_app.json()["approval_status"] == "APPROVED"

        r_bt = client.post(
            "/api/v1/runs/launch",
            headers=researcher_hdr,
            json={"strategy_id": "strat_breakout", "version_id": ver_id_v1, "env": "backtest"},
        )
        assert r_bt.status_code == 200, r_bt.text
        bt_run_id = r_bt.json()["run_id"]

        ws_msg = ws.receive_json()
        assert ws_msg["type"] in {"strategy_approved", "run_completed"}

        r_sh = client.post(
            "/api/v1/runs/launch",
            headers=researcher_hdr,
            json={"strategy_id": "strat_breakout", "version_id": ver_id_v1, "env": "shadow"},
        )
        assert r_sh.status_code == 200, r_sh.text
        sh_run_id = r_sh.json()["run_id"]

        r_lv = client.post(
            "/api/v1/runs/launch",
            headers=admin_hdr,
            json={"strategy_id": "strat_breakout", "version_id": ver_id_v1, "env": "live"},
        )
        assert r_lv.status_code == 200, r_lv.text
        lv_run_id = r_lv.json()["run_id"]

    # 4. Fetch Backtest Report & Unified 3-Env Ledger Tables
    r_rep = client.get(f"/api/v1/runs/backtest/{bt_run_id}/report", headers=viewer_hdr)
    assert r_rep.status_code == 200
    assert "summary_metrics" in r_rep.json()

    for env_name, rid in [("backtest", bt_run_id), ("shadow", sh_run_id), ("live", lv_run_id)]:
        r_orders = client.get(
            f"/api/v1/ledger/orders?env={env_name}&run_id={rid}",
            headers=viewer_hdr,
        )
        assert r_orders.status_code == 200
        assert isinstance(r_orders.json()["items"], list)

    # 5. Run 3-Tier Reconciliation & Export CSV
    r_rec = client.post(
        "/api/v1/reconciliation/run",
        headers=admin_hdr,
        json={
            "tier": "backtest_vs_shadow",
            "backtest_run_id": bt_run_id,
            "shadow_run_id": sh_run_id,
        },
    )
    assert r_rec.status_code == 200
    rep_id = r_rec.json()["report_id"]

    r_csv = client.get(f"/api/v1/reconciliation/reports/{rep_id}/export?fmt=csv", headers=viewer_hdr)
    assert r_csv.status_code == 200
    assert "type,severity,symbol" in r_csv.text

    # 6. Trigger Emergency Kill Switch (<1s) & Verify Audit Trail
    r_ks = client.post(
        "/api/v1/risk/kill-switch",
        headers=admin_hdr,
        json={"close_positions": True, "reason": "ui_emergency_button"},
    )
    assert r_ks.status_code == 200
    assert r_ks.json()["kill_switch_active"] is True
    assert r_ks.json()["elapsed_ms"] < 1000.0

    r_audits = client.get("/api/v1/audit-logs", headers=admin_hdr)
    assert r_audits.status_code == 200
    actions = {a["action"] for a in r_audits.json()["items"]}
    assert "STRATEGY_VERSION_CREATED" in actions
    assert "STRATEGY_VERSION_APPROVED" in actions
    assert "KILL_SWITCH_TRIGGERED" in actions


def test_openapi_typescript_codegen_and_nextjs_strict_typecheck() -> None:
    """
    P11 Gate 3:
    1. Generate OpenAPI TypeScript interfaces (`frontend/src/types/openapi.generated.ts`) and verify sync.
    2. Assert zero hardcoded `localhost` / `127.0.0.1` URLs in browser code (`frontend/src/`).
    3. Run `npx tsc --noEmit` in `frontend/` to prove strict TypeScript type safety across all UI pages!
    """
    app = create_app(db_url="sqlite:///:memory:")
    generated_ts = generate_typescript_types_from_openapi(app.openapi())
    ts_path = Path("frontend/src/types/openapi.generated.ts")
    assert ts_path.exists(), "frontend/src/types/openapi.generated.ts must exist"
    assert ts_path.read_text(encoding="utf-8") == generated_ts

    # Browser code must NEVER call localhost / 127.0.0.1 directly (Arena preview rule!)
    for src_file in Path("frontend/src").rglob("*"):
        if src_file.is_file() and src_file.suffix in {".ts", ".tsx"}:
            txt = src_file.read_text(encoding="utf-8")
            assert "localhost" not in txt and "127.0.0.1" not in txt, (
                f"Browser file {src_file} contains forbidden localhost/127.0.0.1 reference"
            )

    proc = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd="frontend",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"TypeScript check failed:\n{proc.stdout}\n{proc.stderr}"
