"""Create or reset the admin account — inventory-free (unlike `app.seed`).

For firms installing the VMS WITHOUT the vendor present: a one-shot, idempotent
command to mint (or reset the password of) an admin from BOOTSTRAP_ADMIN_USERNAME
/ BOOTSTRAP_ADMIN_PASSWORD (or --username/--password). The created/updated admin
always gets must_change_password=True, so the first login forces a rotation.

Usage (from backend/):
    python -m app.create_admin                       # env BOOTSTRAP_ADMIN_*
    python -m app.create_admin --username ops --password 's3cret'
    python -m app.create_admin --reset-password      # reset an existing admin
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import Role, User
from app.security import hash_password
from app.settings import get_settings


async def _ensure_schema_sqlite() -> None:
    """Create tables against an empty SQLite DB so this works before the backend
    has ever booted. Postgres goes through Alembic — never autocreate there."""
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_or_reset_admin(
    username: str,
    password: str,
    *,
    reset_password: bool = False,
) -> str:
    """Idempotent: create the admin if missing, else optionally reset its
    password. Always leaves must_change_password=True. Returns a status word
    ("created" | "password-reset" | "exists")."""
    await _ensure_schema_sqlite()
    async with SessionLocal() as session:
        async with session.begin():
            existing = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    User(
                        username=username,
                        password_hash=hash_password(password),
                        role=Role.admin,
                        must_change_password=True,
                    )
                )
                return "created"
            # Already there. Only touch the password when explicitly asked, so a
            # re-run doesn't silently clobber a rotated password.
            if reset_password:
                existing.password_hash = hash_password(password)
                existing.role = Role.admin
                existing.is_active = True
                existing.must_change_password = True
                return "password-reset"
            return "exists"


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Create or reset the VMS admin account (inventory-free)."
    )
    parser.add_argument("--username", default=settings.bootstrap_admin_username)
    parser.add_argument("--password", default=settings.bootstrap_admin_password)
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="If the admin already exists, reset its password (default: leave it).",
    )
    args = parser.parse_args(argv)

    result = asyncio.run(
        create_or_reset_admin(
            args.username, args.password, reset_password=args.reset_password
        )
    )
    # Never print the literal password (it lands in install logs).
    if result == "created":
        print(f"Admin '{args.username}' created (must change password on first login).")
    elif result == "password-reset":
        print(f"Admin '{args.username}' password reset (must change on next login).")
    else:
        print(
            f"Admin '{args.username}' already exists — unchanged. "
            "Pass --reset-password to reset it."
        )


if __name__ == "__main__":
    main()
