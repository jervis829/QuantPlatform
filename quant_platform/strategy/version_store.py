"""Versioned strategy repository with unified diff, approval state, and append-only audit trail (P3 / Red Lines 7 & 8)."""

from __future__ import annotations

import difflib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.enums import ApprovalStatus, StrategySourceType
from quant_platform.core.hashing import sha256_hex
from quant_platform.core.interfaces import StrategySandbox
from quant_platform.db.schema import StrategyMetaRecord, StrategyVersionRecord
from quant_platform.db.session import append_audit_log
from quant_platform.strategy.ast_checker import ASTSecurityChecker
from quant_platform.strategy.registry import compile_strategy_class, extract_param_schema_and_defaults


def _render_snapshot_for_diff(code: str, params: dict[str, Any]) -> list[str]:
    formatted_params = json.dumps(params, indent=2, sort_keys=True)
    combined = f"# --- PARAMS ---\n{formatted_params}\n# --- CODE ---\n{code.strip()}\n"
    return combined.splitlines(keepends=True)


def compute_unified_diff(
    old_code: str,
    old_params: dict[str, Any],
    new_code: str,
    new_params: dict[str, Any],
    from_label: str,
    to_label: str,
) -> str:
    old_lines = _render_snapshot_for_diff(old_code, old_params) if (old_code or old_params) else []
    new_lines = _render_snapshot_for_diff(new_code, new_params)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_label,
            tofile=to_label,
        )
    )


