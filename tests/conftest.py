"""Shared pytest fixtures across all stages."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from quant_platform.db import build_engine, build_session_factory, init_db


@pytest.fixture()
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    db_file = tmp_path / "test_quant.sqlite3"
    engine = build_engine(f"sqlite:///{db_file}")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(db_engine)


@pytest.fixture()
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = db_session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()
