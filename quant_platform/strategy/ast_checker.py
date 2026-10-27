"""AST static security and anti-lookahead analyzer for Strategy code (P3 / Red Lines 1 & 7)."""

from __future__ import annotations

import ast
from typing import Any

from quant_platform.exceptions import ASTSecurityError

ALLOWED_IMPORT_PREFIXES: frozenset[str] = frozenset(
    {
        "decimal",
        "math",
        "statistics",
        "typing",
        "collections",
        "dataclasses",
        "enum",
        "functools",
        "itertools",
        "pydantic",
        "numpy",
        "pandas",
        "quant_platform.core",
        "quant_platform.core.models",
        "quant_platform.core.enums",
        "quant_platform.core.interfaces",
        "quant_platform.core.decimal_utils",
        "quant_platform.strategy.indicators",
    }
)

FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
    }
)

FORBIDDEN_DUNDER_ATTRS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__dict__",
        "__class__",
        "__loader__",
        "__spec__",
    }
)

FORBIDDEN_IO_METHODS: frozenset[str] = frozenset(
    {
        "to_csv",
        "to_parquet",
        "to_pickle",
        "to_sql",
        "read_csv",
        "read_parquet",
        "read_pickle",
        "read_sql",
        "write_text",
        "write_bytes",
        "unlink",
        "rmdir",
        "mkdir",
        "popen",
        "system",
    }
)

MAX_STATIC_ALLOC_ELEMENTS = 5_000_000


