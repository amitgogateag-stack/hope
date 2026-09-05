from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


DEFAULT_DATABASE_URL = "postgresql+psycopg://hope:hope@localhost:5432/hope"


def database_url() -> str:
    """Return the configured database URL without ever providing a live broker URL."""
    return os.getenv("HOPE_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(url: str | None = None) -> Engine:
    """Create the SQLAlchemy engine; connection is established lazily."""
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaction boundary for application repositories."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
