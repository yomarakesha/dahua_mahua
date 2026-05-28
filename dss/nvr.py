"""
NVR-related logic:
  • IP-lockout tracking (Dahua/Hikvision block clients after N failed RTSP auths)
  • Event log (JSONL, rotated when >2MB)
  • RTSP health check with digest authentication
  • Inventory validation

Lockouts are server-side cool-downs that prevent repeated hammering of an NVR
that has banned us — they protect us, not the NVR. The actual ban lives in the
NVR's firmware; we mirror it so we don't keep poking after a 403.
"""

import hashlib
import json
import socket
import time

from .config import EVENT_LOG, EVENT_LOG_MAX_LINES, DEFAULT_BAN_COOLDOWN, LOCKOUTS_FILE, log


# ── Lockout tracking ─────────────────────────────────────────────────────────

# ip -> { "banned_at": float, "cooldown": int }
nvr_lockouts = {}


def _save_lockouts():
    """Persist lockouts to disk so a server restart doesn't lose active bans."""
    try:
        LOCKOUTS_FILE.write_text(json.dumps(nvr_lockouts, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("Failed to persist lockouts: %s", e)


def load_lockouts():
    """Load persisted lockouts on startup; drop entries that have already expired."""
    if not LOCKOUTS_FILE.exists():
        return
    try:
        raw = json.loads(LOCKOUTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to load lockouts file: %s", e)
        return
    now = time.time()
    restored = 0
    for ip, info in raw.items():
        if not isinstance(info, dict):
            continue
        banned_at = info.get("banned_at", 0)
        cooldown = info.get("cooldown", DEFAULT_BAN_COOLDOWN)
        if banned_at + cooldown > now:
            nvr_lockouts[ip] = {"banned_at": banned_at, "cooldown": cooldown}
            restored += 1
    if restored:
        log.info("Restored %d active NVR lockout(s) from disk", restored)
    if restored != len(raw):
        _save_lockouts()  # prune expired entries from file


def get_lockout_info(ip):
    """Returns (is_locked: bool, remaining_seconds: int, banned_until: float)."""
    info = nvr_lockouts.get(ip)
    if not info:
        return False, 0, 0
    elapsed = time.time() - info["banned_at"]
    cooldown = info.get("cooldown", DEFAULT_BAN_COOLDOWN)
    remaining = cooldown - elapsed
    if remaining <= 0:
        nvr_lockouts.pop(ip, None)
        _save_lockouts()
        return False, 0, 0
    return True, int(remaining), info["banned_at"] + cooldown


def record_lockout(ip, cooldown=None):
    nvr_lockouts[ip] = {
        "banned_at": time.time(),
        "cooldown": cooldown or DEFAULT_BAN_COOLDOWN,
    }
    _save_lockouts()


def clear_lockout(ip):
    if nvr_lockouts.pop(ip, None) is not None:
        _save_lockouts()


# ── Event log (JSONL) ────────────────────────────────────────────────────────

def log_nvr_event(nvr_id, ip, event_type, message=""):
    entry = {
        "ts": time.time(),
        "nvr_id": nvr_id,
        "ip": ip,
        "event": event_type,
        "message": message,
    }
    try:
        with open(EVENT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
        # Simple rotation: when file exceeds 2 MB, truncate to last N lines
        if EVENT_LOG.stat().st_size > 2 * 1024 * 1024:
            lines = EVENT_LOG.read_text().strip().split("\n")
            EVENT_LOG.write_text("\n".join(lines[-EVENT_LOG_MAX_LINES:]) + "\n")
    except OSError:
        pass


def read_events(nvr_id=None, limit=200):
    if not EVENT_LOG.exists():
        return []
    try:
        lines = EVENT_LOG.read_text().strip().split("\n")
        events = []
        for line in reversed(lines):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if nvr_id and entry.get("nvr_id") != nvr_id:
                continue
            events.append(entry)
            if len(events) >= limit:
                break
        return events
    except OSError:
        return []


# ── RTSP health / digest auth ────────────────────────────────────────────────

def _parse_rtsp_status(response):
    """Extract status code from RTSP response first line."""
    if not response:
        return 0, "No response"
    first_line = response.split("\r\n")[0]
    # e.g. "RTSP/1.0 401 Unauthorized"
    parts = first_line.split(None, 2)
    if len(parts) >= 2:
        try:
            return int(parts[1]), first_line
        except ValueError:
            pass
    return 0, first_line


def _parse_digest_challenge(response):
    """Extract digest auth parameters from a 401 response's WWW-Authenticate header."""
    for line in response.split("\r\n"):
        if line.lower().startswith("www-authenticate:"):
            value = line.split(":", 1)[1].strip()
            if value.lower().startswith("digest"):
                params = {}
                # Parse key="value" pairs
                for part in value[6:].split(","):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k.strip().lower()] = v.strip().strip('"')
                return params
    return None


def _compute_digest_response(username, password, realm, nonce, method, uri):
    """MD5 digest auth response (RFC 2069 / simplified RFC 2617)."""
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()


def _build_rtsp_test_uri(ip, port, channel, vendor):
    """Vendor-specific RTSP URI for OPTIONS health check (uses sub-stream)."""
    vendor = (vendor or "dahua").lower()
    if vendor == "hikvision":
        # Hikvision: channel*100 + 2 = sub-stream of given channel
        return f"rtsp://{ip}:{port}/Streaming/Channels/{channel * 100 + 2}"
    return f"rtsp://{ip}:{port}/cam/realmonitor?channel={channel}&subtype=1"


def test_nvr_rtsp(ip, port, username, password, channel=1, timeout=5,
                  nvr_id=None, vendor="dahua"):
    """Test RTSP connectivity with digest authentication.

    Returns (ok: bool, message: str, extra: dict).

    Flow:
      1. OPTIONS without auth → expect 401 with digest challenge.
      2. Compute digest response and resend → expect 200.
      3. Only record lockout on 403 (actual IP ban), NOT on 401.
    """
    extra = {}

    is_locked, remaining, banned_until = get_lockout_info(ip)
    if is_locked:
        mins = remaining // 60
        secs = remaining % 60
        extra["banned_until"] = banned_until
        extra["remaining"] = remaining
        return False, f"Locked out — retry in {mins}m {secs}s", extra

    uri = _build_rtsp_test_uri(ip, port, channel, vendor)
    method = "OPTIONS"
    tag = f"[{nvr_id or ip}:{port}]"

    sock = None
    try:
        log.debug("%s Connecting TCP...", tag)
        sock = socket.create_connection((ip, port), timeout=timeout)
        log.debug("%s TCP connected, sending OPTIONS (no auth)", tag)

        # Step 1: OPTIONS without auth
        req1 = f"{method} {uri} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: DSS\r\n\r\n"
        sock.sendall(req1.encode())
        resp1 = sock.recv(4096).decode(errors="replace")
        status1, first_line1 = _parse_rtsp_status(resp1)
        log.debug("%s Step1 response: %d — %s", tag, status1, first_line1)

        if status1 == 200:
            # No auth required
            clear_lockout(ip)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_ok", "Credential test passed (no auth)")
            return True, "OK", extra

        if status1 == 403:
            record_lockout(ip)
            extra["banned_until"] = time.time() + DEFAULT_BAN_COOLDOWN
            extra["remaining"] = DEFAULT_BAN_COOLDOWN
            if nvr_id:
                log_nvr_event(nvr_id, ip, "banned", "IP banned by NVR (403)")
            return False, "Forbidden (IP banned — too many failed attempts)", extra

        if status1 != 401:
            return False, f"Unexpected: {first_line1}", extra

        # Step 2: parse digest challenge
        digest = _parse_digest_challenge(resp1)
        log.debug("%s Digest challenge: %s", tag, digest)
        if not digest or "realm" not in digest or "nonce" not in digest:
            log.warning("%s 401 but no digest challenge in response", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_fail", "401 without digest challenge")
            return False, "Authentication failed (no digest challenge)", extra

        realm = digest["realm"]
        nonce = digest["nonce"]
        response_hash = _compute_digest_response(username, password, realm, nonce, method, uri)
        auth_header = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{response_hash}"'
        )

        log.debug("%s Sending OPTIONS with digest auth (realm=%s)", tag, realm)

        # Step 3: resend with digest auth
        req2 = (
            f"{method} {uri} RTSP/1.0\r\n"
            f"CSeq: 2\r\n"
            f"User-Agent: DSS\r\n"
            f"Authorization: {auth_header}\r\n"
            f"\r\n"
        )
        sock.sendall(req2.encode())
        resp2 = sock.recv(4096).decode(errors="replace")
        status2, first_line2 = _parse_rtsp_status(resp2)
        log.debug("%s Step2 response: %d — %s", tag, status2, first_line2)

        if status2 == 200:
            clear_lockout(ip)
            log.info("%s AUTH OK", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_ok", "Credential test passed")
            return True, "OK", extra
        if status2 == 401:
            log.warning("%s AUTH FAIL — wrong password (digest rejected)", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "auth_fail", "Wrong password (digest 401)")
            return False, "Authentication failed (wrong password)", extra
        if status2 == 403:
            record_lockout(ip)
            extra["banned_until"] = time.time() + DEFAULT_BAN_COOLDOWN
            extra["remaining"] = DEFAULT_BAN_COOLDOWN
            log.warning("%s BANNED by NVR (403)", tag)
            if nvr_id:
                log_nvr_event(nvr_id, ip, "banned", "IP banned by NVR (403)")
            return False, "Forbidden (IP banned — too many failed attempts)", extra

        log.warning("%s Unexpected response: %s", tag, first_line2)
        return False, f"Unexpected: {first_line2}", extra

    except socket.timeout:
        log.warning("%s Connection timeout", tag)
        return False, "Connection timeout (NVR unreachable)", extra
    except ConnectionRefusedError:
        log.warning("%s Connection refused", tag)
        return False, "Connection refused (RTSP port closed)", extra
    except OSError as e:
        log.warning("%s Network error: %s", tag, e)
        return False, f"Network error: {e}", extra
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


def check_nvr_reachable(ip, port=554, timeout=3):
    """Quick TCP-connect probe — does NOT send credentials."""
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        sock.close()
        return True, "Reachable"
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return False, str(e)


# ── Inventory validation ─────────────────────────────────────────────────────

def validate_inventory(data):
    """Return error string or None if inventory is well-formed."""
    if not isinstance(data, dict):
        return "Inventory must be a JSON object"
    if "global" not in data:
        return "Missing 'global' key"
    if "nvrs" not in data or not isinstance(data["nvrs"], list):
        return "Missing or invalid 'nvrs' array"
    valid_sources = ("nvr", "server", "")
    for i, nvr in enumerate(data["nvrs"]):
        if not isinstance(nvr, dict):
            return f"NVR #{i} is not an object"
        if not nvr.get("id"):
            return f"NVR #{i} missing 'id'"
        if not nvr.get("ip"):
            return f"NVR #{i} ({nvr.get('id', '?')}) missing 'ip'"
        if not isinstance(nvr.get("channels"), int) or nvr["channels"] < 1:
            return f"NVR #{i} ({nvr['id']}) 'channels' must be a positive integer"
        src = nvr.get("stream_source", "")
        if src and src not in valid_sources:
            return f"NVR #{i} ({nvr['id']}) invalid stream_source: {src}"
    return None
