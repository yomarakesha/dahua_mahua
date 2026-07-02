"""user_cameras — per-camera access grants (M2M users↔cameras)

Revision ID: 0003_user_cameras
Revises: 0002_camera_ip
Create Date: 2026-07-02

The `user_cameras` association table backs ``User.cameras`` (models.py), which is
``lazy="selectin"`` loaded on EVERY authenticated request via get_current_user.
It was defined in the ORM but never added to the migrations, so a fresh Postgres
deploy (which goes through ``alembic upgrade head``, never create_all) would fail
with ``relation "user_cameras" does not exist`` on every request. Mirrors the
`user_regions` pattern from 0001.

Idempotent for existing SQLite dev DBs where create_all already made the table:
skip the create when the table is already present.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "0003_user_cameras"
down_revision: Union[str, None] = "0002_camera_ip"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Existing SQLite dev DBs already have this table via create_all — skip so
    # the migration is safe to run against them.
    if inspect(bind).has_table("user_cameras"):
        return
    op.create_table(
        "user_cameras",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_cameras")
