"""Versioned SymbolSpec persistence, historical lookup, and change alerting service (P2)."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.decimal_utils import format_decimal
from quant_platform.core.enums import ContractType, InstrumentStatus
from quant_platform.core.hashing import canonical_json, sha256_hex
from quant_platform.core.models import FeeTier, MarginBracket, SymbolSpec
from quant_platform.db.schema import InstrumentRecord, SymbolSpecRecord
from quant_platform.db.session import append_audit_log


def _spec_fingerprint(spec: SymbolSpec) -> dict[str, Any]:
    """Compare business-significant specification fields ignoring version number and effective_from_ms."""
    return {
        "contract_type": spec.contract_type.value,
        "base_asset": spec.base_asset,
        "quote_asset": spec.quote_asset,
        "margin_asset": spec.margin_asset,
        "tick_size": format_decimal(spec.tick_size),
        "step_size": format_decimal(spec.step_size),
        "min_qty": format_decimal(spec.min_qty),
        "max_qty": format_decimal(spec.max_qty),
        "min_notional": format_decimal(spec.min_notional),
        "contract_size": format_decimal(spec.contract_size),
        "maker_fee": format_decimal(spec.maker_fee),
        "taker_fee": format_decimal(spec.taker_fee),
        "fee_tiers": [t.model_dump(mode="json") for t in spec.fee_tiers],
        "max_leverage": format_decimal(spec.max_leverage),
        "margin_brackets": [b.model_dump(mode="json") for b in spec.margin_brackets],
        "funding_interval_hours": spec.funding_interval_hours,
        "settlement_ts_ms": spec.settlement_ts_ms,
        "delisted_at_ms": spec.delisted_at_ms,
    }


def _row_to_model(row: SymbolSpecRecord) -> SymbolSpec:
    return SymbolSpec(
        spec_version_id=row.spec_version_id,
        exchange=row.exchange,
        symbol=row.symbol,
        version=row.version,
        effective_from_ms=row.effective_from_ms,
        effective_to_ms=row.effective_to_ms,
        contract_type=ContractType(row.contract_type),
        base_asset=row.base_asset,
        quote_asset=row.quote_asset,
        margin_asset=row.margin_asset,
        tick_size=row.tick_size,
        step_size=row.step_size,
        min_qty=row.min_qty,
        max_qty=row.max_qty,
        min_notional=row.min_notional,
        contract_size=row.contract_size,
        maker_fee=row.maker_fee,
        taker_fee=row.taker_fee,
        fee_tiers=[FeeTier(**t) for t in row.fee_tiers_json],
        max_leverage=row.max_leverage,
        margin_brackets=[MarginBracket(**b) for b in row.margin_brackets_json],
        funding_interval_hours=row.funding_interval_hours,
        settlement_ts_ms=row.settlement_ts_ms,
        delisted_at_ms=row.delisted_at_ms,
        spec_hash=row.spec_hash,
    )


class SymbolSpecService:
    """First-class versioned SymbolSpec repository, change detector, and historical resolver."""

    def __init__(
        self,
        session: Session,
        alert_callbacks: list[Callable[[dict[str, Any]], None]] | None = None,
    ) -> None:
        self.session = session
        self._alerts: list[Callable[[dict[str, Any]], None]] = list(alert_callbacks or [])

    def register_alert_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._alerts.append(callback)

    def _ensure_instrument(self, spec: SymbolSpec, ts_ms: int) -> None:
        inst = self.session.execute(
            select(InstrumentRecord).where(
                InstrumentRecord.exchange == spec.exchange,
                InstrumentRecord.symbol == spec.symbol,
            )
        ).scalar_one_or_none()
        status = InstrumentStatus.DELISTED.value if spec.delisted_at_ms is not None else InstrumentStatus.TRADING.value
        if inst is None:
            self.session.add(
                InstrumentRecord(
                    exchange=spec.exchange,
                    symbol=spec.symbol,
                    base_asset=spec.base_asset,
                    quote_asset=spec.quote_asset,
                    settle_asset=spec.margin_asset,
                    contract_type=spec.contract_type.value,
                    status=status,
                    created_at_ms=ts_ms,
                )
            )
        else:
            inst.status = status
        self.session.flush()

    def sync_spec(
        self,
        incoming: SymbolSpec,
        *,
        synced_at_ms: int,
        actor: str = "spec_sync",
    ) -> tuple[SymbolSpec, bool]:
        """
        Persist initial or updated SymbolSpec version.
        Returns (active_spec, was_inserted_or_updated).
        Emits SYMBOL_SPEC_CHANGED alert + audit log whenever an existing symbol's spec mutates!
        """
        self._ensure_instrument(incoming, synced_at_ms)

        latest_row = self.session.execute(
            select(SymbolSpecRecord)
            .where(
                SymbolSpecRecord.exchange == incoming.exchange,
                SymbolSpecRecord.symbol == incoming.symbol,
            )
            .order_by(SymbolSpecRecord.version.desc())
            .limit(1)
        ).scalar_one_or_none()

        if latest_row is not None:
            latest_model = _row_to_model(latest_row)
            old_fp = _spec_fingerprint(latest_model)
            new_fp = _spec_fingerprint(incoming)
            if old_fp == new_fp:
                return latest_model, False

            changes: dict[str, dict[str, Any]] = {}
            for k, old_val in old_fp.items():
                new_val = new_fp[k]
                if old_val != new_val:
                    changes[k] = {"old": old_val, "new": new_val}

            effective_from = incoming.effective_from_ms or synced_at_ms
            latest_row.effective_to_ms = effective_from
            next_version = latest_row.version + 1
            versioned_spec = incoming.model_copy(
                update={
                    "version": next_version,
                    "effective_from_ms": effective_from,
                    "effective_to_ms": None,
                    "spec_hash": "",
                    "spec_version_id": "",
                }
            )
            # Re-run validator by re-instantiating SymbolSpec
            versioned_spec = SymbolSpec(**versioned_spec.model_dump())

            new_row = self._build_row(versioned_spec, synced_at_ms)
            self.session.add(new_row)
            self.session.flush()

            alert_payload = {
                "code": "SYMBOL_SPEC_CHANGED",
                "exchange": versioned_spec.exchange,
                "symbol": versioned_spec.symbol,
                "old_version": latest_row.version,
                "new_version": versioned_spec.version,
                "changes": changes,
                "effective_from_ms": effective_from,
            }
            append_audit_log(
                self.session,
                actor=actor,
                action="SYMBOL_SPEC_UPDATED",
                resource_type="symbol_spec",
                resource_id=versioned_spec.spec_version_id,
                payload=alert_payload,
                ts_ms=synced_at_ms,
            )
            for cb in self._alerts:
                cb(alert_payload)
            return versioned_spec, True

        first_spec = SymbolSpec(
            **incoming.model_copy(
                update={
                    "version": 1,
                    "spec_hash": "",
                    "spec_version_id": "",
                }
            ).model_dump()
        )
        row = self._build_row(first_spec, synced_at_ms)
        self.session.add(row)
        self.session.flush()
        append_audit_log(
            self.session,
            actor=actor,
            action="SYMBOL_SPEC_CREATED",
            resource_type="symbol_spec",
            resource_id=first_spec.spec_version_id,
            payload={"exchange": first_spec.exchange, "symbol": first_spec.symbol, "version": 1},
            ts_ms=synced_at_ms,
        )
        return first_spec, True

    def _build_row(self, spec: SymbolSpec, created_at_ms: int) -> SymbolSpecRecord:
        return SymbolSpecRecord(
            spec_version_id=spec.spec_version_id,
            exchange=spec.exchange,
            symbol=spec.symbol,
            version=spec.version,
            effective_from_ms=spec.effective_from_ms,
            effective_to_ms=spec.effective_to_ms,
            contract_type=spec.contract_type.value,
            base_asset=spec.base_asset,
            quote_asset=spec.quote_asset,
            margin_asset=spec.margin_asset,
            tick_size=spec.tick_size,
            step_size=spec.step_size,
            min_qty=spec.min_qty,
            max_qty=spec.max_qty,
            min_notional=spec.min_notional,
            contract_size=spec.contract_size,
            maker_fee=spec.maker_fee,
            taker_fee=spec.taker_fee,
            fee_tiers_json=[t.model_dump(mode="json") for t in spec.fee_tiers],
            max_leverage=spec.max_leverage,
            margin_brackets_json=[b.model_dump(mode="json") for b in spec.margin_brackets],
            funding_interval_hours=spec.funding_interval_hours,
            settlement_ts_ms=spec.settlement_ts_ms,
            delisted_at_ms=spec.delisted_at_ms,
            spec_hash=spec.spec_hash,
            created_at_ms=created_at_ms,
        )

    def get_spec(self, exchange: str, symbol: str, at_ts_ms: int | None = None) -> SymbolSpec:
        rows = self.session.execute(
            select(SymbolSpecRecord)
            .where(
                SymbolSpecRecord.exchange == exchange,
                SymbolSpecRecord.symbol == symbol,
            )
            .order_by(SymbolSpecRecord.effective_from_ms.asc(), SymbolSpecRecord.version.asc())
        ).scalars().all()
        if not rows:
            raise KeyError(f"No SymbolSpec found for {exchange}:{symbol}")
        if at_ts_ms is None:
            return _row_to_model(rows[-1])
        matched: SymbolSpecRecord | None = None
        for r in rows:
            if r.effective_from_ms <= at_ts_ms and (r.effective_to_ms is None or at_ts_ms < r.effective_to_ms):
                matched = r
        if matched is None:
            raise KeyError(f"No active SymbolSpec for {exchange}:{symbol} at ts={at_ts_ms}")
        return _row_to_model(matched)

    def list_versions(self, exchange: str, symbol: str) -> list[SymbolSpec]:
        rows = self.session.execute(
            select(SymbolSpecRecord)
            .where(SymbolSpecRecord.exchange == exchange, SymbolSpecRecord.symbol == symbol)
            .order_by(SymbolSpecRecord.version.asc())
        ).scalars().all()
        return [_row_to_model(r) for r in rows]

    @staticmethod
    def composite_version_hash(specs: list[SymbolSpec]) -> str:
        items = sorted((s.spec_version_id, s.spec_hash) for s in specs)
        digest = sha256_hex(canonical_json(items))
        return f"specs_{digest[:24]}"
