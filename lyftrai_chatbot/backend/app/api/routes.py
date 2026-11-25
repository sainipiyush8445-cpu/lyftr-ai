from __future__ import annotations

"""FastAPI route definitions."""

import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas import (
    ChatRequest,
    ChatResponse,
    MessageMetadata,
    MessageOut,
    MessageOutMetadata,
    SessionHistory,
)
from app.services import conversation_service as conv_svc
from app.services.llm_service import (
    AllProvidersFailedError,
    ProviderClientError,
    call_with_failover,
    parse_simulate_header,
)

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


# ---------------------------------------------------------------------------
# Helper: build messages list from history + current turn
# ---------------------------------------------------------------------------


def _build_messages(
    system_prompt: Optional[str],
    history: list,
    user_message: str,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for record in history:
        if record.role in ("user", "assistant"):
            messages.append({"role": record.role, "content": record.content})
    messages.append({"role": "user", "content": user_message})
    return messages


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    raw_request: Request,
    x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure"),
    db: Session = Depends(get_db),
):
    start_time = time.monotonic()

    # Parse failure simulation header
    simulate_failures: Optional[Dict[str, Any]] = None
    is_simulation = (
        settings.ENABLE_FAILURE_SIMULATION
        and x_simulate_failure is not None
    )
    if is_simulation:
        simulate_failures = parse_simulate_header(x_simulate_failure)

    # Load bounded conversation history for context window safety
    history = conv_svc.get_session_history(
        request.session_id, db, limit=settings.MAX_HISTORY_MESSAGES
    )

    # Build messages
    messages = _build_messages(request.system_prompt, history, request.message)

    # Bypass cache for simulated requests by injecting a unique nonce
    if is_simulation:
        nonce_msg = {
            "role": "system",
            "content": f"__simulation_nonce__={uuid.uuid4().hex}",
        }
        messages = messages + [nonce_msg]

    # Call LLM with failover
    try:
        response, key_id_used, fallback_attempts = await call_with_failover(
            messages=messages,
            simulate_failures=simulate_failures,
        )
    except AllProvidersFailedError:
        raise HTTPException(status_code=503, detail="all providers unavailable")
    except ProviderClientError as client_err:
        raise HTTPException(status_code=client_err.status_code, detail=client_err.message)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # Determine cache hit
    cache_hit = False
    try:
        cache_hit = bool(response._hidden_params.get("cache_hit", False))
    except Exception:
        pass

    # Extract reply text safely
    reply_text = ""
    try:
        reply_text = response.choices[0].message.content or ""
    except Exception as exc:
        logger.error("Failed to extract content from provider response: %s", exc)
        raise HTTPException(status_code=502, detail="Invalid provider response format")

    # Extract token usage safely
    input_tokens: int = 0
    output_tokens: int = 0
    try:
        input_tokens = response.usage.prompt_tokens or 0
        output_tokens = response.usage.completion_tokens or 0
    except Exception:
        pass

    # Build metadata
    if cache_hit:
        resolution_source = "LLM_CACHE"
        response_key_id = None
        response_fallback = 0
    else:
        resolution_source = "LIVE_INFERENCE"
        response_key_id = key_id_used
        response_fallback = fallback_attempts

    metadata = MessageMetadata(
        resolution_source=resolution_source,
        model=settings.GEMINI_MODEL,
        latency_ms=elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        key_id=response_key_id,
        fallback_attempts=response_fallback,
    )

    # Persist turn atomically in a single transaction
    conv_svc.save_conversation_turn(
        session_id=request.session_id,
        user_content=request.message,
        assistant_content=reply_text,
        assistant_metadata={
            "resolution_source": resolution_source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        db=db,
    )

    return ChatResponse(
        session_id=request.session_id,
        reply=reply_text,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/messages
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages", response_model=SessionHistory)
def get_session_messages(session_id: str, db: Session = Depends(get_db)):
    if not SESSION_ID_PATTERN.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")

    if not conv_svc.session_exists(session_id, db):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    history = conv_svc.get_session_history(session_id, db)

    out_messages: List[MessageOut] = []
    for record in history:
        # Skip system prompt rows if any were present
        if record.role == "system":
            continue
        msg_meta: Optional[MessageOutMetadata] = None
        if record.meta_json:
            try:
                raw = json.loads(record.meta_json)
                msg_meta = MessageOutMetadata(
                    resolution_source=raw.get("resolution_source"),
                    input_tokens=raw.get("input_tokens"),
                    output_tokens=raw.get("output_tokens"),
                )
            except Exception:
                pass
        out_messages.append(
            MessageOut(role=record.role, content=record.content, metadata=msg_meta)
        )

    return SessionHistory(session_id=session_id, messages=out_messages)