class StrategyVersionStore:
    """
    Single unified pipeline for strategy code/parameter versioning, sandbox verification,
    manual approval state, and tamper-evident audit logging.
    """

    def __init__(self, session: Session, sandbox: StrategySandbox) -> None:
        self.session = session
        self.sandbox = sandbox
        self._checker = ASTSecurityChecker()

    def submit_strategy_code(
        self,
        *,
        strategy_id: str,
        name: str,
        code: str,
        author: str,
        ts_ms: int,
        description: str = "",
        params: dict[str, Any] | None = None,
        source_type: StrategySourceType = StrategySourceType.UPLOAD,
        generation_prompt: str | None = None,
        backtest_gate_report: dict[str, Any] | None = None,
    ) -> StrategyVersionRecord:
        # Step 1: Mandatory AST static check
        self._checker.assert_safe(code)

        # Step 2: Compile & validate Pydantic ParamModel schema + parameters
        cls = compile_strategy_class(code, checker=self._checker)
        param_schema, validated_params = extract_param_schema_and_defaults(cls, params)

        # Step 3: Mandatory isolated sandbox smoke verification (no shortcuts for upload OR LLM!)
        smoke_result = self.sandbox.execute_isolated(
            code,
            {"action": "smoke_test", "params": validated_params},
        )

        # Step 4: Version persistence + unified diff
        meta = self.session.execute(
            select(StrategyMetaRecord).where(StrategyMetaRecord.strategy_id == strategy_id)
        ).scalar_one_or_none()

        prev_ver_row = self.session.execute(
            select(StrategyVersionRecord)
            .where(StrategyVersionRecord.strategy_id == strategy_id)
            .order_by(StrategyVersionRecord.version.desc())
            .limit(1)
        ).scalar_one_or_none()

        next_version = 1 if prev_ver_row is None else prev_ver_row.version + 1
        version_id = f"{strategy_id}:v{next_version}"
        code_hash = sha256_hex(code)

        diff_text = compute_unified_diff(
            old_code=prev_ver_row.code if prev_ver_row else "",
            old_params=prev_ver_row.params_json if prev_ver_row else {},
            new_code=code,
            new_params=validated_params,
            from_label=f"{strategy_id}:v{prev_ver_row.version}" if prev_ver_row else "empty",
            to_label=version_id,
        )

        if meta is None:
            meta = StrategyMetaRecord(
                strategy_id=strategy_id,
                name=name,
                description=description,
                current_version=next_version,
                source_type=source_type.value,
                created_by=author,
                created_at_ms=ts_ms,
                updated_at_ms=ts_ms,
            )
            self.session.add(meta)
        else:
            meta.name = name or meta.name
            if description:
                meta.description = description
            meta.current_version = next_version
            meta.updated_at_ms = ts_ms

        ver_record = StrategyVersionRecord(
            version_id=version_id,
            strategy_id=strategy_id,
            version=next_version,
            code=code,
            code_hash=code_hash,
            param_schema_json=param_schema,
            params_json=validated_params,
            diff_text=diff_text,
            source_type=source_type.value,
            generation_prompt=generation_prompt,
            ast_report_json={"passed": True, "sandbox_smoke": smoke_result},
            backtest_gate_json=backtest_gate_report,
            approval_status=ApprovalStatus.PENDING.value,
            created_by=author,
            created_at_ms=ts_ms,
        )
        self.session.add(ver_record)
        self.session.flush()

        append_audit_log(
            self.session,
            actor=author,
            action="STRATEGY_VERSION_CREATED",
            resource_type="strategy_version",
            resource_id=version_id,
            payload={
                "strategy_id": strategy_id,
                "version": next_version,
                "code_hash": code_hash,
                "source_type": source_type.value,
                "approval_status": ApprovalStatus.PENDING.value,
            },
            ts_ms=ts_ms,
        )
        return ver_record

    def update_strategy_params(
        self,
        *,
        strategy_id: str,
        new_params: dict[str, Any],
        author: str,
        ts_ms: int,
    ) -> StrategyVersionRecord:
        """Any parameter modification creates a new immutable version with unified diff + audit entry."""
        latest = self.get_latest_version(strategy_id)
        cls = compile_strategy_class(latest.code, checker=self._checker)
        param_schema, validated_params = extract_param_schema_and_defaults(cls, new_params)
        smoke_result = self.sandbox.execute_isolated(
            latest.code,
            {"action": "smoke_test", "params": validated_params},
        )

        next_version = latest.version + 1
        version_id = f"{strategy_id}:v{next_version}"
        diff_text = compute_unified_diff(
            old_code=latest.code,
            old_params=latest.params_json,
            new_code=latest.code,
            new_params=validated_params,
            from_label=latest.version_id,
            to_label=version_id,
        )

        meta = self.session.execute(
            select(StrategyMetaRecord).where(StrategyMetaRecord.strategy_id == strategy_id)
        ).scalar_one()
        meta.current_version = next_version
        meta.updated_at_ms = ts_ms

        ver_record = StrategyVersionRecord(
            version_id=version_id,
            strategy_id=strategy_id,
            version=next_version,
            code=latest.code,
            code_hash=latest.code_hash,
            param_schema_json=param_schema,
            params_json=validated_params,
            diff_text=diff_text,
            source_type=latest.source_type,
            generation_prompt=latest.generation_prompt,
            ast_report_json={"passed": True, "sandbox_smoke": smoke_result},
            backtest_gate_json=latest.backtest_gate_json,
            approval_status=ApprovalStatus.PENDING.value,
            created_by=author,
            created_at_ms=ts_ms,
        )
        self.session.add(ver_record)
        self.session.flush()

        append_audit_log(
            self.session,
            actor=author,
            action="STRATEGY_PARAMS_UPDATED",
            resource_type="strategy_version",
            resource_id=version_id,
            payload={
                "strategy_id": strategy_id,
                "from_version": latest.version,
                "to_version": next_version,
                "params": validated_params,
            },
            ts_ms=ts_ms,
        )
        return ver_record

    def approve_version(
        self,
        *,
        version_id: str,
        approver: str,
        ts_ms: int,
        note: str = "",
    ) -> StrategyVersionRecord:
        ver = self.get_version(version_id)
        ver.approval_status = ApprovalStatus.APPROVED.value
        ver.approved_by = approver
        ver.approved_at_ms = ts_ms
        ver.approval_note = note
        self.session.flush()
        append_audit_log(
            self.session,
            actor=approver,
            action="STRATEGY_VERSION_APPROVED",
            resource_type="strategy_version",
            resource_id=version_id,
            payload={"approved_by": approver, "note": note},
            ts_ms=ts_ms,
        )
        return ver

    def reject_version(
        self,
        *,
        version_id: str,
        approver: str,
        ts_ms: int,
        note: str = "",
    ) -> StrategyVersionRecord:
        ver = self.get_version(version_id)
        ver.approval_status = ApprovalStatus.REJECTED.value
        ver.approved_by = approver
        ver.approved_at_ms = ts_ms
        ver.approval_note = note
        self.session.flush()
        append_audit_log(
            self.session,
            actor=approver,
            action="STRATEGY_VERSION_REJECTED",
            resource_type="strategy_version",
            resource_id=version_id,
            payload={"rejected_by": approver, "note": note},
            ts_ms=ts_ms,
        )
        return ver

    def get_version(self, version_id: str) -> StrategyVersionRecord:
        row = self.session.execute(
            select(StrategyVersionRecord).where(StrategyVersionRecord.version_id == version_id)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"StrategyVersion '{version_id}' not found")
        return row

    def get_latest_version(self, strategy_id: str) -> StrategyVersionRecord:
        row = self.session.execute(
            select(StrategyVersionRecord)
            .where(StrategyVersionRecord.strategy_id == strategy_id)
            .order_by(StrategyVersionRecord.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"No versions found for strategy '{strategy_id}'")
        return row

    def list_versions(self, strategy_id: str) -> list[StrategyVersionRecord]:
        return list(
            self.session.execute(
                select(StrategyVersionRecord)
                .where(StrategyVersionRecord.strategy_id == strategy_id)
                .order_by(StrategyVersionRecord.version.desc())
            )
            .scalars()
            .all()
        )

    def list_strategies(self) -> list[StrategyMetaRecord]:
        return list(
            self.session.execute(select(StrategyMetaRecord).order_by(StrategyMetaRecord.updated_at_ms.desc()))
            .scalars()
            .all()
        )

    def get_strategy_meta(self, strategy_id: str) -> StrategyMetaRecord:
        row = self.session.execute(
            select(StrategyMetaRecord).where(StrategyMetaRecord.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"Strategy '{strategy_id}' not found")
        return row
