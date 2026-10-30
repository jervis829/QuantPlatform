"""Core enumerations shared across Backtest, Shadow, and Live environments."""

from __future__ import annotations

from enum import StrEnum


class RunEnv(StrEnum):
    BACKTEST = "backtest"
    SHADOW = "shadow"
    LIVE = "live"


class ContractType(StrEnum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"
    DELIVERY = "DELIVERY"


class InstrumentStatus(StrEnum):
    TRADING = "TRADING"
    MAINTENANCE = "MAINTENANCE"
    DELISTED = "DELISTED"
    SETTLED = "SETTLED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    NET = "NET"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # Post-Only (Good-Till-Crossing)


class OrderStatus(StrEnum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class MarginMode(StrEnum):
    CASH = "CASH"
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"


class SignalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    KILLED = "KILLED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class StrategySourceType(StrEnum):
    UPLOAD = "upload"
    NL_GENERATED = "nl_generated"
    BUILTIN = "builtin"


class ReconciliationTier(StrEnum):
    EXCHANGE_VS_LOCAL = "exchange_vs_local"
    SHADOW_VS_LIVE = "shadow_vs_live"
    BACKTEST_VS_SHADOW = "backtest_vs_shadow"


class DiscrepancySeverity(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
