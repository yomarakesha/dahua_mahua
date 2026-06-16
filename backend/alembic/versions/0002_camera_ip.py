"""cameras.ip — camera's own LAN address for direct main-stream pulls

Revision ID: 0002_camera_ip
Revises: 0001_initial
Create Date: 2026-06-11

When set, path_sync points the camera's main MediaMTX path straight at the
camera instead of the NVR RTSP relay (which drops packets on main streams —
see docs/audit-plan.md §9). NULL keeps the legacy via-NVR source.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_camera_ip"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("ip", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("cameras", "ip")
