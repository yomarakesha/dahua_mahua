"""Offline licensing — Ed25519 signed license files, verified locally.

Design (see docs/licensing-plan.md):
  • We (vendor) hold the Ed25519 PRIVATE key offline. The app embeds only the
    PUBLIC key and verifies signatures with it — no network, ever.
  • A license is a small JSON document:
        {
          "customer": "ACME LLC",
          "site_id": "acme-01",
          "issued": "2026-07-07",
          "expires": "2027-07-07",     # null = perpetual
          "features": ["playback", "hikvision"],
          "max_cameras": 64,
          "max_nvrs": 8,
          "hardware_id": "<machine fingerprint>",
          "sig": "<base64 Ed25519 signature over the canonical payload>"
        }
  • The signature covers the canonical JSON of every field EXCEPT ``sig``
    (sorted keys, compact separators). Tampering with any field invalidates it.
  • On startup / on demand the app computes this machine's fingerprint, verifies
    the signature, checks expiry, and checks ``hardware_id == fingerprint``.

Everything here is defensive: fingerprinting never raises, and verification of a
missing / garbage / tampered / expired / foreign license returns a well-formed
*invalid* status rather than throwing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ── Paths / config ───────────────────────────────────────────────────────────
# backend/app/licensing.py -> backend/app -> backend -> backend/licensing
_BACKEND_DIR = Path(__file__).resolve().parent.parent
LICENSING_DIR = _BACKEND_DIR / "licensing"
PUBLIC_KEY_PATH = LICENSING_DIR / "public_key.pem"
DEFAULT_LICENSE_PATH = LICENSING_DIR / "license.lic"

# Env overrides (deliberately NOT wired into settings.py — file-ownership rule):
#   LICENSE_PUBLIC_KEY  — PEM contents of the public key (takes priority over the file)
#   LICENSE_FILE        — path to the installed .lic file
ENV_PUBLIC_KEY = "LICENSE_PUBLIC_KEY"
ENV_LICENSE_FILE = "LICENSE_FILE"

# Fields that make up the signed payload (everything except the signature).
_SIGNED_FIELDS = (
    "customer",
    "site_id",
    "issued",
    "expires",
    "features",
    "max_cameras",
    "max_nvrs",
    "hardware_id",
)
_SIG_FIELD = "sig"


# ── Status dataclass ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LicenseStatus:
    """Result of verifying a license. Always well-formed, even for failures."""

    valid: bool
    reason: str = ""
    customer: str | None = None
    site_id: str | None = None
    issued: str | None = None
    expires: str | None = None  # ISO date string, or None for perpetual
    features: list[str] = field(default_factory=list)
    max_cameras: int | None = None
    max_nvrs: int | None = None
    hardware_id: str | None = None
    days_left: int | None = None  # None = perpetual or unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "customer": self.customer,
            "site_id": self.site_id,
            "issued": self.issued,
            "expires": self.expires,
            "features": list(self.features),
            "limits": {"max_cameras": self.max_cameras, "max_nvrs": self.max_nvrs},
            "days_left": self.days_left,
        }


def _invalid(reason: str) -> LicenseStatus:
    return LicenseStatus(valid=False, reason=reason)


# ── Canonical payload / signing helpers ──────────────────────────────────────
def canonical_payload(doc: dict[str, Any]) -> bytes:
    """Deterministic byte encoding of the signed portion of a license.

    Sorted keys + compact separators over the ``_SIGNED_FIELDS`` only, so the
    same logical license always hashes/signs identically regardless of the key
    order the JSON happened to arrive in.
    """
    payload = {k: doc.get(k) for k in _SIGNED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_license(payload_fields: dict[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Given the unsigned fields, return a complete license dict with ``sig``."""
    doc = {k: payload_fields.get(k) for k in _SIGNED_FIELDS}
    sig = private_key.sign(canonical_payload(doc))
    doc[_SIG_FIELD] = base64.b64encode(sig).decode("ascii")
    return doc


# ── Machine fingerprint ──────────────────────────────────────────────────────
def _read_first_file(paths: list[str]) -> str | None:
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return text
        except OSError:
            continue
    return None


def _windows_machine_guid() -> str | None:
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_LOCAL_MACHINE,  # type: ignore[attr-defined]
            r"SOFTWARE\Microsoft\Cryptography",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")  # type: ignore[attr-defined]
            if value:
                return str(value).strip()
    except Exception:  # noqa: BLE001 — never let fingerprinting raise
        return None
    return None


