from __future__ import annotations

"""FastAPI application entry point."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import create_tables, engine
from app.services.cache_service import initialize_cache, is_cache_enabled

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Lyftr AI backend...")
    create_tables()
    initialize_cache()
    logger.info("Startup complete.")
    yield


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Lyftr AI Chatbot Backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS configuration
is_wildcard = "*" in settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=not is_wildcard,  # Spec disallows credentials when origin is '*'
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health endpoint — application + database readiness only
# Note: keys_configured = number of non-empty key env vars; it does NOT verify
# that the keys are valid or that the Gemini API is reachable.  Use
# GET /health/provider for a lightweight provider configuration check.
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    # Verify DB reachable without leaking internal error strings
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Health check DB connection failed: %s", exc)
        raise HTTPException(status_code=503, detail="Database unreachable")

    keys_configured = sum(
        1
        for k in (settings.GEMINI_API_KEY_1, settings.GEMINI_API_KEY_2)
        if k and k.strip()
    )
    if keys_configured == 0:
        raise HTTPException(status_code=503, detail="No API keys configured")

    return {
        "status": "ok",
        "keys_configured": keys_configured,
        "cache_enabled": is_cache_enabled(),
    }


# ---------------------------------------------------------------------------
# Provider readiness check — verifies Gemini config WITHOUT consuming quota
# Returns the configured model, number of keys, and failure simulation status.
# Does NOT make a real API call; a 200 here means config looks valid, not that
# the Gemini endpoint is reachable.
# ---------------------------------------------------------------------------
@app.get("/health/provider")
def health_provider():
    keys_configured = sum(
        1
        for k in (settings.GEMINI_API_KEY_1, settings.GEMINI_API_KEY_2)
        if k and k.strip()
    )

    issues = []
    if keys_configured == 0:
        issues.append("No Gemini API keys configured (GEMINI_API_KEY_1 / GEMINI_API_KEY_2 are empty)")
    if not settings.GEMINI_MODEL or not settings.GEMINI_MODEL.strip():
        issues.append("GEMINI_MODEL is not set")

    if issues:
        raise HTTPException(
            status_code=503,
            detail={"status": "misconfigured", "issues": issues},
        )

    return {
        "status": "configured",
        "model": settings.GEMINI_MODEL,
        "keys_configured": keys_configured,
        "failure_simulation_enabled": settings.ENABLE_FAILURE_SIMULATION,
        "note": (
            "This check verifies env-var configuration only. "
            "It does not make a live Gemini API call."
        ),
    }


# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
from app.api.routes import router  # noqa: E402

app.include_router(router)
