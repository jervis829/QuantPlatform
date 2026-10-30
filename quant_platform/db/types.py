"""Custom SQLAlchemy fixed-point Decimal type strictly forbidding binary floats."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from quant_platform.core.decimal_utils import format_decimal, to_strict_decimal
from quant_platform.exceptions import FloatForbiddenError


class StrictDecimalColumn(TypeDecorator[Decimal]):
    """
    Fixed-point Decimal column:
    - PostgreSQL: native NUMERIC(38, 18)
    - SQLite: exact fixed-point string TEXT/VARCHAR(64) avoiding SQLite's IEEE-754 REAL affinity coercion
    - Runtime: rejects binary float on bind and always hydrates exact decimal.Decimal on read.
    """

    impl = Numeric(38, 18)
    cache_ok = True

    def __init__(self, precision: int = 38, scale: int = 18) -> None:
        super().__init__()
        self.precision = precision
        self.scale = scale

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(precision=self.precision, scale=self.scale, asdecimal=True))
        return dialect.type_descriptor(String(64))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (float, bool)):
            raise FloatForbiddenError("StrictDecimalColumn")
        dec = to_strict_decimal(value, "StrictDecimalColumn")
        if dialect.name == "postgresql":
            return dec
        return format_decimal(dec)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, float):
            raise FloatForbiddenError("StrictDecimalColumn_result")
        return Decimal(str(value))
