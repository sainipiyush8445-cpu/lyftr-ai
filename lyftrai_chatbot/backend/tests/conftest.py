from __future__ import annotations

"""pytest configuration and shared fixtures."""

import os
import sys
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure environment vars are set BEFORE importing any app modules
# Using pure in-memory SQLite with StaticPool prevents any test.db on disk
os.environ["GEMINI_API_KEY_1"] = "test-key-1"
os.environ["GEMINI_API_KEY_2"] = "test-key-2"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import app.database as app_db
from app.database import Base, get_db
from app.main import app
from app.services.cache_service import initialize_cache

# ---------------------------------------------------------------------------
# In-memory SQLite with StaticPool for tests
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Bind the global database engine to test_engine to ensure total isolation
app_db.engine = test_engine
app_db.SessionLocal = TestingSessionLocal


@pytest.fixture(autouse=True, scope="session")
def _create_test_tables():
    from app import models  # noqa: F401 – registers ORM classes
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    # Clean up test.db if anything created it
    for fname in ("test.db", "test.db-wal", "test.db-shm"):
        if os.path.exists(fname):
            try:
                os.remove(fname)
            except OSError:
                pass


@pytest.fixture()
def db_session():
    """Fresh DB session per test, tables cleared before each test."""
    from app.models import Message
    session = TestingSessionLocal()
    session.query(Message).delete()
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    """TestClient with DB and cache overridden."""
    try:
        initialize_cache()
    except Exception:
        pass

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock LiteLLM response factory
# ---------------------------------------------------------------------------


def make_mock_response(
    content: str = "Test response",
    prompt_tokens: int = 10,
    completion_tokens: int = 8,
    cache_hit: bool = False,
):
    """Return a MagicMock that looks like a litellm ModelResponse."""
    response = MagicMock()
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    response.choices = [choice]
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    response._hidden_params = {"cache_hit": cache_hit}
    return response
