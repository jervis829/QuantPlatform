"""Standardized declarative UI Schema Metadata helper for Strategy Pydantic ParamModels."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

UIGroupLiteral = Literal["signal", "risk_sizing", "general"]
UIDangerLiteral = Literal["safe", "caution", "high"]


def ui_param(
    default: Any = ...,
    *,
    ui_group: UIGroupLiteral = "general",
    unit: str = "",
    danger_level: UIDangerLiteral = "safe",
    danger_hint: str = "",
    title: str | None = None,
    description: str | None = None,
    ge: int | float | None = None,
    gt: int | float | None = None,
    le: int | float | None = None,
    lt: int | float | None = None,
) -> Any:
    """
    Declarative Pydantic Field wrapper attaching standardized UI schema metadata (`json_schema_extra`)
    so the frontend SchemaParamEditor reads explicit group, unit, and danger level annotations
    rather than relying solely on field-name heuristics.
    """
    extra: dict[str, Any] = {
        "ui_group": ui_group,
        "unit": unit,
        "danger_level": danger_level,
    }
    if danger_hint:
        extra["danger_hint"] = danger_hint

    kwargs: dict[str, Any] = {"json_schema_extra": extra}
    if title is not None:
        kwargs["title"] = title
    if description is not None:
        kwargs["description"] = description
    if ge is not None:
        kwargs["ge"] = ge
    if gt is not None:
        kwargs["gt"] = gt
    if le is not None:
        kwargs["le"] = le
    if lt is not None:
        kwargs["lt"] = lt

    return Field(default, **kwargs)
