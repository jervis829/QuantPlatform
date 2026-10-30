"""Database engine, session factory, and append-only audit log helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from quant_platform.config import get_settings
from quant_platform.core.hashing import canonical_json, sha256_hex
from quant_platform.db.schema import AuditLogRecord, Base


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, connect_args=connect_args, **kwargs)
    if url.startswith("sqlite") and ":memory:" not in url:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: Any, _conn_record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA busy_timeout=30000;")
            cur.close()
    return engine


def install_audit_immutability_triggers(engine: Engine) -> None:
    """Install database-level triggers blocking UPDATE and DELETE on audit_logs."""
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
                    BEFORE UPDATE ON audit_logs
                    BEGIN
                        SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE: audit_logs cannot be updated');
                    END;
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
                    BEFORE DELETE ON audit_logs
                    BEGIN
                        SELECT RAISE(ABORT, 'AUDIT_IMMUTABLE: audit_logs cannot be deleted');
                    END;
                    """
                )
            )
        elif dialect == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION forbid_audit_mutation()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'AUDIT_IMMUTABLE: audit_logs is append-only';
                    END;
                    $$ LANGUAGE plpgsql;
                    """
                )
            )
            conn.execute(
                text(
                    """
                    DROP TRIGGER IF EXISTS trg_audit_logs_immutable ON audit_logs;
                    CREATE TRIGGER trg_audit_logs_immutable
                    BEFORE UPDATE OR DELETE ON audit_logs
                    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();
                    """
                )
            )


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    install_audit_immutability_triggers(engine)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def append_audit_log(
    session: Session,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> AuditLogRecord:
    """Append a hash-chained audit record (Red Line 8)."""
    last_row = session.execute(
        select(AuditLogRecord).order_by(AuditLogRecord.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = last_row.entry_hash if last_row is not None else "GENESIS"
    entry_hash = sha256_hex(
        canonical_json(
            {
                "prev_hash": prev_hash,
                "actor": actor,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "payload": payload,
                "ts_ms": ts_ms,
            }
        )
    )
    record = AuditLogRecord(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        payload_json=payload,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        ts_ms=ts_ms,
    )
    session.add(record)
    session.flush()
    return record
