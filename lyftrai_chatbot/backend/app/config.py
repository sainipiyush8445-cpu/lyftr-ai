from __future__ import annotations

import json
import logging
from typing import Any, List, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

DEFAULT_DEV_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]


def parse_cors_origins(v: Any) -> List[str]:
    """
    Parse CORS_ORIGINS from string (*, comma-separated, JSON array) or list.
    Trims whitespace, drops empty items, falls back to safe local-dev origins on invalid input.
    Never crashes.
    """
    def _clean(items: list) -> List[str]:
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        return cleaned if cleaned else DEFAULT_DEV_ORIGINS

    if isinstance(v, list):
        return _clean(v)

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return DEFAULT_DEV_ORIGINS
        if s == "*":
            return ["*"]
        if s.startswith("[") and s.endswith("]"):
            try:
                loaded = json.loads(s)
                if isinstance(loaded, list):
                    return _clean(loaded)
            except Exception as exc:
                logger.warning(
                    "Failed to parse CORS_ORIGINS JSON array (%s), attempting comma split: %s",
                    s,
                    exc,
                )
                inner = s[1:-1].strip()
                parts = [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
                return _clean(parts)
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return _clean(parts)

    logger.warning("Unrecognized CORS_ORIGINS type %s; falling back to default origins", type(v))
    return DEFAULT_DEV_ORIGINS


class Settings(BaseSettings):
    GEMINI_API_KEY_1: str = ""
    GEMINI_API_KEY_2: str = ""
    GEMINI_MODEL: str = "gemini/gemini-3.5-flash"
    DATABASE_URL: str = "sqlite:///./data/app.db"
    ENABLE_FAILURE_SIMULATION: bool = False
    CORS_ORIGINS: Union[List[str], str] = ["*"]
    MAX_HISTORY_MESSAGES: int = 20
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("GEMINI_API_KEY_1", "GEMINI_API_KEY_2", mode="before")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        return v or ""

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _validate_cors_origins(cls, v: Any) -> List[str]:
        return parse_cors_origins(v)


settings = Settings()
