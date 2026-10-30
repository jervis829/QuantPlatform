"""Long-running background supervisor & scheduled reconciliation worker entrypoint (P12)."""

from __future__ import annotations

import os
import signal
import time

from quant_platform.db.session import build_engine, init_db
from quant_platform.observability.logging_setup import configure_structured_logging


def main() -> None:
    logger = configure_structured_logging(logger_name="quant_platform.worker")
    interval_sec = float(os.environ.get("QP_WORKER_HEARTBEAT_SEC", "15"))

    engine = build_engine()
    init_db(engine)
    logger.info("worker_started", extra={"interval_sec": interval_sec})

    running = True

    def _handle_stop(signum: int, _frame: object) -> None:
        nonlocal running
        logger.info("worker_stopping", extra={"signal": signum})
        running = False

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    while running:
        logger.info("worker_supervisor_heartbeat", extra={"status": "healthy"})
        time.sleep(interval_sec)

    logger.info("worker_stopped")


if __name__ == "__main__":
    main()
