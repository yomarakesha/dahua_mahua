"""Fleet-wide main-stream encoder normaliser for Dahua cameras.

WHY THIS EXISTS
---------------
`netcheck` proved the Dahua NVR's RTSP relay drops packets on *main* streams
(7815 lost via-NVR vs 0 direct — see netcheck-result.md / docs/audit-plan.md §9).
`path_sync` already pulls main straight from each camera when `Camera.ip` is
known. This tool fixes the *other half* of the problem: the cameras' own main
encoder settings.

Dahua "Smart Codec" (H.264+/H.265+) is the biggest device-side cause of the
stutter/freeze: it only operates in **VBR** mode and produces long-GOP,
bursty streams that standard players (and MediaMTX's relay) handle poorly.
Forcing **CBR** neutralises Smart Codec without needing a firmware-specific
"smart" key, and shortening **GOP to ~1×FPS** means a dropped frame freezes
the picture for a fraction of a second instead of seconds. Both are standard,
well-documented Encode keys and are trivially reversible.

WHAT IT DOES (per enabled Dahua NVR)
------------------------------------
  1. Pull the NVR's connected-camera list over HTTP CGI (RemoteDevice) — reuses
     `camera_import.fetch_camera_ips`, the same call the app already trusts.
  2. Connect to each camera *directly* (camera creds mirror the NVR's on this
     fleet) and read its `Encode` config.
  3. AUDIT (default): print each camera's main-stream Compression / BitRateControl
     / BitRate / FPS / GOP so you can see which cameras have Smart Codec on.
  4. APPLY (`--apply`): set `MainFormat[0].Video.BitRateControl=CBR` and
     `GOP=<FPS>` (and `--bitrate` if given), then re-read to verify.

REACHABILITY (important)
------------------------
This tool only helps cameras that are **directly reachable** on HTTP :80 — i.e.
the same cameras for which `apply_camera_ips` (reachability-aware) sets a direct
`Camera.ip`. Live audit 2026-06-16: the 17 reachable `192.168.20.x` cameras of
nvr15 qualify; cameras behind an NVR PoE switch / on dead `192.168.23.x` do NOT
answer and are simply reported as unreachable and skipped (their main stays on
the NVR relay, which must be tuned via the NVR's own web UI instead).

SAFETY
------
  • Dry-run (audit) by default. `--apply` is required to write anything.
  • It only ever changes MainFormat[0] (the main stream). Sub/ExtraFormat is
    left alone — the NVR relays sub cleanly.
  • Test on ONE camera first: `--only 192.168.20.101 --apply`.
  • Reversible: set BitRateControl back to VBR on the camera to restore the
    previous behaviour.
  • Runs on the prod server (uses the DSS DB + NVR_SECRET_KEY for real creds and
    must be on a network that can reach both the NVRs and the camera subnet).

USAGE
-----
    python -m app.fleet_tune                                  # audit whole fleet
    python -m app.fleet_tune --nvr nvr-192-168-20-15          # audit one NVR
    python -m app.fleet_tune --only 192.168.20.101 --apply    # tune one camera
    python -m app.fleet_tune --apply --gop-factor 1.0         # tune whole fleet
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from app.crypto import decrypt_password
from app.db import SessionLocal
from app.models import Nvr, Vendor
from app.services.camera_import import fetch_camera_ips

log = logging.getLogger("dss.fleet_tune")

# One line per Encode field, e.g.
#   table.Encode[0].MainFormat[0].Video.BitRateControl=VBR
_ENCODE_RE = re.compile(
    r"table\.Encode\[(\d+)\]\.MainFormat\[0\]\.Video\.(\w+)=(.*)"
)


@dataclass(slots=True)
class MainEncode:
    """Parsed main-stream video settings for one camera channel index."""
    channel_index: int
    compression: str = ""
    bitrate_control: str = ""
    bitrate: str = ""
    fps: str = ""
    gop: str = ""

    @property
    def smart_codec_active(self) -> bool:
        """Smart Codec only runs in VBR. VBR on main is the thing we fix."""
        return self.bitrate_control.upper() == "VBR"

    def summary(self) -> str:
        flag = "  ⚠ SMART/VBR" if self.smart_codec_active else ""
        return (
            f"ch_idx={self.channel_index} {self.compression or '?'} "
            f"{self.bitrate_control or '?'} {self.bitrate or '?'}kbps "
            f"fps={self.fps or '?'} gop={self.gop or '?'}{flag}"
        )


def parse_encode(text: str) -> dict[int, MainEncode]:
    """Parse a `getConfig&name=Encode` dump into {channel_index: MainEncode}."""
    out: dict[int, MainEncode] = {}
    for line in text.splitlines():
        m = _ENCODE_RE.match(line.strip())
        if not m:
            continue
        idx, key, val = int(m.group(1)), m.group(2), m.group(3).strip()
        enc = out.setdefault(idx, MainEncode(channel_index=idx))
        if key == "Compression":
            enc.compression = val
        elif key == "BitRateControl":
            enc.bitrate_control = val
        elif key == "BitRate":
            enc.bitrate = val
        elif key == "FPS":
            # FPS can come back as a float string ("25.000000")
            enc.fps = val.split(".")[0]
        elif key == "GOP":
            enc.gop = val.split(".")[0]
    return out


async def _get_encode(
    client: httpx.AsyncClient, ip: str, auth: httpx.DigestAuth
) -> dict[int, MainEncode]:
    url = f"http://{ip}/cgi-bin/configManager.cgi?action=getConfig&name=Encode"
    r = await client.get(url, auth=auth)
    r.raise_for_status()
    return parse_encode(r.text)


async def _set_main_encode(
    client: httpx.AsyncClient,
    ip: str,
    auth: httpx.DigestAuth,
    channel_index: int,
    *,
    gop: int | None,
    bitrate: int | None,
) -> None:
    """Force CBR (kills Smart Codec) and optionally GOP / BitRate on the main
    stream of one camera channel index."""
    base = f"Encode[{channel_index}].MainFormat[0].Video"
    params = [f"{base}.BitRateControl=CBR"]
    if gop is not None:
        params.append(f"{base}.GOP={gop}")
    if bitrate is not None:
        params.append(f"{base}.BitRate={bitrate}")
    url = (
        f"http://{ip}/cgi-bin/configManager.cgi?action=setConfig&"
        + "&".join(params)
    )
    r = await client.get(url, auth=auth)
    r.raise_for_status()
    if "OK" not in r.text:
        raise RuntimeError(f"setConfig did not return OK: {r.text.strip()[:120]}")


async def _process_camera(
    ip: str,
    username: str,
    password: str,
    *,
    apply: bool,
    gop_factor: float,
    bitrate: int | None,
    timeout: float,
) -> tuple[bool, str]:
    """Audit (and optionally tune) one camera. Returns (changed, message)."""
    auth = httpx.DigestAuth(username, password)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            encodes = await _get_encode(client, ip, auth)
        except httpx.HTTPError as e:
            return False, f"{ip}: unreachable / auth failed ({e!r})"

        # A standalone IP camera reports a single channel at index 0.
        enc = encodes.get(0) or (next(iter(encodes.values())) if encodes else None)
        if enc is None:
            return False, f"{ip}: no Encode config returned"

        if not apply:
            return False, f"{ip}: {enc.summary()}"

        # Decide target GOP from the camera's own FPS, so we don't impose a
        # frame rate — only shorten the keyframe interval.
        target_gop: int | None = None
        if enc.fps.isdigit() and gop_factor > 0:
            target_gop = max(1, round(int(enc.fps) * gop_factor))

        try:
            await _set_main_encode(
                client, ip, auth, enc.channel_index,
                gop=target_gop, bitrate=bitrate,
            )
            verify = await _get_encode(client, ip, auth)
        except (httpx.HTTPError, RuntimeError) as e:
            return False, f"{ip}: APPLY FAILED ({e!r})  [was: {enc.summary()}]"

        after = verify.get(enc.channel_index)
        ok = after is not None and not after.smart_codec_active
        status = "OK" if ok else "DID NOT STICK"
        return ok, (
            f"{ip}: tuned [{status}]\n"
            f"        before: {enc.summary()}\n"
            f"        after:  {after.summary() if after else '?'}"
        )


async def run(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        nvrs = list(
            (
                await session.execute(
                    select(Nvr).where(Nvr.enabled.is_(True), Nvr.vendor == Vendor.dahua)
                )
            ).scalars()
        )
    if args.nvr:
        nvrs = [n for n in nvrs if n.id == args.nvr]
        if not nvrs:
            print(f"No enabled Dahua NVR with id={args.nvr!r}", file=sys.stderr)
            return 2

    mode = "APPLY (writing CBR + GOP)" if args.apply else "AUDIT (read-only)"
    print(f"=== fleet_tune — {mode} — {len(nvrs)} NVR(s) ===\n")

    total_cams = 0
    total_changed = 0
    total_smart = 0
    for nvr in nvrs:
        password = decrypt_password(nvr.rtsp_password_encrypted)
        print(f"── {nvr.id} ({nvr.label}) @ {nvr.ip} ──")
        try:
            chan_ips = await fetch_camera_ips(nvr.ip, nvr.rtsp_username, password)
        except Exception as e:  # noqa: BLE001
            print(f"   ! could not fetch camera list from NVR: {e!r}\n")
            continue
        if not chan_ips:
            print("   (NVR reported no camera IPs)\n")
            continue

        # Optionally narrow to a single camera IP for safe first-run testing.
        targets = sorted(set(chan_ips.values()))
        if args.only:
            targets = [ip for ip in targets if ip == args.only]
            if not targets:
                continue

        results = await asyncio.gather(*[
            _process_camera(
                ip, nvr.rtsp_username, password,
                apply=args.apply, gop_factor=args.gop_factor,
                bitrate=args.bitrate, timeout=args.timeout,
            )
            for ip in targets
        ])
        for ip, (changed, msg) in zip(targets, results):
            total_cams += 1
            if changed:
                total_changed += 1
            if "SMART/VBR" in msg or "⚠" in msg:
                total_smart += 1
            print(f"   {msg}")
        print()

    print("=== summary ===")
    print(f"cameras seen:        {total_cams}")
    if args.apply:
        print(f"cameras tuned (CBR): {total_changed}")
    else:
        print(f"with Smart Codec/VBR on main (need tuning): {total_smart}")
        print("\nRe-run with --apply to force CBR + short GOP on the main stream.")
        print("Test one camera first:  python -m app.fleet_tune --only <camera_ip> --apply")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        prog="python -m app.fleet_tune",
        description="Audit/normalise Dahua camera MAIN-stream encoders (kill Smart Codec).",
    )
    p.add_argument("--apply", action="store_true",
                   help="Actually write changes (default: audit/read-only).")
    p.add_argument("--nvr", metavar="ID",
                   help="Limit to a single NVR id (e.g. nvr11).")
    p.add_argument("--only", metavar="CAMERA_IP",
                   help="Limit to a single camera IP — use this to test --apply safely.")
    p.add_argument("--gop-factor", type=float, default=1.0,
                   help="Target GOP = round(FPS * factor). 1.0 = keyframe every second (default).")
    p.add_argument("--bitrate", type=int, default=None, metavar="KBPS",
                   help="Also set a fixed main BitRate in kbps (default: leave as-is).")
    p.add_argument("--timeout", type=float, default=8.0,
                   help="Per-camera HTTP timeout in seconds (default: 8).")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
