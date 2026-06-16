"""SQLAlchemy ORM models.

Schema overview:
  • User           — operators and admins, login credentials
  • Region         — geographic/organisational unit (RBAC boundary)
  • Nvr            — physical NVR device, fan-out source
  • Camera         — single channel on an NVR, mapped to two MediaMTX paths (sub + main)
  • UserRegion     — M2M access grant for operators (admin role bypasses this)
  • NvrEvent       — audit log: auth ok/fail, IP banned, auto-disabled, etc.
  • StreamSession  — who watched what, when (audit + concurrency telemetry)
  • Lockout        — IP-level RTSP ban cool-downs (mirror of NVR firmware ban)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    operator = "operator"


class Vendor(str, enum.Enum):
    dahua = "dahua"
    hikvision = "hikvision"


class StreamQuality(str, enum.Enum):
    sub = "sub"
    main = "main"


# ── User & RBAC ─────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="user_role"), nullable=False, default=Role.operator)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    regions: Mapped[list[Region]] = relationship(
        secondary="user_regions",
        back_populates="users",
        lazy="selectin",
    )


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    users: Mapped[list[User]] = relationship(
        secondary="user_regions",
        back_populates="regions",
    )
    nvrs: Mapped[list[Nvr]] = relationship(back_populates="region")


class UserRegion(Base):
    """M2M: which regions an operator can view."""

    __tablename__ = "user_regions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True
    )


# ── Inventory ───────────────────────────────────────────────────────────────


class Nvr(Base):
    __tablename__ = "nvrs"

    # String PK matches existing nvr_inventory.json ids ("nvr01" etc.) so the
    # MediaMTX path names stay stable across the migration.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=554)
    rtsp_username: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")
    # Encrypted at rest with Fernet (key from settings.nvr_secret_key).
    rtsp_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    vendor: Mapped[Vendor] = mapped_column(SAEnum(Vendor, name="nvr_vendor"), default=Vendor.dahua)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    group: Mapped[str | None] = mapped_column(String(64), nullable=True)

    region_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )
    region: Mapped[Region | None] = relationship(back_populates="nvrs")

    cameras: Mapped[list[Camera]] = relationship(
        back_populates="nvr",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Camera(Base):
    __tablename__ = "cameras"
    __table_args__ = (UniqueConstraint("nvr_id", "channel", name="uq_camera_nvr_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    nvr_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("nvrs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Camera's own LAN IP. When set, the main-stream MediaMTX path pulls
    # straight from the camera (the NVR's RTSP relay drops packets on main —
    # see docs/audit-plan.md §9); NULL keeps the legacy via-NVR source.
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_sub: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    nvr: Mapped[Nvr] = relationship(back_populates="cameras")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def display_name(self) -> str:
        return self.name or f"{self.nvr_id} ch{self.channel}"

    def mediamtx_path(self, quality: StreamQuality) -> str:
        """MediaMTX path name — must match what path_sync generates."""
        suffix = "_main" if quality == StreamQuality.main else ""
        return f"{self.nvr_id}_ch{self.channel}{suffix}"


# ── Audit / runtime state ───────────────────────────────────────────────────


class NvrEvent(Base):
    """Audit log of NVR-related events (auth result, ban, auto-disable)."""

    __tablename__ = "nvr_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    nvr_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class StreamSession(Base):
    """One row per oprator stream session — for audit + concurrency telemetry."""

    __tablename__ = "stream_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("cameras.id", ondelete="CASCADE"), index=True
    )
    quality: Mapped[StreamQuality] = mapped_column(SAEnum(StreamQuality, name="stream_quality"))
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Lockout(Base):
    """IP-level RTSP lockout cool-down (mirrors NVR firmware ban)."""

    __tablename__ = "lockouts"

    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
