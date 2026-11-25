from __future__ import annotations

"""Conversation persistence helpers (SQLite via SQLAlchemy)."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import Message

logger = logging.getLogger(__name__)


def session_exists(session_id: str, db: Session) -> bool:
    return db.query(Message).filter(Message.session_id == session_id).first() is not None


def get_session_history(
    session_id: str,
    db: Session,
    limit: Optional[int] = None,
) -> List[Message]:
    """
    Return messages for the session ordered oldest-first (id ASC).
    If limit is provided and positive, returns only the last `limit` messages in chronological order.
    """
    query = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.id.asc())
    )
    if limit is not None and limit > 0:
        total = query.count()
        if total > limit:
            # Query last N messages by id DESC, then reverse to chronological order
            recent = (
                db.query(Message)
                .filter(Message.session_id == session_id)
                .order_by(Message.id.desc())
                .limit(limit)
                .all()
            )
            return list(reversed(recent))
    return query.all()


def save_conversation_turn(
    session_id: str,
    user_content: str,
    assistant_content: str,
    assistant_metadata: Optional[Dict[str, Any]],
    db: Session,
) -> Tuple[Message, Message]:
    """
    Atomically persist both user message and assistant reply in a single database transaction.
    Rolls back completely if either insert fails.
    """
    now = datetime.now(timezone.utc).isoformat()
    user_record = Message(
        session_id=session_id,
        role="user",
        content=user_content,
        meta_json=None,
        created_at=now,
    )
    assistant_record = Message(
        session_id=session_id,
        role="assistant",
        content=assistant_content,
        meta_json=json.dumps(assistant_metadata) if assistant_metadata else None,
        created_at=now,
    )

    try:
        db.add(user_record)
        db.add(assistant_record)
        db.commit()
        db.refresh(user_record)
        db.refresh(assistant_record)
        return user_record, assistant_record
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist conversation turn for session %s: %s", session_id, exc)
        raise


def save_message(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]],
    db: Session,
) -> Message:
    """Legacy single message persister."""
    record = Message(
        session_id=session_id,
        role=role,
        content=content,
        meta_json=json.dumps(metadata) if metadata else None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record
