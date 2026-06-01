"""initial schema: users, regions, nvrs, cameras, events, sessions, lockouts

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_role = sa.Enum("admin", "operator", name="user_role")
    nvr_vendor = sa.Enum("dahua", "hikvision", name="nvr_vendor")
    stream_quality = sa.Enum("sub", "main", name="stream_quality")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_regions_slug", "regions", ["slug"])

    op.create_table(
        "user_regions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("region_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "nvrs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("port", sa.Integer, nullable=False, server_default="554"),
        sa.Column("rtsp_username", sa.String(64), nullable=False, server_default="admin"),
        sa.Column("rtsp_password_encrypted", sa.Text, nullable=False),
        sa.Column("vendor", nvr_vendor, nullable=False, server_default="dahua"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("group", sa.String(64), nullable=True),
        sa.Column("region_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "cameras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nvr_id", sa.String(64), sa.ForeignKey("nvrs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("has_sub", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("has_main", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("nvr_id", "channel", name="uq_camera_nvr_channel"),
    )
    op.create_index("ix_cameras_nvr_id", "cameras", ["nvr_id"])

    op.create_table(
        "nvr_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nvr_id", sa.String(64), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nvr_events_nvr_id", "nvr_events", ["nvr_id"])
    op.create_index("ix_nvr_events_event_type", "nvr_events", ["event_type"])
    op.create_index("ix_nvr_events_created_at", "nvr_events", ["created_at"])

    op.create_table(
        "stream_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quality", stream_quality, nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stream_sessions_user_id", "stream_sessions", ["user_id"])
    op.create_index("ix_stream_sessions_camera_id", "stream_sessions", ["camera_id"])
    op.create_index("ix_stream_sessions_started_at", "stream_sessions", ["started_at"])

    op.create_table(
        "lockouts",
        sa.Column("ip", sa.String(64), primary_key=True),
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cooldown_seconds", sa.Integer, nullable=False, server_default="1800"),
    )


def downgrade() -> None:
    op.drop_table("lockouts")
    op.drop_table("stream_sessions")
    op.drop_table("nvr_events")
    op.drop_table("cameras")
    op.drop_table("nvrs")
    op.drop_table("user_regions")
    op.drop_table("regions")
    op.drop_table("users")
    sa.Enum(name="stream_quality").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nvr_vendor").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
