from __future__ import annotations

"""Cache service – thin wrapper that sets up LiteLLM's built-in in-memory cache."""

import logging
from typing import Optional

import litellm

logger = logging.getLogger(__name__)

_cache_initialized = False


def initialize_cache() -> bool:
    """Call at application startup to enable LiteLLM InMemoryCache. Degrades safely on failure."""
    global _cache_initialized
    try:
        from litellm.caching.caching import Cache

        # Compatibility fix for openai/typing where TranscriptionCreateParams lacks __annotations__
        try:
            Cache._get_litellm_supported_transcription_kwargs = lambda self: set()
        except Exception:
            pass

        litellm.cache = Cache(type="local")
        litellm.enable_cache()
        _cache_initialized = True
        logger.info("LiteLLM in-memory cache initialized.")
        return True
    except Exception as exc:
        logger.warning("Could not initialize LiteLLM cache: %s. Continuing without cache.", exc)
        _cache_initialized = False
        return False


def is_cache_enabled() -> bool:
    """Check actual active cache state."""
    try:
        return bool(_cache_initialized and getattr(litellm, "cache", None) is not None)
    except Exception:
        return False
