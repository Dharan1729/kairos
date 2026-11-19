"""Engine and session setup.

Synchronous SQLAlchemy: the relational layer is not the bottleneck
(embedding is), and sync code keeps the tests simple. ``configure()`` can be
called again to point at another database (tests use one SQLite file each).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from kairos.config import load_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def configure(database_url: str | None = None):
    global _engine, _SessionLocal
    database_url = database_url or load_settings().database_url
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        db_path = database_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(database_url, connect_args=connect_args)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_engine():
    if _engine is None:
        configure()
    return _engine


def get_sessionmaker():
    if _SessionLocal is None:
        configure()
    return _SessionLocal


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def create_all():
    from kairos import directory, models  # noqa: F401  (register tables on Base.metadata)

    Base.metadata.create_all(bind=get_engine())
