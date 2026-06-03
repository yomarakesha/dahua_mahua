"""LAN discovery for NVRs / cameras.

Two complementary strategies. Both target IP-based devices that expose an
RTSP server on the LAN:

  1. **ONVIF WS-Discovery** (`ws_discovery`)
     UDP multicast probe to 239.255.255.250:3702, asking
     `dn:NetworkVideoTransmitter`. Devices respond with their HTTP service
     URL (XAddrs) and a list of Scopes that usually contain manufacturer +
     model. Fast (~3s), vendor-agnostic, but only catches devices that
     speak ONVIF and live in the same L2 multicast domain.

  2. **TCP /24 scan** (`tcp_scan`)
     Brute-force TCP connect to a port (default 554 RTSP) on every host in
     the given CIDR. Catches devices with ONVIF disabled, devices on a
     different L2 segment (as long as L3 routes to it), and rebadged
     no-name hardware that ignores WS-Discovery.

After we know *where* a device is, `detect_dahua_channels` tries to learn
*how many* channels it has via Dahua's `magicBox` HTTP CGI (digest auth).
Failure modes — non-Dahua, wrong credentials, firmware variations — fall
back to the caller-supplied default (16 in the router).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import uuid
from dataclasses import dataclass, field
from typing import Iterable

import httpx

log = logging.getLogger("dss.discovery")

WS_DISCOVERY_GROUP = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

# Minimal SOAP envelope. The vast majority of Dahua / Hikvision / Axis /
# Hanwha devices answer this exact body.
_PROBE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{msg_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""


@dataclass(slots=True)
class Candidate:
    """One discovered host. `source` records which strategy found it so the
    UI can show why an entry showed up."""
    ip: str
    port: int = 554
    sources: list[str] = field(default_factory=list)
    vendor_guess: str = "dahua"  # default — most of our fleet is Dahua
    xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    label_hint: str | None = None


# ── ONVIF WS-Discovery ──────────────────────────────────────────────────────


def _ws_discovery_sync(timeout: float, bind_iface: str | None) -> list[tuple[str, str]]:
    """Blocking multicast probe. Returns raw (peer_ip, soap_xml) tuples.

    Called inside a thread via `asyncio.to_thread` so the event loop stays
    responsive while we sit on `recvfrom`.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    # Bind to 0 so the kernel picks a port; binding the address forces the
    # outbound interface on multi-homed hosts (useful when org LAN and a
    # docker bridge are both present).
    sock.bind((bind_iface or "0.0.0.0", 0))
    sock.settimeout(timeout)

    msg_id = uuid.uuid4()
    probe = _PROBE_TEMPLATE.format(msg_id=msg_id).encode("utf-8")
    try:
        sock.sendto(probe, (WS_DISCOVERY_GROUP, WS_DISCOVERY_PORT))
    except OSError as e:
        log.warning("WS-Discovery send failed: %s", e)
        sock.close()
        return []

    responses: list[tuple[str, str]] = []
    # Loop until socket timeout fires (i.e. silence for `timeout` seconds).
    while True:
        try:
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            break
        except OSError as e:
            log.warning("WS-Discovery recv failed: %s", e)
            break
        try:
            responses.append((addr[0], data.decode("utf-8", errors="replace")))
        except Exception:  # noqa: BLE001
            continue
    sock.close()
    return responses


_XADDRS_RE = re.compile(r"<[^:>]*:?XAddrs>([^<]+)</[^:>]*:?XAddrs>", re.IGNORECASE)
_SCOPES_RE = re.compile(r"<[^:>]*:?Scopes[^>]*>([^<]+)</[^:>]*:?Scopes>", re.IGNORECASE)


def _parse_probe_match(xml: str) -> tuple[list[str], list[str]]:
    """Pull XAddrs (HTTP service URLs) and Scopes (URI list with vendor /
    model info) out of a ProbeMatch reply. Regex parsing — proper SOAP is
    overkill and pulls a heavy dep for a one-shot."""
    xaddrs: list[str] = []
    scopes: list[str] = []
    for m in _XADDRS_RE.finditer(xml):
        xaddrs.extend(m.group(1).split())
    for m in _SCOPES_RE.finditer(xml):
        scopes.extend(m.group(1).split())
    return xaddrs, scopes


def _vendor_from_scopes(scopes: Iterable[str]) -> str:
    """Best-effort: ONVIF scopes encode `onvif://www.onvif.org/name/<NAME>`
    where NAME is the manufacturer. Dahua and Hikvision both follow this."""
    joined = " ".join(scopes).lower()
    if "hikvision" in joined or "hangzhou hikvision" in joined:
        return "hikvision"
    if "dahua" in joined:
        return "dahua"
    return "dahua"  # safe default for our fleet