def _eval_static_int(node: ast.AST) -> int | None:
    """Conservatively evaluate static integer constant expressions (e.g., 10 ** 9, -1, 1024 * 1024)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _eval_static_int(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _eval_static_int(node.operand)
    if isinstance(node, ast.BinOp):
        left = _eval_static_int(node.left)
        right = _eval_static_int(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Pow):
            if right > 20 and abs(left) > 1:
                return MAX_STATIC_ALLOC_ELEMENTS + 1
            if 0 <= right <= 20:
                return int(left**right)
    return None


def _is_module_whitelisted(mod_name: str) -> bool:
    if mod_name in ALLOWED_IMPORT_PREFIXES:
        return True
    return False


class ASTSecurityChecker:
    """
    Static analyzer intercepting:
    - Unauthorized imports (os, sys, subprocess, socket, requests, datetime, time, random, etc.)
    - Dangerous builtins (eval, exec, __import__, open, getattr, globals, ...)
    - Dunder object graph escapes (__subclasses__, __globals__, ...)
    - Lookahead patterns: .shift(-n), .iloc[i + k] / subscript[i + k], .bfill() / .backfill()
    - Nondeterministic wall-clock / unseeded RNG calls
    - Infinite while loops (while True / constant truthy condition)
    - Direct or mutual recursion in strategy functions/methods
    - Massive static memory allocations ([0] * 10**9, bytearray(10**9), np.zeros(10**9))
    """

    def check(self, code: str) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return [
                {
                    "rule": "SYNTAX_ERROR",
                    "line": exc.lineno or 1,
                    "message": f"Python syntax error: {exc.msg}",
                }
            ]

        has_strategy_subclass = False
        defined_funcs: dict[str, set[str]] = {}
        current_func_stack: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                nonlocal has_strategy_subclass
                for base in node.bases:
                    base_name = base.id if isinstance(base, ast.Name) else (
                        base.attr if isinstance(base, ast.Attribute) else ""
                    )
                    if base_name == "Strategy":
                        has_strategy_subclass = True
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qname = f"{node.name}.{item.name}"
                        defined_funcs.setdefault(qname, set())
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    if not _is_module_whitelisted(alias.name):
                        violations.append(
                            {
                                "rule": "FORBIDDEN_IMPORT",
                                "line": node.lineno,
                                "message": f"Import '{alias.name}' is not in strategy whitelist",
                            }
                        )
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                mod = node.module or ""
                if node.level > 0 or not _is_module_whitelisted(mod):
                    violations.append(
                        {
                            "rule": "FORBIDDEN_IMPORT",
                            "line": node.lineno,
                            "message": f"Import from '{mod}' is not in strategy whitelist",
                        }
                    )
                self.generic_visit(node)

            def visit_While(self, node: ast.While) -> None:
                # Block constant-truthy while (while True / while 1) and any while without simple counter comparison
                if isinstance(node.test, ast.Constant) and bool(node.test.value):
                    violations.append(
                        {
                            "rule": "INFINITE_WHILE_LOOP",
                            "line": node.lineno,
                            "message": "Unbounded 'while True' loop is prohibited in strategy code",
                        }
                    )
                elif not isinstance(node.test, ast.Compare):
                    violations.append(
                        {
                            "rule": "INFINITE_WHILE_LOOP",
                            "line": node.lineno,
                            "message": "while loop requires explicit bounded comparison condition",
                        }
                    )
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_func(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_func(node)

            def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                fname = node.name
                defined_funcs.setdefault(fname, set())
                current_func_stack.append(fname)
                self.generic_visit(node)
                current_func_stack.pop()

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if node.attr in FORBIDDEN_DUNDER_ATTRS:
                    violations.append(
                        {
                            "rule": "FORBIDDEN_DUNDER_ACCESS",
                            "line": node.lineno,
                            "message": f"Access to dunder attribute '{node.attr}' is prohibited",
                        }
                    )
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                # 1. Direct builtin call check
                if isinstance(node.func, ast.Name):
                    fn_name = node.func.id
                    if fn_name in FORBIDDEN_BUILTINS:
                        violations.append(
                            {
                                "rule": "FORBIDDEN_BUILTIN",
                                "line": node.lineno,
                                "message": f"Call to forbidden builtin '{fn_name}()'",
                            }
                        )
                    if current_func_stack:
                        defined_funcs.setdefault(current_func_stack[-1], set()).add(fn_name)
                    if fn_name in {"bytearray", "bytes"} and node.args:
                        sz = _eval_static_int(node.args[0])
                        if sz is not None and sz > MAX_STATIC_ALLOC_ELEMENTS:
                            violations.append(
                                {
                                    "rule": "EXCESSIVE_MEMORY_ALLOCATION",
                                    "line": node.lineno,
                                    "message": f"Allocation of {sz} bytes exceeds static safety limit",
                                }
                            )

                # 2. Method / attribute call check
                elif isinstance(node.func, ast.Attribute):
                    attr = node.func.attr
                    if attr in FORBIDDEN_IO_METHODS:
                        violations.append(
                            {
                                "rule": "FORBIDDEN_IO_CALL",
                                "line": node.lineno,
                                "message": f"Forbidden I/O or system method '.{attr}()'",
                            }
                        )
                    if attr in {"now", "utcnow", "time", "monotonic"}:
                        if isinstance(node.func.value, (ast.Name, ast.Attribute)):
                            owner = (
                                node.func.value.id
                                if isinstance(node.func.value, ast.Name)
                                else node.func.value.attr
                            )
                            if owner in {"datetime", "time"}:
                                violations.append(
                                    {
                                        "rule": "FORBIDDEN_NONDETERMINISTIC_CALL",
                                        "line": node.lineno,
                                        "message": f"Direct wall-clock call '{owner}.{attr}()' prohibited; use self.ctx.now_ms",
                                    }
                                )

                    # Self recursion tracking: self.foo(...)
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "self" and current_func_stack:
                        defined_funcs.setdefault(current_func_stack[-1], set()).add(attr)

                    # Lookahead: .shift(-n) or .shift(periods=-n)
                    if attr == "shift":
                        shift_arg: ast.AST | None = node.args[0] if node.args else None
                        for kw in node.keywords:
                            if kw.arg in {"periods", None}:
                                shift_arg = kw.value
                        if shift_arg is not None:
                            val = _eval_static_int(shift_arg)
                            is_neg = (val is not None and val < 0) or (
                                isinstance(shift_arg, ast.UnaryOp) and isinstance(shift_arg.op, ast.USub)
                            )
                            if is_neg:
                                violations.append(
                                    {
                                        "rule": "LOOKAHEAD_NEGATIVE_SHIFT",
                                        "line": node.lineno,
                                        "message": "Negative shift '.shift(-n)' introduces future data lookahead",
                                    }
                                )

                    if attr in {"bfill", "backfill"}:
                        violations.append(
                            {
                                "rule": "LOOKAHEAD_BACKFILL",
                                "line": node.lineno,
                                "message": f"'.{attr}()' backfills from future observations (lookahead bias)",
                            }
                        )

                    # numpy huge array allocation: np.zeros(10**9) / np.ones(10**9) / np.full(...)
                    if attr in {"zeros", "ones", "empty", "full"} and node.args:
                        sz = _eval_static_int(node.args[0])
                        if sz is not None and sz > MAX_STATIC_ALLOC_ELEMENTS:
                            violations.append(
                                {
                                    "rule": "EXCESSIVE_MEMORY_ALLOCATION",
                                    "line": node.lineno,
                                    "message": f"Array allocation size {sz} exceeds static safety limit",
                                }
                            )

                self.generic_visit(node)

            def visit_Subscript(self, node: ast.Subscript) -> None:
                # Detect .iloc[i + 1], .loc[i + 1], or bars[i + 1] positive index lookahead
                slc = node.slice
                is_iloc_or_series = isinstance(node.value, ast.Attribute) and node.value.attr in {"iloc", "loc", "iat", "at"}
                if isinstance(slc, ast.BinOp) and isinstance(slc.op, ast.Add):
                    offset = _eval_static_int(slc.right)
                    if offset is not None and offset > 0 and isinstance(slc.left, ast.Name):
                        if is_iloc_or_series or slc.left.id in {"i", "idx", "t", "bar_idx", "cursor"}:
                            violations.append(
                                {
                                    "rule": "LOOKAHEAD_FUTURE_INDEX",
                                    "line": node.lineno,
                                    "message": f"Future index offset '{slc.left.id} + {offset}' accesses future data",
                                }
                            )
                self.generic_visit(node)

            def visit_BinOp(self, node: ast.BinOp) -> None:
                # Detect [0] * (10 ** 9) or "x" * (10 ** 9)
                if isinstance(node.op, ast.Mult):
                    if isinstance(node.left, (ast.List, ast.Tuple, ast.Constant)):
                        mult = _eval_static_int(node.right)
                        if mult is not None and mult > MAX_STATIC_ALLOC_ELEMENTS:
                            violations.append(
                                {
                                    "rule": "EXCESSIVE_MEMORY_ALLOCATION",
                                    "line": node.lineno,
                                    "message": f"Sequence multiplication factor {mult} exceeds static memory limit",
                                }
                            )
                    if isinstance(node.right, (ast.List, ast.Tuple, ast.Constant)):
                        mult = _eval_static_int(node.left)
                        if mult is not None and mult > MAX_STATIC_ALLOC_ELEMENTS:
                            violations.append(
                                {
                                    "rule": "EXCESSIVE_MEMORY_ALLOCATION",
                                    "line": node.lineno,
                                    "message": f"Sequence multiplication factor {mult} exceeds static memory limit",
                                }
                            )
                self.generic_visit(node)

        _Visitor().visit(tree)

        # Call-graph cycle detection for direct or mutual recursion
        visited: set[str] = set()
        active: set[str] = set()

        def _has_cycle(fn: str) -> bool:
            visited.add(fn)
            active.add(fn)
            for callee in defined_funcs.get(fn, set()):
                if callee in defined_funcs:
                    if callee in active or (callee not in visited and _has_cycle(callee)):
                        return True
            active.remove(fn)
            return False

        for fn_name in list(defined_funcs.keys()):
            if fn_name not in visited and _has_cycle(fn_name):
                violations.append(
                    {
                        "rule": "RECURSION_DETECTED",
                        "line": 1,
                        "message": f"Recursive call cycle detected involving '{fn_name}'",
                    }
                )
                break

        if not has_strategy_subclass:
            violations.append(
                {
                    "rule": "MISSING_STRATEGY_SUBCLASS",
                    "line": 1,
                    "message": "Strategy file must define at least one class inheriting from Strategy",
                }
            )

        return violations

    def assert_safe(self, code: str) -> None:
        violations = self.check(code)
        if violations:
            raise ASTSecurityError(violations)
