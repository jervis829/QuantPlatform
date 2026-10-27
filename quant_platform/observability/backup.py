"""Atomic hot database backup & restore verification with SHA-256 manifests (P12)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time


@dataclass(frozen=True)
class BackupManifest:
    backup_id: str
    backup_file: Path
    manifest_file: Path
    sha256_hex: str
    size_bytes: int
    created_ts_ms: int
    engine_type: str


class DatabaseBackupManager:
    """
    Provides online hot backup and verified restore:
    - SQLite: uses `sqlite3.Connection.backup(...)` for transactionally consistent hot snapshot;
    - PostgreSQL: builds canonical `pg_dump --format=custom` / `pg_restore` command pipelines;
    - Writes SHA-256 manifest JSON alongside backup file and enforces hash verification before restore.
    """

    def __init__(self, backup_dir: Path | str) -> None:
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                digest.update(chunk)
        return digest.hexdigest()

    def create_sqlite_backup(self, source_db_path: Path | str, *, label: str = "manual") -> BackupManifest:
        src = Path(source_db_path)
        if not src.exists():
            raise FileNotFoundError(f"Source SQLite database not found: {src}")
        ts_ms = int(time.time() * 1000)
        backup_id = f"bkp_{label}_{ts_ms}"
        dest_file = self._backup_dir / f"{backup_id}.sqlite3"
        manifest_file = self._backup_dir / f"{backup_id}.manifest.json"

        with sqlite3.connect(str(src)) as src_conn, sqlite3.connect(str(dest_file)) as dst_conn:
            src_conn.backup(dst_conn)

        sha = self._compute_sha256(dest_file)
        size_bytes = dest_file.stat().st_size
        manifest_payload = {
            "backup_id": backup_id,
            "backup_file": str(dest_file),
            "sha256_hex": sha,
            "size_bytes": size_bytes,
            "created_ts_ms": ts_ms,
            "engine_type": "sqlite",
        }
        manifest_file.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")
        return BackupManifest(
            backup_id=backup_id,
            backup_file=dest_file,
            manifest_file=manifest_file,
            sha256_hex=sha,
            size_bytes=size_bytes,
            created_ts_ms=ts_ms,
            engine_type="sqlite",
        )

    def verify_backup_manifest(self, manifest: BackupManifest) -> bool:
        if not manifest.backup_file.exists() or not manifest.manifest_file.exists():
            return False
        actual_sha = self._compute_sha256(manifest.backup_file)
        actual_size = manifest.backup_file.stat().st_size
        return actual_sha == manifest.sha256_hex and actual_size == manifest.size_bytes

    def restore_sqlite_backup(self, manifest: BackupManifest, *, target_db_path: Path | str) -> Path:
        if not self.verify_backup_manifest(manifest):
            raise ValueError(f"Backup integrity check failed for {manifest.backup_id}")
        target = Path(target_db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        with sqlite3.connect(str(manifest.backup_file)) as bkp_conn, sqlite3.connect(str(target)) as dst_conn:
            bkp_conn.backup(dst_conn)
        return target

    @staticmethod
    def build_postgres_pg_dump_cmd(dsn: str, output_file: Path | str) -> list[str]:
        return [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={output_file}",
            dsn,
        ]

    @staticmethod
    def build_postgres_pg_restore_cmd(dsn: str, backup_file: Path | str) -> list[str]:
        return [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            f"--dbname={dsn}",
            str(backup_file),
        ]
