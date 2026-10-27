"""Strategy safe compilation, discovery, registry, and Pydantic schema extraction (P3)."""

from __future__ import annotations

import builtins
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from quant_platform.core.interfaces import Strategy
from quant_platform.strategy.ast_checker import ALLOWED_IMPORT_PREFIXES, ASTSecurityChecker

SAFE_BUILTIN_NAMES: tuple[str, ...] = (
    "abs",
    "all",
    "any",
    "bool",
    "bytearray",
    "bytes",
    "dict",
    "enumerate",
    "Exception",
    "filter",
    "float",
    "frozenset",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "pow",
    "print",
    "property",
    "range",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "ValueError",
    "KeyError",
    "RuntimeError",
    "ZeroDivisionError",
    "zip",
    "__build_class__",
)


def _restricted_import(
    name: str,
    globals_dict: dict[str, Any] | None = None,
    locals_dict: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    if level > 0 or name not in ALLOWED_IMPORT_PREFIXES:
        raise ImportError(f"Module '{name}' is blocked by strategy import whitelist")
    return builtins.__import__(name, globals_dict, locals_dict, fromlist, level)


def build_restricted_builtins() -> dict[str, Any]:
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTIN_NAMES if hasattr(builtins, k)}
    safe["__import__"] = _restricted_import
    return safe


def compile_strategy_class(code: str, checker: ASTSecurityChecker | None = None) -> type[Strategy]:
    """AST-validate and compile strategy code in a restricted builtin namespace, returning the Strategy subclass."""
    ast_checker = checker or ASTSecurityChecker()
    ast_checker.assert_safe(code)

    namespace: dict[str, Any] = {
        "__builtins__": build_restricted_builtins(),
        "__name__": "user_strategy_module",
    }
    compiled = compile(code, "<strategy>", "exec")
    exec(compiled, namespace, namespace)  # noqa: S102 - executed only after AST whitelist & safe builtins

    for obj in list(namespace.values()):
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            obj.model_rebuild(_types_namespace=namespace)

    candidates: list[type[Strategy]] = []
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            candidates.append(obj)
    if not candidates:
        raise ValueError("No Strategy subclass discovered after compiling strategy module")
    return candidates[-1]


def extract_param_schema_and_defaults(
    strategy_cls: type[Strategy],
    override_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Extract JSON Schema (for dynamic frontend form rendering) and validated default/merged parameters
    from Strategy.ParamModel (Pydantic v2 BaseModel).
    """
    param_model: type[BaseModel] | None = getattr(strategy_cls, "ParamModel", None)
    if param_model is None:
        return {"type": "object", "properties": {}}, dict(override_params or {})

    schema = param_model.model_json_schema()
    instance = param_model(**(override_params or {}))
    validated_json = instance.model_dump(mode="json")
    return schema, validated_json


class StrategyRegistry:
    """In-process registry of validated Strategy classes keyed by strategy_id."""

    def __init__(self) -> None:
        self._classes: dict[str, type[Strategy]] = {}
        self._checker = ASTSecurityChecker()

    def load_and_register(self, strategy_id: str, code: str) -> type[Strategy]:
        cls = compile_strategy_class(code, checker=self._checker)
        self._classes[strategy_id] = cls
        return cls

    def register_class(self, strategy_id: str, cls: type[Strategy]) -> None:
        if not issubclass(cls, Strategy):
            raise TypeError(f"{cls} must inherit from Strategy")
        self._classes[strategy_id] = cls

    def get(self, strategy_id: str) -> type[Strategy]:
        if strategy_id not in self._classes:
            raise KeyError(f"Strategy '{strategy_id}' is not registered")
        return self._classes[strategy_id]

    def list_registered(self) -> list[str]:
        return sorted(self._classes.keys())
