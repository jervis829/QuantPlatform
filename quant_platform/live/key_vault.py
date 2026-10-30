"""Encrypted API Key Vault enforcing no-withdraw permission, IP whitelist, and rotation audit trail (P8)."""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from quant_platform.core.enums import RunEnv
from quant_platform.core.interfaces import Clock
from quant_platform.db.schema import ApiKeyVaultRecord
from quant_platform.db.session import append_audit_log
from quant_platform.exceptions import QuantPlatformError


class KeyVaultSecurityError(QuantPlatformError):
    """Raised when an API key violates mandatory security policies (withdrawal enabled, missing IP whitelist, etc.)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code=code)


class ApiKeyVault:
    """
    Fernet-encrypted storage for exchange API keys.
    Security invariants:
    - Keys with withdrawal permissions (`can_withdraw=True`) are unconditionally rejected;
    - Production (`is_testnet=False`) keys require non-empty `ip_whitelist` by default;
    - Rotation updates ciphertext and records a hash-chained `AuditLogRecord`.
    """

    def __init__(self, *, session: Session, master_key: str | bytes, clock: Clock) -> None:
        self.session = session
        key_bytes = master_key.encode("ascii") if isinstance(master_key, str) else master_key
        self._fernet = Fernet(key_bytes)
        self.clock = clock

    def _encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def _decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")

    def store_key(
        self,
        *,
        key_id: str,
        exchange: str,
        account_label: str,
        env: RunEnv,
        api_key: str,
        api_secret: str,
        passphrase: str | None = None,
        ip_whitelist: list[str] | None = None,
        can_trade: bool = True,
        can_withdraw: bool = False,
        is_testnet: bool = True,
        require_ip_whitelist: bool | None = None,
        operator: str = "system",
    ) -> ApiKeyVaultRecord:
        if env == RunEnv.SHADOW:
            raise KeyVaultSecurityError("SHADOW_KEY_FORBIDDEN", "Shadow environment cannot bind exchange API keys.")
        if can_withdraw:
            raise KeyVaultSecurityError(
                "WITHDRAW_PERMISSION_FORBIDDEN",
                f"API key {key_id} has withdrawal permission enabled, which is strictly forbidden.",
            )
        whitelist = list(ip_whitelist or [])
        must_whitelist = (not is_testnet) if require_ip_whitelist is None else require_ip_whitelist
        if must_whitelist and not whitelist:
            raise KeyVaultSecurityError(
                "IP_WHITELIST_REQUIRED",
                f"API key {key_id} requires a non-empty IP whitelist.",
            )

        now = self.clock.now_ms()
        row = ApiKeyVaultRecord(
            key_id=key_id,
            exchange=exchange,
            account_label=account_label,
            env=env.value,
            encrypted_api_key=self._encrypt(api_key),
            encrypted_api_secret=self._encrypt(api_secret),
            encrypted_passphrase=self._encrypt(passphrase) if passphrase else None,
            ip_whitelist_json=whitelist,
            can_trade=can_trade,
            can_withdraw=False,
            is_testnet=is_testnet,
            is_active=True,
            rotated_at_ms=None,
            created_at_ms=now,
        )
        self.session.add(row)
        self.session.flush()
        append_audit_log(
            self.session,
            actor=operator,
            action="API_KEY_STORED",
            resource_type="api_key",
            resource_id=key_id,
            payload={
                "exchange": exchange,
                "account_label": account_label,
                "is_testnet": is_testnet,
                "ip_whitelist_count": len(whitelist),
            },
            ts_ms=now,
        )
        return row

    def load_decrypted_credentials(self, key_id: str) -> dict[str, Any]:
        row = self.session.execute(
            select(ApiKeyVaultRecord).where(ApiKeyVaultRecord.key_id == key_id)
        ).scalar_one_or_none()
        if row is None or not row.is_active:
            raise KeyVaultSecurityError("KEY_NOT_FOUND_OR_INACTIVE", f"API key {key_id} not found or inactive.")
        if row.can_withdraw:
            raise KeyVaultSecurityError("WITHDRAW_PERMISSION_FORBIDDEN", f"API key {key_id} has withdraw enabled.")
        return {
            "key_id": row.key_id,
            "exchange": row.exchange,
            "account_label": row.account_label,
            "api_key": self._decrypt(row.encrypted_api_key),
            "api_secret": self._decrypt(row.encrypted_api_secret),
            "passphrase": self._decrypt(row.encrypted_passphrase) if row.encrypted_passphrase else None,
            "ip_whitelist": list(row.ip_whitelist_json),
            "is_testnet": row.is_testnet,
        }

    def rotate_key(
        self,
        *,
        key_id: str,
        new_api_key: str,
        new_api_secret: str,
        new_passphrase: str | None = None,
        operator: str = "system",
    ) -> ApiKeyVaultRecord:
        row = self.session.execute(
            select(ApiKeyVaultRecord).where(ApiKeyVaultRecord.key_id == key_id)
        ).scalar_one_or_none()
        if row is None:
            raise KeyVaultSecurityError("KEY_NOT_FOUND", f"API key {key_id} does not exist.")
        now = self.clock.now_ms()
        row.encrypted_api_key = self._encrypt(new_api_key)
        row.encrypted_api_secret = self._encrypt(new_api_secret)
        if new_passphrase is not None:
            row.encrypted_passphrase = self._encrypt(new_passphrase)
        row.rotated_at_ms = now
        self.session.flush()
        append_audit_log(
            self.session,
            actor=operator,
            action="API_KEY_ROTATED",
            resource_type="api_key",
            resource_id=key_id,
            payload={"exchange": row.exchange, "rotated_at_ms": now},
            ts_ms=now,
        )
        return row
