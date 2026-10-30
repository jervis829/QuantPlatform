"""Container & Linux namespace isolated StrategySandbox implementation (P3 / Red Line 7)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

from quant_platform.core.interfaces import StrategySandbox
from quant_platform.exceptions import SandboxExecutionError
from quant_platform.strategy.ast_checker import ASTSecurityChecker


class ContainerStrategySandbox(StrategySandbox):
    """
    Isolated StrategySandbox supporting:
    1) Docker container isolation (`docker run --rm -i --network none --read-only --cpus ... --memory ...`)
       when Docker daemon & `quant-platform-sandbox:latest` image are available;
    2) Linux kernel namespace isolation (`unshare --user --net` + read-only cwd + RLIMIT_AS/CPU/FSIZE + SIGKILLpg)
       when running inside containerized environments without a nested Docker daemon.
    """

    def __init__(
        self,
        *,
        default_timeout_sec: float = 5.0,
        default_max_memory_mb: int = 256,
        docker_image: str = "quant-platform-sandbox:latest",
    ) -> None:
        self.default_timeout_sec = default_timeout_sec
        self.default_max_memory_mb = default_max_memory_mb
        self.docker_image = docker_image
        self._checker = ASTSecurityChecker()
        self.execution_log: list[dict[str, Any]] = []

    def validate_code(self, code: str) -> dict[str, Any]:
        violations = self._checker.check(code)
        return {"passed": len(violations) == 0, "violations": violations}

    def _has_docker_image(self) -> bool:
        if shutil.which("docker") is None or not Path("/var/run/docker.sock").exists():
            return False
        res = subprocess.run(
            ["docker", "image", "inspect", self.docker_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0

    def _build_command(self, timeout_sec: float, max_memory_mb: int) -> list[str]:
        if self._has_docker_image():
            return [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--cpus",
                "1.0",
                "--memory",
                f"{max_memory_mb}m",
                "--memory-swap",
                f"{max_memory_mb}m",
                "--pids-limit",
                "32",
                self.docker_image,
            ]

        repo_root = str(Path(__file__).resolve().parents[2])
        py_cmd = [sys.executable, "-m", "quant_platform.strategy.sandbox_worker"]
        if shutil.which("unshare") is not None:
            return ["unshare", "--user", "--net", *py_cmd]
        return py_cmd

    def execute_isolated(
        self,
        code: str,
        entrypoint_payload: dict[str, Any],
        timeout_sec: float | None = None,
        max_memory_mb: int | None = None,
    ) -> dict[str, Any]:
        self._checker.assert_safe(code)

        t_limit = timeout_sec if timeout_sec is not None else self.default_timeout_sec
        m_limit = max_memory_mb if max_memory_mb is not None else self.default_max_memory_mb
        cpu_sec = max(1, int(math.ceil(t_limit)) + 1)

        payload_str = json.dumps(
            {
                "code": code,
                "max_memory_mb": m_limit,
                "cpu_seconds": cpu_sec,
                **(entrypoint_payload or {}),
            }
        )

        repo_root = str(Path(__file__).resolve().parents[2])
        cmd = self._build_command(timeout_sec=t_limit, max_memory_mb=m_limit)
        env = {
            "PYTHONPATH": repo_root,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PATH": "/usr/bin:/bin",
        }

        ro_dir = tempfile.mkdtemp(prefix="quant_sbx_")
        os.chmod(ro_dir, 0o555)
        start_ns = time.monotonic_ns()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ro_dir,
                env=env,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(input=payload_str, timeout=t_limit)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2.0)
                elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
                rec = {
                    "ok": False,
                    "reason_code": "SANDBOX_TIMEOUT",
                    "elapsed_ms": elapsed_ms,
                    "timeout_sec": t_limit,
                }
                self.execution_log.append(rec)
                raise SandboxExecutionError(
                    "SANDBOX_TIMEOUT",
                    f"Strategy sandbox exceeded wall-clock timeout of {t_limit}s and was killed",
                    details=rec,
                ) from exc

            elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
            parsed: dict[str, Any] | None = None
            if stdout.strip():
                try:
                    parsed = json.loads(stdout.strip().splitlines()[-1])
                except json.JSONDecodeError:
                    parsed = None

            if proc.returncode == 0 and parsed and parsed.get("ok") is True:
                parsed["elapsed_ms"] = elapsed_ms
                self.execution_log.append({"ok": True, "reason_code": "OK", "elapsed_ms": elapsed_ms})
                return parsed

            # Classify OOM vs CPU vs runtime error
            err_code = (parsed or {}).get("error_code", "")
            combined_err = f"{stderr}\n{(parsed or {}).get('error', '')}"
            if (
                err_code == "SANDBOX_MEMORY_EXCEEDED"
                or proc.returncode in {137, -9, -6}
                or "memoryerror" in combined_err.lower()
                or "cannot allocate memory" in combined_err.lower()
            ):
                reason = "SANDBOX_MEMORY_EXCEEDED"
                msg = f"Strategy sandbox exceeded memory limit of {m_limit} MB"
            elif proc.returncode in {-24, 152}:  # SIGXCPU
                reason = "SANDBOX_TIMEOUT"
                msg = "Strategy sandbox exceeded CPU time limit"
            else:
                reason = err_code or "SANDBOX_RUNTIME_ERROR"
                msg = f"Strategy sandbox failed (rc={proc.returncode}): {combined_err.strip()[:300]}"

            rec = {
                "ok": False,
                "reason_code": reason,
                "returncode": proc.returncode,
                "elapsed_ms": elapsed_ms,
                "stderr": stderr[:300],
            }
            self.execution_log.append(rec)
            raise SandboxExecutionError(reason, msg, details=rec)
        finally:
            try:
                os.chmod(ro_dir, 0o700)
                shutil.rmtree(ro_dir, ignore_errors=True)
            except OSError:
                pass
