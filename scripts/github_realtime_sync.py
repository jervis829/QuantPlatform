#!/usr/bin/env python3
"""
Real-time GitHub -> Local Docker workspace synchronization daemon.

Continuously polls GitHub (`https://github.com/jervis829/QuantPlatform.git`) and
fast-forwards / updates the local checkout whenever new commits land on `main` or
active `arena/*` branches (when `GITHUB_SYNC_BRANCH=auto`), triggering automatic
strategy seeding when `scripts/seed_*.py` changes while preserving uncommitted local edits.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

DEFAULT_REPO_URL = "https://github.com/jervis829/QuantPlatform.git"


def _run_git(repo_dir: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=check,
    )


def ensure_safe_directory(repo_dir: Path) -> None:
    resolved = str(repo_dir.resolve())
    existing = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        text=True,
        capture_output=True,
        check=False,
    )
    safe_dirs = {line.strip() for line in existing.stdout.splitlines() if line.strip()}
    if resolved not in safe_dirs and "*" not in safe_dirs:
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", resolved],
            text=True,
            capture_output=True,
            check=False,
        )


def ensure_remote_url(repo_dir: Path, remote: str, repo_url: str) -> None:
    res = _run_git(repo_dir, ["remote", "get-url", remote], check=False)
    if res.returncode != 0:
        _run_git(repo_dir, ["remote", "add", remote, repo_url], check=True)
    elif repo_url and res.stdout.strip() != repo_url and not res.stdout.strip().startswith(("git@", "ssh://", "/")):
        # Keep SSH or local file remotes untouched; normalize anonymous HTTPS remote if needed
        _run_git(repo_dir, ["remote", "set-url", remote, repo_url], check=True)


def resolve_target_ref(repo_dir: Path, remote: str, branch_spec: str) -> tuple[str, str]:
    """Fetch remote refs and resolve (remote_ref, commit_sha) to track."""
    _run_git(repo_dir, ["fetch", "--prune", remote], check=True)

    if branch_spec and branch_spec.lower() != "auto":
        ref_full = f"refs/remotes/{remote}/{branch_spec}"
        rev = _run_git(repo_dir, ["rev-parse", ref_full], check=True)
        return f"{remote}/{branch_spec}", rev.stdout.strip()

    # Auto mode: pick the most recently committed ref across origin/main and origin/arena/*
    refs_out = _run_git(
        repo_dir,
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)|%(objectname)",
            f"refs/remotes/{remote}/main",
            f"refs/remotes/{remote}/arena",
            f"refs/remotes/{remote}/arena/*",
        ],
        check=True,
    ).stdout.strip()

    if not refs_out:
        refs_out = _run_git(
            repo_dir,
            [
                "for-each-ref",
                "--sort=-committerdate",
                "--format=%(refname:short)|%(objectname)",
                f"refs/remotes/{remote}",
            ],
            check=True,
        ).stdout.strip()

    for line in refs_out.splitlines():
        if "|" not in line:
            continue
        ref_short, sha = line.split("|", 1)
        if ref_short.endswith("/HEAD"):
            continue
        return ref_short.strip(), sha.strip()

    raise RuntimeError(f"No remote branches found on remote '{remote}'")


def has_dirty_tracked_files(repo_dir: Path) -> bool:
    status = _run_git(repo_dir, ["status", "--porcelain", "--untracked-files=no"], check=True)
    return bool(status.stdout.strip())


def _write_status_file(repo_dir: Path, payload: dict[str, Any]) -> None:
    git_dir = repo_dir / ".git"
    if git_dir.is_dir():
        status_path = git_dir / "quant_sync_status.json"
        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_once(
    repo_dir: Path,
    *,
    remote: str = "origin",
    repo_url: str = DEFAULT_REPO_URL,
    branch_spec: str = "auto",
    force_clean: bool = False,
    run_seed_on_change: bool = True,
) -> dict[str, Any]:
    ensure_safe_directory(repo_dir)
    if repo_url:
        ensure_remote_url(repo_dir, remote, repo_url)

    head_res = _run_git(repo_dir, ["rev-parse", "HEAD"], check=False)
    old_sha = head_res.stdout.strip() if head_res.returncode == 0 else ""
    target_ref, target_sha = resolve_target_ref(repo_dir, remote, branch_spec)
    now_iso = datetime.now(timezone.utc).isoformat()

    if old_sha == target_sha:
        result = {
            "ts_utc": now_iso,
            "updated": False,
            "skipped_dirty": False,
            "target_ref": target_ref,
            "old_sha": old_sha,
            "new_sha": target_sha,
            "changed_files": [],
            "needs_image_rebuild": False,
        }
        _write_status_file(repo_dir, result)
        return result

    if old_sha and has_dirty_tracked_files(repo_dir) and not force_clean:
        result = {
            "ts_utc": now_iso,
            "updated": False,
            "skipped_dirty": True,
            "target_ref": target_ref,
            "old_sha": old_sha,
            "new_sha": target_sha,
            "changed_files": [],
            "needs_image_rebuild": False,
            "warning": "Local tracked modifications detected; skipping overwrite (set GITHUB_SYNC_FORCE_CLEAN=true to force).",
        }
        _write_status_file(repo_dir, result)
        return result

    if old_sha:
        diff_out = _run_git(repo_dir, ["diff", "--name-only", old_sha, target_sha], check=True).stdout
    else:
        diff_out = _run_git(repo_dir, ["ls-tree", "-r", "--name-only", target_sha], check=True).stdout
    changed_files = [f.strip() for f in diff_out.splitlines() if f.strip()]

    local_branch = target_ref.split("/", 1)[1] if "/" in target_ref else target_ref
    if force_clean and old_sha:
        _run_git(repo_dir, ["reset", "--hard"], check=True)
    _run_git(repo_dir, ["checkout", "-B", local_branch, target_sha], check=True)

    commit_subject = _run_git(repo_dir, ["log", "-1", "--pretty=%s", target_sha], check=True).stdout.strip()
    rebuild_triggers = {
        "pyproject.toml",
        "Dockerfile.api",
        "Dockerfile.ui",
        "Dockerfile.sandbox",
        "docker-compose.yml",
        "docker-compose.sync.yml",
        "frontend/package.json",
        "frontend/package-lock.json",
    }
    needs_rebuild = any(f in rebuild_triggers for f in changed_files)
    seed_changed = any(f.startswith("scripts/seed_") for f in changed_files)

    seed_ran = False
    if run_seed_on_change and seed_changed:
        seed_script = repo_dir / "scripts" / "seed_delphi_strategies.py"
        if seed_script.is_file():
            proc = subprocess.run(
                [sys.executable, str(seed_script)],
                cwd=str(repo_dir),
                text=True,
                capture_output=True,
                check=False,
            )
            seed_ran = proc.returncode == 0

    result = {
        "ts_utc": now_iso,
        "updated": True,
        "skipped_dirty": False,
        "target_ref": target_ref,
        "branch": local_branch,
        "old_sha": old_sha,
        "new_sha": target_sha,
        "commit_subject": commit_subject,
        "changed_files": changed_files,
        "needs_image_rebuild": needs_rebuild,
        "seed_ran": seed_ran,
    }
    _write_status_file(repo_dir, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-time GitHub -> Docker workspace sync")
    parser.add_argument("--repo-dir", default=os.environ.get("GITHUB_SYNC_REPO_DIR", "."))
    parser.add_argument("--remote", default=os.environ.get("GITHUB_SYNC_REMOTE", "origin"))
    parser.add_argument("--repo-url", default=os.environ.get("GITHUB_REPO_URL", DEFAULT_REPO_URL))
    parser.add_argument("--branch", default=os.environ.get("GITHUB_SYNC_BRANCH", "auto"))
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("GITHUB_SYNC_INTERVAL_SEC", "15")),
    )
    parser.add_argument(
        "--force-clean",
        action="store_true",
        default=os.environ.get("GITHUB_SYNC_FORCE_CLEAN", "false").lower() in ("1", "true", "yes"),
    )
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run a single sync check and exit")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    running = True

    def _stop(_sig: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(
        json.dumps(
            {
                "event": "github_sync_started",
                "repo_dir": str(repo_dir),
                "branch_mode": args.branch,
                "interval_sec": args.interval,
                "once": args.once,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    while running:
        try:
            res = sync_once(
                repo_dir,
                remote=args.remote,
                repo_url=args.repo_url,
                branch_spec=args.branch,
                force_clean=args.force_clean,
                run_seed_on_change=not args.no_seed,
            )
            if res.get("updated") or res.get("skipped_dirty") or args.once:
                print(json.dumps(res, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "github_sync_error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "ts_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if args.once:
                return 1

        if args.once:
            break
        sleep_rem = max(3.0, args.interval)
        step = 0.5
        elapsed = 0.0
        while running and elapsed < sleep_rem:
            time.sleep(min(step, sleep_rem - elapsed))
            elapsed += step

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
