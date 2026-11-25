from __future__ import annotations

"""LLM service: multi-key failover via manual retry loop using litellm.acompletion."""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import litellm
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from app.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {401, 403, 408, 429, 500, 502, 503, 504}

# Human-readable error categories exposed in structured errors (never includes keys)
ERROR_CATEGORY_INVALID_KEY = "invalid_api_key"
ERROR_CATEGORY_QUOTA = "quota_exceeded"
ERROR_CATEGORY_MODEL_NOT_FOUND = "model_not_found"
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_CATEGORY_BAD_REQUEST = "bad_request"
ERROR_CATEGORY_ALL_EXHAUSTED = "all_providers_exhausted"


class AllProvidersFailedError(Exception):
    """Raised when every configured API key has failed or no keys are configured."""

    def __init__(self, message: str = "all providers unavailable", category: str = ERROR_CATEGORY_ALL_EXHAUSTED) -> None:
        super().__init__(message)
        self.message = message
        self.category = category


class ProviderClientError(Exception):
    """Raised for non-retryable provider errors (4xx client/content-policy errors)."""

    def __init__(self, message: str, status_code: int = 400, category: str = ERROR_CATEGORY_BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.category = category


# ---------------------------------------------------------------------------
# Key scrubbing helper
# ---------------------------------------------------------------------------


def scrub_secrets(text: str, extra_keys: Optional[List[str]] = None) -> str:
    """Scrub known API keys and sensitive tokens from error messages and logs."""
    if not text:
        return ""
    scrubbed = str(text)

    # Check known configured keys
    all_keys = [settings.GEMINI_API_KEY_1, settings.GEMINI_API_KEY_2]
    if extra_keys:
        all_keys.extend(extra_keys)

    for k in all_keys:
        if k and len(k.strip()) > 3:
            scrubbed = scrubbed.replace(k.strip(), "[REDACTED_KEY]")

    # Scrub query parameters and headers containing keys
    scrubbed = re.sub(r'(?i)(?:key|api_key|token)=([A-Za-z0-9_\-]{8,})', r'key=[REDACTED]', scrubbed)
    # Scrub Google API key patterns (AIza...)
    scrubbed = re.sub(r'AIza[0-9A-Za-z-_]{35}', '[REDACTED_KEY]', scrubbed)

    return scrubbed


# ---------------------------------------------------------------------------
# Failure simulation helpers
# ---------------------------------------------------------------------------


def parse_simulate_header(header_value: str) -> Dict[str, Union[int, str]]:
    """
    Parse X-Simulate-Failure header.

    Examples:
        "key_1=429"             -> {"key_1": 429}
        "key_1=500,key_2=503"   -> {"key_1": 500, "key_2": 503}
        "*=429"                 -> {"*": 429}
        "key_1=timeout"         -> {"key_1": "timeout"}
    """
    result: Dict[str, Union[int, str]] = {}
    if not header_value:
        return result
    for part in header_value.split(","):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            key_name = k.strip()
            val_str = v.strip().lower()
            try:
                result[key_name] = int(val_str)
            except ValueError:
                result[key_name] = val_str
    return result


def _make_simulated_exception(code_or_reason: Union[int, str], key_id: str) -> Exception:
    message = f"Simulated {code_or_reason} failure for {key_id}"
    model = settings.GEMINI_MODEL
    provider = "google"

    if code_or_reason == 429:
        return RateLimitError(message=message, model=model, llm_provider=provider)
    if code_or_reason in (500, 502):
        return InternalServerError(message=message, model=model, llm_provider=provider)
    if code_or_reason == 503:
        return ServiceUnavailableError(message=message, model=model, llm_provider=provider)
    if code_or_reason in (401, 403):
        return AuthenticationError(message=message, model=model, llm_provider=provider)
    if code_or_reason == 400:
        return BadRequestError(message=message, model=model, llm_provider=provider)
    if code_or_reason in ("timeout", 408, 504):
        return Timeout(message=message, model=model, llm_provider=provider)
    return ServiceUnavailableError(message=message, model=model, llm_provider=provider)


def _should_simulate(key_id: str, simulate_map: Dict[str, Union[int, str]]) -> Optional[Union[int, str]]:
    """Return the status code or reason to simulate for key_id, or None."""
    if "*" in simulate_map:
        return simulate_map["*"]
    return simulate_map.get(key_id)


def is_retryable_error(exc: Exception) -> bool:
    """Return True if the error should trigger failover to the next candidate key."""
    # Never retry BadRequest / client / content-policy errors
    if isinstance(exc, BadRequestError):
        return False

    # Server errors, rate limits, timeouts, connection issues
    if isinstance(
        exc,
        (
            RateLimitError,
            ServiceUnavailableError,
            InternalServerError,
            Timeout,
            APIConnectionError,
            AuthenticationError,
            PermissionDeniedError,
            httpx.TimeoutException,
            httpx.ConnectError,
            asyncio.TimeoutError,
        ),
    ):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code is not None and status_code in RETRYABLE_STATUS_CODES:
        return True

    # Check for keywords in message
    msg = str(exc).lower()
    retryable_keywords = (
        "rate limit",
        "quota",
        "too many requests",
        "service unavailable",
        "gateway timeout",
        "timeout",
        "connection",
        "unauthorized",
        "authentication",
        "permission",
        "malformed provider response",
        "401",
        "403",
        "429",
        "500",
        "502",
        "503",
        "504",
    )
    return any(k in msg for k in retryable_keywords)


# ---------------------------------------------------------------------------
# Main failover function
# ---------------------------------------------------------------------------


async def call_with_failover(
    messages: List[Dict[str, Any]],
    simulate_failures: Optional[Dict[str, Union[int, str]]] = None,
) -> Tuple[Any, str, int]:
    """
    Execute LLM call across configured keys in sequence.
    Unconfigured/empty keys are skipped without counting as attempts.
    Bad key 1 fails over to key 2.
    BadRequestError fails immediately without failover.
    Raises AllProvidersFailedError if all configured keys fail or no keys are configured.

    Returns:
        (litellm_response, key_id_used, fallback_attempts)
    """
    configured_keys = []
    if settings.GEMINI_API_KEY_1 and settings.GEMINI_API_KEY_1.strip():
        configured_keys.append({"key_id": "key_1", "api_key": settings.GEMINI_API_KEY_1.strip()})
    if settings.GEMINI_API_KEY_2 and settings.GEMINI_API_KEY_2.strip():
        configured_keys.append({"key_id": "key_2", "api_key": settings.GEMINI_API_KEY_2.strip()})

    if not configured_keys:
        logger.warning("No Gemini API keys configured.")
        raise AllProvidersFailedError("all providers unavailable")

    fallback_attempts = 0
    last_error: Optional[Exception] = None

    for idx, key_info in enumerate(configured_keys):
        key_id = key_info["key_id"]
        api_key = key_info["api_key"]

        if idx > 0:
            fallback_attempts += 1

        # ---- Failure simulation ----
        if simulate_failures:
            sim_code = _should_simulate(key_id, simulate_failures)
            if sim_code is not None:
                logger.info("Simulating %s failure for %s", sim_code, key_id)
                sim_exc = _make_simulated_exception(sim_code, key_id)
                if isinstance(sim_exc, BadRequestError):
                    raise ProviderClientError(
                        message=f"Simulated bad request for {key_id}", status_code=400
                    )
                last_error = sim_exc
                continue

        # ---- Real API call ----
        try:
            response = await litellm.acompletion(
                model=settings.GEMINI_MODEL,
                messages=messages,
                api_key=api_key,
                timeout=30.0,
                num_retries=0,
                caching=True,
            )

            # Guard choices
            if not getattr(response, "choices", None) or len(response.choices) == 0:
                logger.warning("Key %s returned empty choices list", key_id)
                last_error = ValueError("Malformed provider response: empty choices list")
                continue
            if not getattr(response.choices[0], "message", None):
                logger.warning("Key %s returned missing message in choice", key_id)
                last_error = ValueError("Malformed provider response: missing message in choice")
                continue

            return response, key_id, fallback_attempts
        except ProviderClientError:
            raise
        except Exception as exc:
            scrubbed = scrub_secrets(str(exc))
            if isinstance(exc, NotFoundError):
                # NotFoundError = model retired/removed — do NOT failover, give clear message
                logger.warning(
                    "Key %s returned NotFoundError (model retired or not available): %s",
                    key_id, scrubbed,
                )
                raise ProviderClientError(
                    message=(
                        f"The configured model '{settings.GEMINI_MODEL}' was not found or has been retired. "
                        "Update GEMINI_MODEL to a currently supported model."
                    ),
                    status_code=404,
                    category=ERROR_CATEGORY_MODEL_NOT_FOUND,
                )
            if isinstance(exc, BadRequestError):
                logger.warning("Key %s returned non-retryable BadRequest: %s", key_id, scrubbed)
                raise ProviderClientError(
                    message=scrubbed,
                    status_code=getattr(exc, "status_code", 400),
                    category=ERROR_CATEGORY_BAD_REQUEST,
                )

            if is_retryable_error(exc):
                # Classify into human-readable categories for logging (never expose keys)
                if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
                    cat = ERROR_CATEGORY_INVALID_KEY
                elif isinstance(exc, RateLimitError):
                    cat = ERROR_CATEGORY_QUOTA
                elif isinstance(exc, (Timeout, asyncio.TimeoutError, httpx.TimeoutException)):
                    cat = ERROR_CATEGORY_TIMEOUT
                else:
                    cat = ERROR_CATEGORY_PROVIDER_UNAVAILABLE
                logger.warning(
                    "Key %s failed [%s] with retryable error: %s", key_id, cat, scrubbed
                )
                last_error = exc
                continue

            # Non-retryable unexpected error
            logger.error("Key %s failed with non-retryable error: %s", key_id, scrubbed)
            raise

    safe_error_msg = scrub_secrets(str(last_error)) if last_error else "all providers unavailable"
    logger.error("All providers exhausted. Last error: %s", safe_error_msg)
    raise AllProvidersFailedError("all providers unavailable") from last_error
