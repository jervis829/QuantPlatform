"""Structured JSON logging with automatic secret redaction and RotatingFileHandler log rotation (P12)."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from typing import Any

_SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "api_secret",
        "secret",
        "passphrase",
        "password",
        "private_key",
        "authorization",
    }
)

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
)


def _redact_value(key: str, val: Any) -> Any:
    if key.lower() in _SENSITIVE_FIELD_NAMES:
        return "***REDACTED***"
    if isinstance(val, dict):
        return {k: _redact_value(str(k), v) for k, v in val.items()}
    return val


class StructuredJsonFormatter(logging.Formatter):
    """Formats every LogRecord as single-line UTF-8 JSON with UTC millisecond timestamp and secret masking."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts_ms": int(record.created * 1000) if record.created else int(time.time() * 1000),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STANDARD_RECORD_ATTRS and not k.startswith("_"):
                payload[k] = _redact_value(k, v)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_structured_logging(
    *,
    logger_name: str = "quant_platform",
    log_file: Path | str | None = None,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 5,
    level: int = logging.INFO,
) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = StructuredJsonFormatter()
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            filename=str(path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    else:
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)
    return logger