async def ws_discovery(
    timeout: float = 3.0,
    bind_iface: str | None = None,
) -> dict[str, Candidate]:
    """Run the multicast probe and parse replies."""
    raw = await asyncio.to_thread(_ws_discovery_sync, timeout, bind_iface)
    out: dict[str, Candidate] = {}
    for peer_ip, xml in raw:
        if "probematch" not in xml.lower():
            continue
        xaddrs, scopes = _parse_probe_match(xml)
        c = out.setdefault(peer_ip, Candidate(ip=peer_ip))
        if "onvif" not in c.sources:
            c.sources.append("onvif")
        c.xaddrs = xaddrs
        c.scopes = scopes
        c.vendor_guess = _vendor_from_scopes(scopes)
        # Try to extract a friendly label from `name/...` scope, e.g.
        # onvif://www.onvif.org/name/DH-NVR4216-4KS2
        for s in scopes:
            if "/name/" in s.lower():
                c.label_hint = s.rsplit("/name/", 1)[-1]
                break
    log.info("WS-Discovery found %d hosts", len(out))
    return out


# ── TCP CIDR scan ───────────────────────────────────────────────────────────


async def _tcp_probe(ip: str, port: int, timeout: float) -> bool:
    try:
        fut = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def tcp_scan(
    cidr: str,
    ports: list[int] | None = None,
    timeout: float = 0.6,
    concurrency: int = 128,
) -> dict[str, Candidate]:
    """Scan every host in `cidr` (e.g. "192.168.1.0/24") on `ports`. Returns
    candidates for each IP that answers on at least one port."""
    ports = ports or [554]
    network = ipaddress.ip_network(cidr, strict=False)
    if network.num_addresses > 4096:
        raise ValueError(f"CIDR too large ({network.num_addresses} hosts); cap at /20")

    sem = asyncio.Semaphore(concurrency)
    found: dict[str, Candidate] = {}

    async def _one(ip: str, port: int) -> None:
        async with sem:
            if await _tcp_probe(ip, port, timeout):
                c = found.setdefault(ip, Candidate(ip=ip, port=port))
                if "tcp" not in c.sources:
                    c.sources.append("tcp")

    targets = [
        (str(host), port)
        for host in network.hosts()
        for port in ports
    ]
    await asyncio.gather(*[_one(ip, p) for ip, p in targets])
    log.info("TCP scan %s found %d hosts on ports %s", cidr, len(found), ports)
    return found


# ── Dahua channel autodetect ────────────────────────────────────────────────


_DAHUA_CHANNEL_RE = re.compile(
    r"(?:maxRemoteInputChannels|MaxChannel|MaxNum|VideoInChannel)\s*=\s*(\d+)",
    re.IGNORECASE,
)


async def detect_dahua_channels(
    ip: str,
    username: str,
    password: str,
    timeout: float = 3.0,
) -> int | None:
    """Probe Dahua's magicBox CGI for the channel count.

    Returns None if the device isn't Dahua, creds are wrong, or HTTP isn't
    reachable on :80. Callers should fall back to a sensible default (16).
    """
    base = f"http://{ip}"
    # Two CGI variants — different firmwares expose different keys, so we
    # try both and union the numbers we find.
    endpoints = [
        "/cgi-bin/magicBox.cgi?action=getProductDefinition",
        "/cgi-bin/devVideoInput.cgi?action=getCaps&channel=0",
    ]
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            auth=httpx.DigestAuth(username, password),
            # NVRs live on the LAN. Don't route requests through the user's
            # HTTP_PROXY (Windows system proxy bites us in `mediamtx_api.py`
            # the same way — see trust_env=False there).
            trust_env=False,
        ) as client:
            channels: list[int] = []
            for ep in endpoints:
                try:
                    r = await client.get(base + ep)
                except httpx.HTTPError as e:
                    log.debug("Dahua %s %s failed: %s", ip, ep, e)
                    continue
                if r.status_code != 200:
                    continue
                channels.extend(int(m.group(1)) for m in _DAHUA_CHANNEL_RE.finditer(r.text))
            if channels:
                # Trust the largest match — when both keys are present we want
                # `maxRemoteInputChannels`, which is the higher number.
                return max(channels)
    except Exception as e:  # noqa: BLE001
        log.debug("Dahua channel probe %s errored: %s", ip, e)
    return None


# ── Helpers ─────────────────────────────────────────────────────────────────


def default_cidr() -> str | None:
    """Guess the LAN /24 from the primary outbound interface. Works without
    netifaces by opening a UDP socket to a public IP — the kernel binds it
    to the route's source address but doesn't actually send anything."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("1.1.1.1", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        return str(net)
    except OSError:
        return None
