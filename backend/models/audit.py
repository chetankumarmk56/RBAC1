"""Audit trail. Every tool request is written here — allowed and denied alike."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Denormalised so the log stays readable even if the user or role changes later.
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    agent: Mapped[str | None] = mapped_column(String(50))
    tool: Mapped[str | None] = mapped_column(String(60))
    required_permission: Mapped[str | None] = mapped_column(String(50))
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # ALLOWED | DENIED
    reason: Mapped[str | None] = mapped_column(String(255))
    request_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
