"""Public branding endpoint for white-label / per-deployment rebranding.

Unauthenticated on purpose: the login page needs the brand (name, logo, accent
color) BEFORE any user has authenticated, so this must be reachable pre-login.
It is cheap and read-only — it just echoes the BRAND_* settings, no DB access —
so leaving it open carries no risk (the values are already visible in the UI).

The defaults reproduce today's look exactly, so a deployment that sets no
BRAND_* env vars is visually unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.settings import get_settings

router = APIRouter(prefix="/branding", tags=["meta"])


class Branding(BaseModel):
    name: str
    short: str
    primary: str
    accent: str
    logo_url: str


@router.get("", response_model=Branding)
async def branding() -> Branding:
    s = get_settings()
    return Branding(
        name=s.brand_name,
        short=s.brand_short,
        primary=s.brand_primary,
        accent=s.brand_accent,
        logo_url=s.brand_logo_url,
    )
