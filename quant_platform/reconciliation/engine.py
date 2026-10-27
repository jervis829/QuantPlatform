"""Three-Tier Reconciliation Engine: Exchange vs Local, Shadow vs Live, Backtest vs Shadow (P9 / Red Line 10)."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from typing import Any, Callable
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.decimal_utils import BPS_DIVISOR, ZERO, format_decimal
from quant_platform.core.enums import (
    DiscrepancySeverity,
    OrderStatus,
    ReconciliationTier,
    RunEnv,
)
from quant_platform.core.interfaces import BrokerAdapter, Clock, Reconciler
from quant_platform.core.models import Fill
from quant_platform.db.ledger import UnifiedLedgerRepository
from quant_platform.db.schema import (
    BalanceSnapshotRecord,
    FillRecord,
    FundingPaymentRecord,
    OrderRecord,
    PositionRecord,
    ReconciliationReportRecord,
    SignalTraceRecord,
)
from quant_platform.execution.state_machine import TERMINAL_ORDER_STATUSES

_SEVERITY_RANK: dict[DiscrepancySeverity, int] = {
    DiscrepancySeverity.NONE: 0,
    DiscrepancySeverity.LOW: 1,
    DiscrepancySeverity.MEDIUM: 2,
    DiscrepancySeverity.HIGH: 3,
    DiscrepancySeverity.CRITICAL: 4,
}


def _utc_date_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


class ThreeTierReconciler(Reconciler):
    """
    Implements all 3 reconciliation tiers, DB persistence, alert callbacks, drill-down, and JSON/CSV export.
    """

    def __init__(
        self,
        *,
        session: Session,
        clock: Clock,
        alert_callbacks: list[Callable[[dict[str, Any]], None]] | None = None,
    ) -> None:
        self.session = session
        self.ledger = UnifiedLedgerRepository(session)
        self.clock = clock
        self._alerts = list(alert_callbacks or [])

    def _persist_and_alert(
        self,
        *,
        tier: ReconciliationTier,
        env: RunEnv,
        run_id: str,
        reference_run_id: str | None,
        summary: dict[str, Any],
        items: list[dict[str, Any]],
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self.clock.now_ms()
        report_id = f"recon_{tier.value[:4]}_{uuid.uuid4().hex[:14]}"
        max_sev = DiscrepancySeverity.NONE
        for it in items:
            sev = DiscrepancySeverity(it.get("severity", DiscrepancySeverity.LOW.value))
            if _SEVERITY_RANK[sev] > _SEVERITY_RANK[max_sev]:
                max_sev = sev

        passed = len(items) == 0
        row = ReconciliationReportRecord(
            report_id=report_id,
            tier=tier.value,
            env=env.value,
            run_id=run_id,
            reference_run_id=reference_run_id,
            passed=passed,
            discrepancy_count=len(items),
            max_severity=max_sev.value,
            summary_json=summary,
            items_json=items,
            created_ts_ms=now,
        )
        self.session.add(row)
        self.session.flush()

        result: dict[str, Any] = {
            "report_id": report_id,
            "tier": tier.value,
            "env": env.value,
            "run_id": run_id,
            "reference_run_id": reference_run_id,
            "passed": passed,
            "discrepancy_count": len(items),
            "max_severity": max_sev.value,
            "summary": summary,
            "items": items,
            "created_ts_ms": now,
        }
        if extra_fields:
            result.update(extra_fields)

        if not passed and _SEVERITY_RANK[max_sev] >= _SEVERITY_RANK[DiscrepancySeverity.MEDIUM]:
            for cb in self._alerts:
                cb(result)
        return result

    # ------------------------------------------------------------------
    # Tier 1: System Local DB vs Authoritative Exchange State
    # ------------------------------------------------------------------

    def reconcile_exchange_vs_local(
        self,
        run_id: str,
        broker: BrokerAdapter,
        *,
        auto_heal: bool = False,
    ) -> dict[str, Any]:
        now = self.clock.now_ms()
        items: list[dict[str, Any]] = []

        # 1. Open Orders comparison
        local_orders = self.ledger.list_orders(RunEnv.LIVE, run_id)
        local_open = {
            o.client_order_id: o for o in local_orders if o.status not in TERMINAL_ORDER_STATUSES
        }
        remote_open = {o.client_order_id: o for o in broker.query_open_orders()}

        for cid, loc_o in local_open.items():
            if cid not in remote_open:
                remote_actual = broker.query_order(cid, loc_o.exchange, loc_o.symbol)
                items.append(
                    {
                        "type": "PHANTOM_LOCAL_OPEN_ORDER",
                        "severity": DiscrepancySeverity.HIGH.value,
                        "symbol": loc_o.symbol,
                        "order_id": cid,
                        "exchange_order_id": loc_o.exchange_order_id,
                        "fill_id": None,
                        "ts_ms": loc_o.updated_ts_ms,
                        "local_value": loc_o.status.value,
                        "exchange_value": remote_actual.status.value if remote_actual else "MISSING_ON_EXCHANGE",
                    }
                )
                if auto_heal:
                    if remote_actual is not None:
                        self.ledger.upsert_order(remote_actual)
                    else:
                        self.ledger.upsert_order(
                            loc_o.model_copy(
                                update={"status": OrderStatus.CANCELED, "remaining_qty": ZERO, "updated_ts_ms": now}
                            )
                        )

        for cid, rem_o in remote_open.items():
            if cid not in local_open:
                items.append(
                    {
                        "type": "MISSING_LOCAL_OPEN_ORDER",
                        "severity": DiscrepancySeverity.HIGH.value,
                        "symbol": rem_o.symbol,
                        "order_id": cid,
                        "exchange_order_id": rem_o.exchange_order_id,
                        "fill_id": None,
                        "ts_ms": rem_o.exchange_ts_ms,
                        "local_value": "MISSING",
                        "exchange_value": rem_o.status.value,
                    }
                )
                if auto_heal:
                    self.ledger.upsert_order(rem_o.model_copy(update={"run_id": run_id}))

        # 2. Recent Fills comparison
        local_fills = {f.fill_id: f for f in self.ledger.list_fills(RunEnv.LIVE, run_id)}
        remote_fills_map: dict[str, Fill] = {}
        for f_list in getattr(broker, "recorded_fills_by_cid", {}).values():
            for rf in f_list:
                remote_fills_map[rf.fill_id] = rf

        for fid, rf in remote_fills_map.items():
            if fid not in local_fills:
                items.append(
                    {
                        "type": "MISSING_LOCAL_FILL",
                        "severity": DiscrepancySeverity.CRITICAL.value,
                        "symbol": rf.symbol,
                        "order_id": rf.client_order_id,
                        "exchange_order_id": rf.exchange_order_id,
                        "fill_id": fid,
                        "ts_ms": rf.exchange_ts_ms,
                        "local_value": "0",
                        "exchange_value": format_decimal(rf.quantity),
                    }
                )
                if auto_heal:
                    self.ledger.record_fill(rf.model_copy(update={"run_id": run_id}))

        # 3. Positions comparison
        local_pos_map = {(p.exchange, p.symbol): p for p in self.ledger.list_positions(RunEnv.LIVE, run_id)}
        remote_pos_map = {(p.exchange, p.symbol): p for p in broker.query_positions()}
        for key in sorted(set(local_pos_map.keys()) | set(remote_pos_map.keys())):
            lp = local_pos_map.get(key)
            rp = remote_pos_map.get(key)
            lq = lp.quantity if lp else ZERO
            rq = rp.quantity if rp else ZERO
            if lq != rq:
                items.append(
                    {
                        "type": "POSITION_QTY_MISMATCH",
                        "severity": DiscrepancySeverity.CRITICAL.value,
                        "symbol": key[1],
                        "order_id": None,
                        "fill_id": None,
                        "ts_ms": rp.updated_ts_ms if rp else now,
                        "local_value": format_decimal(lq),
                        "exchange_value": format_decimal(rq),
                        "delta": format_decimal(rq - lq),
                    }
                )
                if auto_heal and rp is not None:
                    self.ledger.upsert_position(rp.model_copy(update={"run_id": run_id}))

        # 4. Balances comparison (latest snapshot per asset)
        bal_rows = list(
            self.session.execute(
                select(BalanceSnapshotRecord)
                .where(BalanceSnapshotRecord.env == RunEnv.LIVE.value, BalanceSnapshotRecord.run_id == run_id)
                .order_by(BalanceSnapshotRecord.ts_ms.desc(), BalanceSnapshotRecord.id.desc())
            )
            .scalars()
            .all()
        )
        latest_local_bal: dict[tuple[str, str], BalanceSnapshotRecord] = {}
        for br in bal_rows:
            latest_local_bal.setdefault((br.exchange, br.asset), br)

        for rb in broker.query_balance():
            lb = latest_local_bal.get((rb.exchange, rb.asset))
            l_tot = lb.total if lb is not None else ZERO
            if l_tot != rb.total:
                items.append(
                    {
                        "type": "BALANCE_TOTAL_MISMATCH",
                        "severity": DiscrepancySeverity.HIGH.value,
                        "symbol": rb.asset,
                        "order_id": None,
                        "fill_id": None,
                        "ts_ms": rb.ts_ms,
                        "local_value": format_decimal(l_tot),
                        "exchange_value": format_decimal(rb.total),
                        "delta": format_decimal(rb.total - l_tot),
                    }
                )
                if auto_heal:
                    self.ledger.record_balance_snapshot(rb.model_copy(update={"run_id": run_id}))

        # 5. Funding Payments comparison
        local_fps = {fp.payment_id: fp for fp in self.ledger.list_funding_payments(RunEnv.LIVE, run_id)}
        for rfp in broker.query_funding_payments():
            if rfp.payment_id not in local_fps:
                items.append(
                    {
                        "type": "MISSING_LOCAL_FUNDING",
                        "severity": DiscrepancySeverity.MEDIUM.value,
                        "symbol": rfp.symbol,
                        "order_id": None,
                        "fill_id": rfp.payment_id,
                        "ts_ms": rfp.settlement_ts_ms,
                        "local_value": "0",
                        "exchange_value": format_decimal(rfp.amount),
                    }
                )
                if auto_heal:
                    self.ledger.record_funding_payment(rfp.model_copy(update={"run_id": run_id}))

        summary = {
            "checked_open_orders": len(remote_open),
            "checked_positions": len(remote_pos_map),
            "checked_fills": len(remote_fills_map),
            "discrepancy_count": len(items),
            "auto_healed": auto_heal,
        }
        return self._persist_and_alert(
            tier=ReconciliationTier.EXCHANGE_VS_LOCAL,
            env=RunEnv.LIVE,
            run_id=run_id,
            reference_run_id=None,
            summary=summary,
            items=items,
            extra_fields={"healed": auto_heal},
        )

    # ------------------------------------------------------------------
    # Tier 2: Shadow vs Live (Aggregated by UTC Day x Symbol + Drill-down)
    # ------------------------------------------------------------------

    def reconcile_shadow_vs_live(self, shadow_run_id: str, live_run_id: str) -> dict[str, Any]:
        sh_fills = self.ledger.list_fills(RunEnv.SHADOW, shadow_run_id)
        lv_fills = self.ledger.list_fills(RunEnv.LIVE, live_run_id)
        sh_fps = self.ledger.list_funding_payments(RunEnv.SHADOW, shadow_run_id)
        lv_fps = self.ledger.list_funding_payments(RunEnv.LIVE, live_run_id)

        keys: set[tuple[str, str]] = set()
        sh_by_key: dict[tuple[str, str], list[Fill]] = {}
        lv_by_key: dict[tuple[str, str], list[Fill]] = {}
        for f in sh_fills:
            k = (_utc_date_str(f.exchange_ts_ms), f.symbol)
            keys.add(k)
            sh_by_key.setdefault(k, []).append(f)
        for f in lv_fills:
            k = (_utc_date_str(f.exchange_ts_ms), f.symbol)
            keys.add(k)
            lv_by_key.setdefault(k, []).append(f)

        sh_fp_by_key: dict[tuple[str, str], Decimal] = {}
        lv_fp_by_key: dict[tuple[str, str], Decimal] = {}
        for fp in sh_fps:
            k = (_utc_date_str(fp.settlement_ts_ms), fp.symbol)
            keys.add(k)
            sh_fp_by_key[k] = sh_fp_by_key.get(k, ZERO) + fp.amount
        for fp in lv_fps:
            k = (_utc_date_str(fp.settlement_ts_ms), fp.symbol)
            keys.add(k)
            lv_fp_by_key[k] = lv_fp_by_key.get(k, ZERO) + fp.amount

        by_day_symbol: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []

        for date_str, sym in sorted(keys):
            sf_list = sh_by_key.get((date_str, sym), [])
            lf_list = lv_by_key.get((date_str, sym), [])
            s_qty = sum((x.quantity for x in sf_list), ZERO)
            l_qty = sum((x.quantity for x in lf_list), ZERO)
            s_quote = sum((x.quote_qty for x in sf_list), ZERO)
            l_quote = sum((x.quote_qty for x in lf_list), ZERO)
            s_vwap = (s_quote / s_qty) if s_qty > ZERO else ZERO
            l_vwap = (l_quote / l_qty) if l_qty > ZERO else ZERO
            vwap_bps = ((l_vwap - s_vwap) / s_vwap * BPS_DIVISOR) if s_vwap > ZERO and l_vwap > ZERO else ZERO

            s_fee = sum((x.fee for x in sf_list), ZERO)
            l_fee = sum((x.fee for x in lf_list), ZERO)
            s_fund = sh_fp_by_key.get((date_str, sym), ZERO)
            l_fund = lv_fp_by_key.get((date_str, sym), ZERO)

            bucket = {
                "date": date_str,
                "symbol": sym,
                "shadow_fill_qty": format_decimal(s_qty),
                "live_fill_qty": format_decimal(l_qty),
                "qty_delta": format_decimal(l_qty - s_qty),
                "shadow_vwap": format_decimal(s_vwap),
                "live_vwap": format_decimal(l_vwap),
                "vwap_diff_bps": format_decimal(vwap_bps),
                "fee_delta": format_decimal(l_fee - s_fee),
                "funding_delta": format_decimal(l_fund - s_fund),
            }
            by_day_symbol.append(bucket)

            if l_qty != s_qty or abs(vwap_bps) > Decimal("1"):
                sample_live = lf_list[0] if lf_list else None
                sample_sh = sf_list[0] if sf_list else None
                ref_fill = sample_live or sample_sh
                items.append(
                    {
                        "type": "SHADOW_LIVE_EXECUTION_DIVERGENCE",
                        "severity": (
                            DiscrepancySeverity.HIGH.value
                            if abs(vwap_bps) > Decimal("25")
                            else DiscrepancySeverity.MEDIUM.value
                        ),
                        "date": date_str,
                        "symbol": sym,
                        "order_id": ref_fill.client_order_id if ref_fill else None,
                        "fill_id": ref_fill.fill_id if ref_fill else None,
                        "ts_ms": ref_fill.exchange_ts_ms if ref_fill else self.clock.now_ms(),
                        "qty_delta": format_decimal(l_qty - s_qty),
                        "vwap_diff_bps": format_decimal(vwap_bps),
                    }
                )

        summary = {
            "shadow_run_id": shadow_run_id,
            "live_run_id": live_run_id,
            "by_day_symbol": by_day_symbol,
        }
        return self._persist_and_alert(
            tier=ReconciliationTier.SHADOW_VS_LIVE,
            env=RunEnv.LIVE,
            run_id=live_run_id,
            reference_run_id=shadow_run_id,
            summary=summary,
            items=items,
        )

    # ------------------------------------------------------------------
    # Tier 3: Backtest vs Shadow (6-Dimension Attribution + Drill-down)
    # ------------------------------------------------------------------

    def reconcile_backtest_vs_shadow(self, backtest_run_id: str, shadow_run_id: str) -> dict[str, Any]:
        bt_orders = self.ledger.list_orders(RunEnv.BACKTEST, backtest_run_id)
        sh_orders = self.ledger.list_orders(RunEnv.SHADOW, shadow_run_id)
        bt_fills = self.ledger.list_fills(RunEnv.BACKTEST, backtest_run_id)
        sh_fills = self.ledger.list_fills(RunEnv.SHADOW, shadow_run_id)
        bt_fps = self.ledger.list_funding_payments(RunEnv.BACKTEST, backtest_run_id)
        sh_fps = self.ledger.list_funding_payments(RunEnv.SHADOW, shadow_run_id)

        bt_traces = {
            t.signal_id: t
            for t in self.session.execute(
                select(SignalTraceRecord).where(
                    SignalTraceRecord.env == RunEnv.BACKTEST.value,
                    SignalTraceRecord.run_id == backtest_run_id,
                )
            )
            .scalars()
            .all()
        }
        sh_traces = {
            t.signal_id: t
            for t in self.session.execute(
                select(SignalTraceRecord).where(
                    SignalTraceRecord.env == RunEnv.SHADOW.value,
                    SignalTraceRecord.run_id == shadow_run_id,
                )
            )
            .scalars()
            .all()
        }

        # 1. Slippage delta (notional-weighted bps)
        def _avg_slip(fills: list[Fill]) -> Decimal:
            tot_q = sum((f.quote_qty for f in fills), ZERO)
            if tot_q <= ZERO:
                return ZERO
            return sum((f.slippage_bps * f.quote_qty for f in fills), ZERO) / tot_q

        bt_slip = _avg_slip(bt_fills)
        sh_slip = _avg_slip(sh_fills)
        slip_delta = sh_slip - bt_slip

        # 2. Fill rate delta (filled_qty / requested_qty)
        bt_req = sum((o.quantity for o in bt_orders), ZERO)
        sh_req = sum((o.quantity for o in sh_orders), ZERO)
        bt_fr = (sum((o.filled_qty for o in bt_orders), ZERO) / bt_req) if bt_req > ZERO else ZERO
        sh_fr = (sum((o.filled_qty for o in sh_orders), ZERO) / sh_req) if sh_req > ZERO else ZERO
        fr_delta = sh_fr - bt_fr

        # 3. Latency delta (mean(fill_ts_ms - created_ts_ms))
        def _avg_latency(orders, fills) -> Decimal:
            ord_created = {o.client_order_id: o.created_ts_ms for o in orders}
            diffs = [
                Decimal(f.exchange_ts_ms - ord_created[f.client_order_id])
                for f in fills
                if f.client_order_id in ord_created
            ]
            return (sum(diffs, ZERO) / Decimal(len(diffs))) if diffs else ZERO

        bt_lat = _avg_latency(bt_orders, bt_fills)
        sh_lat = _avg_latency(sh_orders, sh_fills)
        lat_delta = sh_lat - bt_lat

        # 4. Fee delta
        bt_fee = sum((f.fee for f in bt_fills), ZERO)
        sh_fee = sum((f.fee for f in sh_fills), ZERO)
        fee_delta = sh_fee - bt_fee

        # 5. Funding delta
        bt_fund = sum((fp.amount for fp in bt_fps), ZERO)
        sh_fund = sum((fp.amount for fp in sh_fps), ZERO)
        fund_delta = sh_fund - bt_fund

        # 6. Data / Snapshot divergence across signals
        items: list[dict[str, Any]] = []
        data_div_count = 0
        sh_fills_by_cid = {f.client_order_id: f for f in sh_fills}

        for sig_id in sorted(set(bt_traces.keys()) | set(sh_traces.keys())):
            bt_t = bt_traces.get(sig_id)
            sh_t = sh_traces.get(sig_id)
            if bt_t is None or sh_t is None or bt_t.snapshot_ref != sh_t.snapshot_ref:
                data_div_count += 1
                sf = sh_fills_by_cid.get(sh_t.client_order_id or "") if sh_t else None
                items.append(
                    {
                        "type": "BACKTEST_SHADOW_DATA_SNAPSHOT_DIVERGENCE",
                        "severity": DiscrepancySeverity.MEDIUM.value,
                        "signal_id": sig_id,
                        "symbol": (sh_t or bt_t).symbol,
                        "order_id": sh_t.client_order_id if sh_t else (bt_t.client_order_id if bt_t else None),
                        "fill_id": sf.fill_id if sf else None,
                        "ts_ms": (sh_t or bt_t).signal_ts_ms,
                        "backtest_snapshot_ref": bt_t.snapshot_ref if bt_t else None,
                        "shadow_snapshot_ref": sh_t.snapshot_ref if sh_t else None,
                    }
                )

        if slip_delta != ZERO or fr_delta != ZERO or lat_delta != ZERO:
            sf0 = sh_fills[0] if sh_fills else None
            items.append(
                {
                    "type": "BACKTEST_SHADOW_EXECUTION_ATTRIBUTION",
                    "severity": (
                        DiscrepancySeverity.HIGH.value
                        if abs(slip_delta) > Decimal("20")
                        else DiscrepancySeverity.LOW.value
                    ),
                    "symbol": sf0.symbol if sf0 else "",
                    "order_id": sf0.client_order_id if sf0 else None,
                    "fill_id": sf0.fill_id if sf0 else None,
                    "ts_ms": sf0.exchange_ts_ms if sf0 else self.clock.now_ms(),
                    "slippage_bps_delta": format_decimal(slip_delta),
                    "fill_rate_delta": format_decimal(fr_delta),
                    "latency_ms_delta": format_decimal(lat_delta),
                }
            )

        attribution = {
            "backtest_slippage_bps": format_decimal(bt_slip),
            "shadow_slippage_bps": format_decimal(sh_slip),
            "slippage_bps_delta": format_decimal(slip_delta),
            "backtest_fill_rate": format_decimal(bt_fr),
            "shadow_fill_rate": format_decimal(sh_fr),
            "fill_rate_delta": format_decimal(fr_delta),
            "backtest_latency_ms": format_decimal(bt_lat),
            "shadow_latency_ms": format_decimal(sh_lat),
            "latency_ms_delta": format_decimal(lat_delta),
            "fee_delta": format_decimal(fee_delta),
            "funding_delta": format_decimal(fund_delta),
            "data_divergence_count": data_div_count,
        }
        summary = {
            "backtest_run_id": backtest_run_id,
            "shadow_run_id": shadow_run_id,
            "attribution": attribution,
        }
        return self._persist_and_alert(
            tier=ReconciliationTier.BACKTEST_VS_SHADOW,
            env=RunEnv.SHADOW,
            run_id=shadow_run_id,
            reference_run_id=backtest_run_id,
            summary=summary,
            items=items,
        )

    # ------------------------------------------------------------------
    # Drill-Down & Export Helpers
    # ------------------------------------------------------------------

    def get_report(self, report_id: str) -> ReconciliationReportRecord:
        row = self.session.execute(
            select(ReconciliationReportRecord).where(ReconciliationReportRecord.report_id == report_id)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"Reconciliation report {report_id} not found")
        return row

    def drill_down(
        self,
        report_id: str,
        *,
        symbol: str | None = None,
        min_severity: DiscrepancySeverity = DiscrepancySeverity.LOW,
    ) -> list[dict[str, Any]]:
        row = self.get_report(report_id)
        out: list[dict[str, Any]] = []
        for item in row.items_json:
            sev = DiscrepancySeverity(item.get("severity", DiscrepancySeverity.LOW.value))
            if _SEVERITY_RANK[sev] < _SEVERITY_RANK[min_severity]:
                continue
            if symbol is not None and item.get("symbol") != symbol:
                continue
            out.append(item)
        return out

    def export_report_json(self, report_id: str) -> str:
        row = self.get_report(report_id)
        payload = {
            "report_id": row.report_id,
            "tier": row.tier,
            "env": row.env,
            "run_id": row.run_id,
            "reference_run_id": row.reference_run_id,
            "passed": row.passed,
            "discrepancy_count": row.discrepancy_count,
            "max_severity": row.max_severity,
            "summary": row.summary_json,
            "items": row.items_json,
            "created_ts_ms": row.created_ts_ms,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def export_report_csv(self, report_id: str) -> str:
        row = self.get_report(report_id)
        buf = io.StringIO()
        cols = ["type", "severity", "symbol", "order_id", "fill_id", "ts_ms", "local_value", "exchange_value", "delta"]
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for item in row.items_json:
            writer.writerow(item)
        return buf.getvalue()
