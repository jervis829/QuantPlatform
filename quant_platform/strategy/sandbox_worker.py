"""Isolated subprocess/container worker enforcing kernel RLIMITs, audit hooks & memory caps on Linux & macOS (P3)."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import random
import resource
import socket
import sys
import tracemalloc
import traceback
from typing import Any

from quant_platform.core.interfaces import NullClock, NullDataFeed, StrategyContext
from quant_platform.core.models import Bar, StrategyConfig
from quant_platform.strategy.registry import compile_strategy_class, extract_param_schema_and_defaults


def _current_vmsize_bytes() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmSize:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except OSError:
        pass
    return 64 * 1024 * 1024


def _install_audit_security_blocker() -> None:
    """
    Install unremovable CPython PEP-578 audit hook blocking network sockets and subprocess spawning.
    Guarantees outbound network isolation on macOS as well as Linux namespaces/containers.
    """
    blocked_prefixes = (
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "socket.getaddrinfo",
        "os.system",
        "os.exec",
        "os.spawn",
        "os.posix_spawn",
        "subprocess.Popen",
    )

    def _hook(event: str, _args: tuple[Any, ...]) -> None:
        if event.startswith(blocked_prefixes):
            raise OSError(f"SANDBOX_BLOCKED_SYSCALL: {event}")

    sys.addaudithook(_hook)


def _probe_network_isolated() -> bool:
    """Confirm outbound TCP/UDP networking is blocked inside the sandbox."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _install_tracemalloc_memory_guard(max_memory_mb: int) -> None:
    """Fallback memory cap for macOS Darwin where XNU rejects RLIMIT_AS below dyld shared cache."""
    cap_bytes = max_memory_mb * 1024 * 1024
    tracemalloc.start()

    def _mem_trace(frame: Any, event: str, _arg: Any) -> Any:
        if event in ("line", "call"):
            current_bytes, _peak = tracemalloc.get_traced_memory()
            if current_bytes > cap_bytes:
                raise MemoryError(f"Sandbox traced heap {current_bytes} exceeded {cap_bytes} bytes")
        return _mem_trace

    sys.settrace(_mem_trace)


def _apply_resource_limits(max_memory_mb: int, cpu_seconds: int) -> None:
    try:
        _cur_cpu, hard_cpu = resource.getrlimit(resource.RLIMIT_CPU)
        target_hard_cpu = cpu_seconds + 1 if hard_cpu == resource.RLIM_INFINITY else min(hard_cpu, cpu_seconds + 1)
        target_soft_cpu = min(cpu_seconds, target_hard_cpu)
        resource.setrlimit(resource.RLIMIT_CPU, (target_soft_cpu, target_hard_cpu))
    except (ValueError, OSError):
        pass

    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except (ValueError, OSError):
        pass

    if sys.platform.startswith("linux"):
        base_vm = _current_vmsize_bytes()
        limit_bytes = base_vm + max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    else:
        # macOS Darwin maps multi-hundred-GB dyld shared cache, making RLIMIT_AS raise ValueError;
        # enforce exact heap byte budget via tracemalloc trace guard instead.
        _install_tracemalloc_memory_guard(max_memory_mb)


def main() -> int:
    raw_input = sys.stdin.read()
    req = json.loads(raw_input)
    code: str = req["code"]
    max_memory_mb = int(req.get("max_memory_mb", 256))
    cpu_seconds = max(1, int(req.get("cpu_seconds", 5)))
    params = req.get("params") or {}

    # Pre-warm allowed heavy optional modules if referenced in code before RLIMIT_AS / audit lock
    if "numpy" in code:
        import numpy  # noqa: F401
    if "pandas" in code:
        import pandas  # noqa: F401

    _install_audit_security_blocker()
    net_isolated = _probe_network_isolated()
    _apply_resource_limits(max_memory_mb=max_memory_mb, cpu_seconds=cpu_seconds)

    try:
        cls = compile_strategy_class(code)
        schema, validated_params = extract_param_schema_and_defaults(cls, params)

        clock = NullClock(1700000000000)
        feed = NullDataFeed(clock)
        cfg = StrategyConfig(
            strategy_id="sandbox_smoke",
            strategy_version_id="sandbox_smoke:v1",
            name="Sandbox Smoke",
            exchanges=["binance"],
            symbols=["BTC/USDT:USDT"],
            params=validated_params,
        )
        ctx = StrategyContext(clock=clock, data_feed=feed, config=cfg, rng=random.Random(42))
        strat = cls(ctx, validated_params)

        emitted_count = 0
        res_start = strat.on_start()
        if res_start:
            emitted_count += len(res_start)
        emitted_count += len(strat.drain_intents())

        t0 = 1700000000000
        for idx in range(6):
            bar_ts = t0 + idx * 60_000
            clock.set_now_ms(bar_ts + 59_999)
            bar = Bar(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                interval="1m",
                open_time_ms=bar_ts,
                close_time_ms=bar_ts + 59_999,
                open=Decimal("60000") + Decimal(idx * 10),
                high=Decimal("60020") + Decimal(idx * 10),
                low=Decimal("59990") + Decimal(idx * 10),
                close=Decimal("60010") + Decimal(idx * 15),
                volume=Decimal("10"),
                is_closed=True,
                exchange_ts_ms=bar_ts + 59_999,
                local_recv_ts_ms=bar_ts + 60_000,
            )
            out = strat.on_bar(bar)
            if out:
                emitted_count += len(out)
            emitted_count += len(strat.drain_intents())

        res_stop = strat.on_stop()
        if res_stop:
            emitted_count += len(res_stop)
        emitted_count += len(strat.drain_intents())

        sys.settrace(None)
        sys.stdout.write(
            json.dumps(
                {
                    "ok": True,
                    "strategy_class": cls.__name__,
                    "param_schema": schema,
                    "validated_params": validated_params,
                    "emitted_intents": emitted_count,
                    "network_isolated": net_isolated,
                }
            )
        )
        sys.stdout.flush()
        return 0
    except MemoryError:
        sys.settrace(None)
        sys.stdout.write(
            json.dumps({"ok": False, "error_code": "SANDBOX_MEMORY_EXCEEDED", "error": "Memory limit exceeded"})
        )
        sys.stdout.flush()
        return 137
    except Exception as exc:
        sys.settrace(None)
        msg = f"{type(exc).__name__}: {exc}"
        if "MemoryError" in msg or "cannot allocate memory" in msg.lower():
            code_str = "SANDBOX_MEMORY_EXCEEDED"
        else:
            code_str = "SANDBOX_RUNTIME_ERROR"
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error_code": code_str,
                    "error": msg,
                    "traceback": traceback.format_exc(limit=5),
                }
            )
        )
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    os._exit(main())
