from __future__ import annotations

import logging
import os
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _resolve_and_prepare_db_url(url: str) -> str:
    """Ensure directory for file-based SQLite exists; fallback gracefully if path is unwritable."""
    if url.startswith("sqlite") and not url.startswith("sqlite:///:memory:") and url != "sqlite://":
        # Extract path component after sqlite:///
        path_part = url.split("sqlite:///")[-1]
        if path_part:
            # Handle absolute or relative paths
            parent_dir = os.path.dirname(os.path.abspath(path_part))
            if parent_dir:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except (PermissionError, OSError) as exc:
                    logger.warning(
                        "Cannot create database parent directory '%s' (%s). Falling back to local './data' directory.",
                        parent_dir,
                        exc,
                    )
                    local_dir = os.path.abspath("./data")
                    os.makedirs(local_dir, exist_ok=True)
                    return f"sqlite:///{os.path.join(local_dir, 'app.db')}"
    return url


def _get_engine(db_url: str | None = None):
    raw_url = db_url or settings.DATABASE_URL
    final_url = _resolve_and_prepare_db_url(raw_url)

    connect_args = {}
    if final_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine_instance = create_engine(
        final_url,
        connect_args=connect_args,
        echo=False,
    )

    # Enable WAL mode and busy_timeout for SQLite to improve concurrent performance
    if final_url.startswith("sqlite"):
        @event.listens_for(engine_instance, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            except Exception as e:
                logger.warning("Could not set SQLite pragmas: %s", e)
            finally:
                cursor.close()

    return engine_instance


engine = _get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables() -> None:
    """Create all tables (called at startup)."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