def _linux_dmi_uuid() -> str | None:
    return _read_first_file(
        [
            "/sys/class/dmi/id/product_uuid",
            "/sys/class/dmi/id/board_serial",
        ]
    )


def _collect_fingerprint_sources() -> list[str]:
    """Gather 1-2 stable host identifiers, most-stable first. Best effort."""
    system = platform.system()
    sources: list[str] = []

    try:
        if system == "Windows":
            guid = _windows_machine_guid()
            if guid:
                sources.append(f"winguid:{guid}")
        elif system == "Linux":
            mid = _read_first_file(["/etc/machine-id", "/var/lib/dbus/machine-id"])
            if mid:
                sources.append(f"machineid:{mid}")
            dmi = _linux_dmi_uuid()
            if dmi:
                sources.append(f"dmi:{dmi}")
        elif system == "Darwin":
            # macOS: the IOPlatformUUID is stable across reboots.
            try:
                out = subprocess.run(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for line in out.stdout.splitlines():
                    if "IOPlatformUUID" in line:
                        val = line.split("=", 1)[-1].strip().strip('"')
                        if val:
                            sources.append(f"macuuid:{val}")
                        break
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # Universal fallback: the primary NIC MAC via uuid.getnode(). Marked so a
    # locally-administered/random MAC (getnode's 8th bit set) still yields a
    # deterministic value on this boot.
    try:
        node = uuid.getnode()
        sources.append(f"node:{node:012x}")
    except Exception:  # noqa: BLE001
        pass

    if not sources:
        # Last-ditch: something deterministic-per-host so we never return "".
        sources.append(f"platform:{platform.node()}:{platform.machine()}")
    return sources


def machine_fingerprint() -> str:
    """Stable, deterministic hash of this host's identifiers. Never raises.

    Sources by OS:
      • Windows — registry MachineGuid.
      • Linux   — /etc/machine-id (+ DMI product_uuid).
      • macOS   — IOPlatformUUID.
      • Any     — primary MAC (uuid.getnode()) as a fallback / secondary factor.
    """
    try:
        sources = _collect_fingerprint_sources()
        raw = "|".join(sources)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 — absolutely never raise
        digest = hashlib.sha256(b"dss-fallback").hexdigest()
    # 64 hex chars is unwieldy for a human to read/type; 32 keeps ample entropy.
    return digest[:32]


# ── Public-key loading ───────────────────────────────────────────────────────
def load_public_key(pubkey: bytes | str | None = None) -> Ed25519PublicKey | None:
    """Load the verification key. Priority: explicit arg → env → PEM file.

    Returns None if no key is available (verification then fails closed).
    """
    pem: bytes | None = None
    if pubkey is not None:
        pem = pubkey.encode("utf-8") if isinstance(pubkey, str) else pubkey
    else:
        env_val = os.environ.get(ENV_PUBLIC_KEY)
        if env_val:
            pem = env_val.encode("utf-8")
        elif PUBLIC_KEY_PATH.exists():
            try:
                pem = PUBLIC_KEY_PATH.read_bytes()
            except OSError:
                pem = None
    if not pem:
        return None
    try:
        key = serialization.load_pem_public_key(pem)
        if isinstance(key, Ed25519PublicKey):
            return key
    except Exception:  # noqa: BLE001
        return None
    return None


# ── Date / expiry helpers ────────────────────────────────────────────────────
def _parse_date(value: str) -> date | None:
    """Parse an ISO date (YYYY-MM-DD) or full datetime → date. None on failure."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        return None


def _today(now: datetime | date | None) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        return now.date()
    return now


# ── Verification ─────────────────────────────────────────────────────────────
def verify_license(
    raw: bytes | str,
    *,
    pubkey: bytes | str | Ed25519PublicKey | None = None,
    now: datetime | date | None = None,
    fingerprint: str | None = None,
) -> LicenseStatus:
    """Parse + verify a license blob. Returns a well-formed status, never raises.

    Checks, in order: JSON parse → signature (Ed25519 over the canonical
    payload) → hardware_id binding → expiry.
    """
    # 1. Parse JSON.
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        doc = json.loads(text)
        if not isinstance(doc, dict):
            return _invalid("License is not a JSON object")
    except Exception:  # noqa: BLE001
        return _invalid("License is not valid JSON")

    sig_b64 = doc.get(_SIG_FIELD)
    if not isinstance(sig_b64, str) or not sig_b64:
        return _invalid("License is missing a signature")
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except Exception:  # noqa: BLE001
        return _invalid("License signature is malformed")

    # 2. Verify signature.
    key = pubkey if isinstance(pubkey, Ed25519PublicKey) else load_public_key(pubkey)
    if key is None:
        return _invalid("No public key available to verify the license")
    try:
        key.verify(sig, canonical_payload(doc))
    except InvalidSignature:
        return _invalid("License signature is invalid (tampered or wrong key)")
    except Exception:  # noqa: BLE001
        return _invalid("License signature could not be verified")

    # Signature is authentic → we can trust the fields.
    customer = doc.get("customer")
    site_id = doc.get("site_id")
    issued = doc.get("issued")
    expires = doc.get("expires")
    features = doc.get("features") or []
    if not isinstance(features, list):
        features = []
    max_cameras = doc.get("max_cameras")
    max_nvrs = doc.get("max_nvrs")
    hardware_id = doc.get("hardware_id")

    def _partial(reason: str, days_left: int | None = None) -> LicenseStatus:
        return LicenseStatus(
            valid=False,
            reason=reason,
            customer=customer,
            site_id=site_id,
            issued=issued,
            expires=expires,
            features=[str(f) for f in features],
            max_cameras=max_cameras,
            max_nvrs=max_nvrs,
            hardware_id=hardware_id,
            days_left=days_left,
        )

    # 3. Hardware binding.
    fp = fingerprint if fingerprint is not None else machine_fingerprint()
    if hardware_id and fp and hardware_id != fp:
        return _partial("License is bound to a different machine")

    # 4. Expiry.
    days_left: int | None = None
    if expires not in (None, "", "null"):
        exp_date = _parse_date(str(expires))
        if exp_date is None:
            return _partial("License expiry date is malformed")
        today = _today(now)
        days_left = (exp_date - today).days
        if days_left < 0:
            return _partial("License has expired", days_left=days_left)

    return LicenseStatus(
        valid=True,
        reason="ok",
        customer=customer,
        site_id=site_id,
        issued=issued,
        expires=expires if expires not in ("", "null") else None,
        features=[str(f) for f in features],
        max_cameras=max_cameras,
        max_nvrs=max_nvrs,
        hardware_id=hardware_id,
        days_left=days_left,
    )


# ── License state machine (grace → hard-block) ───────────────────────────────
# The set of states in which enforcement (when ON) hard-blocks the protected API.
# ``valid`` and ``grace`` are allowed through; everything else is a block.
BLOCKED_STATES = frozenset({"expired", "missing", "invalid", "mismatch"})


def license_state(
    status: LicenseStatus | None = None,
    *,
    today: datetime | date | None = None,
    grace_days: int = 0,
) -> dict[str, Any]:
    """Classify a verified license into an enforcement state. Pure + testable.

    Returns ``{"state", "days_left", "grace_days_left"}`` where ``state`` is one of:
      • ``valid``    — signed, machine-matched, not expired (or perpetual).
      • ``grace``    — signed + machine-matched but EXPIRED, within ``grace_days``
                       of ``expires`` → app keeps working with a renewal warning.
      • ``expired``  — past ``expires`` AND past the grace window → block.
      • ``missing``  — no license file installed → block.
      • ``invalid``  — bad/absent signature, tampered, or unverifiable → block.
      • ``mismatch`` — signature valid but bound to a different machine → block.

    ``today`` is injectable so expiry/grace boundaries are deterministic in tests;
    it defaults to the current UTC date. ``days_left`` is the (possibly negative)
    day count to ``expires``; ``grace_days_left`` is how many grace days remain
    (only set for the grace state, ``None`` otherwise).

    Reason strings from ``verify_license`` are used to disambiguate the failure
    kinds — the same substring convention the frontend already relies on. Expiry
    itself is re-decided here from ``expires`` vs ``today`` so a license that was
    valid at verify-time is correctly rolled into grace/expired as the clock moves.
    """
    if status is None:
        status = get_status()
    when = _today(today)
    reason = (status.reason or "").lower()

    # A signature-valid, machine-matched license reaches verify's expiry check —
    # so it is either ``valid`` (status.valid) or rejected solely for expiry
    # ("...has expired"). Both mean crypto + hardware binding are sound; only the
    # clock decides valid / grace / expired from here.
    crypto_and_machine_ok = status.valid or "expired" in reason
    if crypto_and_machine_ok:
        exp_raw = status.expires
        if exp_raw in (None, "", "null"):
            return {"state": "valid", "days_left": None, "grace_days_left": None}
        exp_date = _parse_date(str(exp_raw))
        if exp_date is None:
            # Expiry present but unparseable → treat as invalid (never runs).
            return {"state": "invalid", "days_left": None, "grace_days_left": None}
        days_left = (exp_date - when).days
        if days_left >= 0:
            return {"state": "valid", "days_left": days_left, "grace_days_left": None}
        grace_left = grace_days + days_left  # days_left is negative here
        if grace_left >= 0:
            return {"state": "grace", "days_left": days_left, "grace_days_left": grace_left}
        return {"state": "expired", "days_left": days_left, "grace_days_left": grace_left}

    if "no license" in reason or "empty" in reason or "unreadable" in reason:
        return {"state": "missing", "days_left": None, "grace_days_left": None}
    if "machine" in reason:  # "License is bound to a different machine"
        return {"state": "mismatch", "days_left": None, "grace_days_left": None}
    return {"state": "invalid", "days_left": None, "grace_days_left": None}


# ── Loading from disk + cache ────────────────────────────────────────────────
def license_path() -> Path:
    override = os.environ.get(ENV_LICENSE_FILE)
    return Path(override) if override else DEFAULT_LICENSE_PATH


def load_license(
    path: str | Path | None = None,
    *,
    pubkey: bytes | str | Ed25519PublicKey | None = None,
    now: datetime | date | None = None,
    fingerprint: str | None = None,
) -> LicenseStatus:
    """Read + verify the license file at ``path`` (or the configured default)."""
    p = Path(path) if path is not None else license_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return _invalid("No license installed")
    except OSError as e:
        return _invalid(f"License file is unreadable: {e.__class__.__name__}")
    if not raw.strip():
        return _invalid("License file is empty")
    return verify_license(raw, pubkey=pubkey, now=now, fingerprint=fingerprint)


# Module-level cache so we don't re-read/re-verify on every request.
_cache_lock = Lock()
_cached_status: LicenseStatus | None = None


def get_status(refresh: bool = False) -> LicenseStatus:
    """Cached current license status. Pass refresh=True to re-read from disk."""
    global _cached_status
    with _cache_lock:
        if _cached_status is None or refresh:
            _cached_status = load_license()
        return _cached_status


def invalidate_cache() -> None:
    global _cached_status
    with _cache_lock:
        _cached_status = None


def save_license(raw: bytes | str, *, path: str | Path | None = None) -> LicenseStatus:
    """Validate a license blob, and if valid write it to disk + refresh cache.

    Returns the resulting status. On an invalid blob nothing is written.
    """
    status = verify_license(raw)
    # We still persist a signature-valid-but-expired/foreign license so the UI
    # can display why it's rejected; we refuse only cryptographically-bad blobs.
    if status.customer is None and not status.valid:
        # Couldn't even verify the signature → don't overwrite a good file.
        return status
    is_default = path is None
    p = Path(path) if path is not None else license_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8")
        p.write_bytes(data)
    except OSError as e:
        return _invalid(f"Could not save license: {e.__class__.__name__}")
    invalidate_cache()
    # Refresh the module cache only when we wrote to the live license path; for
    # a custom path (tests) just re-verify from that file.
    return get_status(refresh=True) if is_default else load_license(p)


# ── Enforcement helper (caller decides what to do) ───────────────────────────
def enforce() -> LicenseStatus:
    """Return the current license status for the app to act on.

    Intentionally does NOT block or exit — the caller (startup hook, router,
    middleware) decides whether to hard-fail, degrade to read-only/demo, or just
    surface a warning. This keeps policy out of the crypto module.
    """
    return get_status()


# ── Dev / test keypair generator ─────────────────────────────────────────────
def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair (for tests + the one-time vendor setup)."""
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def public_key_pem(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def private_key_pem(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_key(pem: bytes | str) -> Ed25519PrivateKey:
    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("PEM is not an Ed25519 private key")
    return key
