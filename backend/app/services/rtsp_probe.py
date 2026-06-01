"""RTSP digest-auth health check for Dahua/Hikvision NVRs.

Ported from legacy dss/nvr.py — same protocol semantics:
  1. OPTIONS without auth → expect 401 Digest challenge.
  2. Compute MD5 digest, resend → expect 200.
Status 403 means the NVR firmware has banned our IP — record a cool-down so
we don't keep hammering it. 401 means wrong password; we surface that to the
operator but do NOT record a lockout (no firmware ban yet).

This module is sync (uses stdlib socket) — call it from an executor.
"""

from __future__ import annotations

import hashlib
import logging
import socket
from dataclasses import dataclass

from app.models import Vendor

log = logging.getLogger("dss.rtsp")

DEFAULT_BAN_COOLDOWN = 1800


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    message: str
    banned: bool = False
    banned_cooldown: int = 0


def _parse_status(response: str) -> tuple[int, str]:
    first = response.split("\r\n", 1)[0]
    parts = first.split(None, 2)
    if len(parts) >= 2:
        try:
            return int(parts[1]), first
        except ValueError:
            pass
    return 0, first


def _parse_digest(response: str) -> dict[str, str] | None:
    for line in response.split("\r\n"):
        if line.lower().startswith("www-authenticate:"):
            value = line.split(":", 1)[1].strip()
            if value.lower().startswith("digest"):
                out: dict[str, str] = {}
                for part in value[6:].split(","):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        out[k.strip().lower()] = v.strip().strip('"')
                return out
    return None


def _digest_response(username: str, password: str, realm: str, nonce: str, method: str, uri: str) -> str:
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()


def build_rtsp_url(
    ip: str,
    port: int,
    channel: int,
    *,
    vendor: Vendor | str,
    subtype: int,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Build vendor-specific RTSP URL. Credentials are optional (used by both
    the probe and MediaMTX path generation)."""
    from urllib.parse import quote

    vendor_str = vendor.value if isinstance(vendor, Vendor) else (vendor or "dahua").lower()
    if vendor_str == "hikvision":
        # stream 1 = main, 2 = sub
        stream = 1 if subtype == 0 else 2
        path = f"/Streaming/Channels/{channel * 100 + stream}"
    else:
        path = f"/cam/realmonitor?channel={channel}&subtype={subtype}"

    auth = ""
    if username is not None and password is not None:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return f"rtsp://{auth}{ip}:{port}{path}"


def probe_rtsp(
    ip: str,
    port: int,
    username: str,
    password: str,
    *,
    channel: int = 1,
    vendor: Vendor | str = Vendor.dahua,
    timeout: float = 5.0,
    tag: str = "",
) -> ProbeResult:
    """Send OPTIONS + digest auth to the NVR. Returns ProbeResult."""
    uri = build_rtsp_url(ip, port, channel, vendor=vendor, subtype=1)  # probe via sub-stream
    method = "OPTIONS"
    tag = tag or f"[{ip}:{port}]"

    sock = None
    try:
        log.debug("%s connecting...", tag)
        sock = socket.create_connection((ip, port), timeout=timeout)

        req1 = f"{method} {uri} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: DSS\r\n\r\n"
        sock.sendall(req1.encode())
        resp1 = sock.recv(4096).decode(errors="replace")
        status1, first1 = _parse_status(resp1)

        if status1 == 200:
            return ProbeResult(True, "OK (no auth)")
        if status1 == 403:
            log.warning("%s 403 — IP banned by NVR", tag)
            return ProbeResult(False, "Forbidden (IP banned by NVR)", banned=True,
                               banned_cooldown=DEFAULT_BAN_COOLDOWN)
        if status1 != 401:
            return ProbeResult(False, f"Unexpected: {first1}")

        digest = _parse_digest(resp1)
        if not digest or "realm" not in digest or "nonce" not in digest:
            return ProbeResult(False, "Authentication failed (no digest challenge)")

        response_hash = _digest_response(
            username, password, digest["realm"], digest["nonce"], method, uri
        )
        auth_header = (
            f'Digest username="{username}", realm="{digest["realm"]}", '
            f'nonce="{digest["nonce"]}", uri="{uri}", response="{response_hash}"'
        )
        req2 = (
            f"{method} {uri} RTSP/1.0\r\nCSeq: 2\r\nUser-Agent: DSS\r\n"
            f"Authorization: {auth_header}\r\n\r\n"
        )
        sock.sendall(req2.encode())
        resp2 = sock.recv(4096).decode(errors="replace")
        status2, first2 = _parse_status(resp2)

        if status2 == 200:
            log.info("%s AUTH OK", tag)
            return ProbeResult(True, "OK")
        if status2 == 401:
            log.warning("%s wrong password", tag)
            return ProbeResult(False, "Authentication failed (wrong password)")
        if status2 == 403:
            log.warning("%s 403 — IP banned by NVR", tag)
            return ProbeResult(False, "Forbidden (IP banned by NVR)", banned=True,
                               banned_cooldown=DEFAULT_BAN_COOLDOWN)
        return ProbeResult(False, f"Unexpected: {first2}")

    except socket.timeout:
        return ProbeResult(False, "Connection timeout (NVR unreachable)")
    except ConnectionRefusedError:
        return ProbeResult(False, "Connection refused (RTSP port closed)")
    except OSError as e:
        return ProbeResult(False, f"Network error: {e}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def tcp_reachable(ip: str, port: int = 554, timeout: float = 3.0) -> tuple[bool, str]:
    """Quick TCP-connect probe — does NOT send credentials."""
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        sock.close()
        return True, "Reachable"
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return False, str(e)
