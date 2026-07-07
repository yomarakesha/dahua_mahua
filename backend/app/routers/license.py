"""License status + activation endpoints.

  • GET  /license — PUBLIC (no auth). The UI must be able to render an
    "unlicensed / expired" banner both before and after login, and the machine
    fingerprint has to be visible on the activation screen for a fresh install
    where no operator can log in yet. Nothing secret is exposed: the fingerprint
    is a one-way hash and the license fields are already shipped to the client.
  • POST /license — ADMIN only. Upload a `.lic` blob; it's verified, and if the
    signature checks out it's saved to the license path. Returns the new status.

The orchestrator mounts this router in main.py (see the report).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app import licensing
from app.deps import AdminUser

router = APIRouter(prefix="/license", tags=["license"])


def _payload(status: licensing.LicenseStatus, *, fingerprint: str) -> dict:
    d = status.to_dict()
    d["fingerprint"] = fingerprint
    return d


@router.get("")
async def get_license() -> dict:
    """Public: current license status + this machine's fingerprint."""
    fp = licensing.machine_fingerprint()
    status = licensing.get_status()
    return _payload(status, fingerprint=fp)


@router.post("")
async def upload_license(_: AdminUser, request: Request) -> dict:
    """Admin: install a `.lic` file. The raw request body is the license text
    (JSON or a JSON string); it's validated and persisted only if the signature
    is authentic. Returns the resulting status."""
    raw = await request.body()
    if not raw or not raw.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty license body")
    text = raw.decode("utf-8", errors="replace").strip()
    # The browser client sends the .lic as a JSON string (fetch JSON.stringify),
    # so unwrap one layer of JSON-string quoting if present; a raw JSON document
    # (starts with '{') is used as-is.
    if text.startswith('"'):
        try:
            import json

            unwrapped = json.loads(text)
            if isinstance(unwrapped, str):
                text = unwrapped
        except ValueError:
            pass
    lic_status = licensing.save_license(text)
    return _payload(lic_status, fingerprint=licensing.machine_fingerprint())
