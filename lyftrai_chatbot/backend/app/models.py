from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=lambda: datetime.now(timezone.utc).isoformat(),
    )
