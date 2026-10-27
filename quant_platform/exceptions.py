"""Domain-specific exception hierarchy for QuantPlatform."""

from __future__ import annotations

from typing import Any


class QuantPlatformError(Exception):
    """Base exception for all platform errors."""

    def __init__(self, message: str, code: str = "QUANT_ERROR", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details: dict[str, Any] = details or {}


class FloatForbiddenError(QuantPlatformError, TypeError):
    """Raised whenever a binary float is passed into monetary or quantity calculations/storage."""

    def __init__(self, field_name: str = "value") -> None:
        super().__init__(
            f"Binary float is forbidden for monetary/quantity field '{field_name}'; use Decimal or str.",
            code="FLOAT_FORBIDDEN",
            details={"field": field_name},
        )


class LookaheadError(QuantPlatformError):
    """Raised by DataFeed when any query attempts to access data beyond the current Clock cursor."""

    def __init__(self, requested_ts_ms: int, current_ts_ms: int, resource: str = "data") -> None:
        super().__init__(
            f"Lookahead violation on {resource}: requested_ts_ms={requested_ts_ms} > cursor_ts_ms={current_ts_ms}",
            code="LOOKAHEAD_VIOLATION",
            details={"requested_ts_ms": requested_ts_ms, "cursor_ts_ms": current_ts_ms, "resource": resource},
        )


class DataIntegrityError(QuantPlatformError):
    """Raised when market data has unrecoverable gaps, out-of-order sequences, or corrupt snapshots."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="DATA_INTEGRITY_ERROR", details=details)


class CrossExchangeIsolationError(QuantPlatformError):
    """Raised when attempting to mix market data across distinct exchanges."""

    def __init__(self, expected_exchange: str, actual_exchange: str, symbol: str) -> None:
        super().__init__(
            f"Cross-exchange data mixing forbidden for {symbol}: expected {expected_exchange}, got {actual_exchange}",
            code="CROSS_EXCHANGE_ISOLATION",
            details={"expected_exchange": expected_exchange, "actual_exchange": actual_exchange, "symbol": symbol},
        )


class ClockSkewError(QuantPlatformError):
    """Raised when local-to-exchange server clock offset exceeds safety threshold."""

    def __init__(self, exchange: str, offset_ms: int, threshold_ms: int) -> None:
        super().__init__(
            f"Exchange {exchange} clock offset {offset_ms}ms exceeds max threshold {threshold_ms}ms",
            code="CLOCK_SKEW_EXCEEDED",
            details={"exchange": exchange, "offset_ms": offset_ms, "threshold_ms": threshold_ms},
        )


class SymbolSpecViolationError(QuantPlatformError):
    """Raised when an order violates versioned SymbolSpec constraints."""

    def __init__(self, reason_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=reason_code, details=details)


class ASTSecurityError(QuantPlatformError):
    """Raised when strategy source code fails AST static security/lookahead checks."""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        summary = "; ".join(f"{v['rule']} (line {v.get('line', '?')}): {v['message']}" for v in violations)
        super().__init__(
            f"AST check blocked strategy code: {summary}",
            code="AST_SECURITY_VIOLATION",
            details={"violations": violations},
        )
        self.violations = violations


class SandboxExecutionError(QuantPlatformError):
    """Raised when sandbox execution fails, times out, or exceeds memory/CPU limits."""

    def __init__(self, reason_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=reason_code, details=details)


class OrderStateTransitionError(QuantPlatformError):
    """Raised on illegal order state machine transition."""

    def __init__(self, client_order_id: str, from_status: str, to_status: str) -> None:
        super().__init__(
            f"Illegal order state transition for {client_order_id}: {from_status} -> {to_status}",
            code="ILLEGAL_ORDER_TRANSITION",
            details={"client_order_id": client_order_id, "from_status": from_status, "to_status": to_status},
        )


class BrokerNotReadyError(QuantPlatformError):
    """Raised when attempting to trade on a broker before exchange state reconciliation completes."""

    def __init__(self, exchange: str) -> None:
        super().__init__(
            f"Broker for {exchange} is not ready: startup state reconciliation with exchange not completed",
            code="BROKER_NOT_READY",
            details={"exchange": exchange},
        )


class RiskRejectedError(QuantPlatformError):
    """Raised when pre-trade risk engine rejects an order."""

    def __init__(self, rule_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=rule_code, details=details)


class AuditImmutabilityError(QuantPlatformError):
    """Raised when attempting to mutate or delete append-only audit log entries."""

    def __init__(self) -> None:
        super().__init__(
            "Audit log entries are strictly append-only and cannot be updated or deleted.",
            code="AUDIT_IMMUTABLE",
        )


class ApprovalRequiredError(QuantPlatformError):
    """Raised when attempting to run an unapproved strategy version in restricted workflows."""

    def __init__(self, version_id: str, status: str) -> None:
        super().__init__(
            f"Strategy version {version_id} requires manual approval (current status: {status})",
            code="APPROVAL_REQUIRED",
            details={"version_id": version_id, "status": status},
        )
