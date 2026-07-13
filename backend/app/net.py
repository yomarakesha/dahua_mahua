"""LAN IPv4 detection helpers (stdlib only — no netifaces/psutil dependency).

Used by two portability features:
  • the startup banner (which address a client connects the desktop app to), and
  • go2rtc WebRTC ICE candidate auto-detection (app/services/go2rtc_config.py).

Deliberately simple and dependency-free. Two strategies, combined and
de-duplicated (primary first):

  1. The UDP-socket trick — open a UDP socket and ``connect()`` it to a public
     address. No packet is actually sent (UDP connect just fixes the peer), but
     the OS picks the source interface it *would* route through, and
     ``getsockname()`` then reveals that interface's local IP. This reliably
     yields the box's primary outbound LAN IP on a normal single-gateway host.
  2. Enumerate every resolved IPv4 for the hostname and keep the private-LAN
     ones (10./172.16-31./192.168.). Catches extra NICs the UDP trick misses.

Caveat (documented for operators): on a multi-NIC box the UDP-trick "primary"
is whichever interface holds the default route — that may NOT be the
viewer-facing LAN if the camera network is the default route. In that case set
GO2RTC_WEBRTC_CANDIDATES explicitly.
"""

from __future__ import annotations

import ipaddress
import socket


def _is_private_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        isinstance(addr, ipaddress.IPv4Address)
        and addr.is_private
        and not addr.is_loopback
        and not addr.is_link_local  # drop 169.254.x.x APIPA
    )


def _primary_via_udp() -> str | None:
    """Primary outbound LAN IPv4 via the UDP-connect trick (no packet sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 is just a routable target to select the outbound interface;
        # UDP connect sends nothing, so this works fully offline too.
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ip if _is_private_ipv4(ip) else None


def _enumerate_via_hostname() -> list[str]:
    """Private-LAN IPv4s resolved from the local hostname."""
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_private_ipv4(ip) and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def detect_lan_ipv4s() -> list[str]:
    """All private-LAN IPv4 addresses of this box, primary (default-route) first.

    Returns an empty list only if the box has no private IPv4 at all (unusual);
    callers should degrade gracefully (e.g. the banner prints localhost).
    """
    ips: list[str] = []
    primary = _primary_via_udp()
    if primary:
        ips.append(primary)
    for ip in _enumerate_via_hostname():
        if ip not in ips:
            ips.append(ip)
    return ips


def primary_lan_ipv4() -> str | None:
    """The single best-guess viewer-facing LAN IPv4, or None if undetectable."""
    ips = detect_lan_ipv4s()
    return ips[0] if ips else None
