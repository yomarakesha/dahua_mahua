# STABLE — working 4MP, no-pulse, no-freeze (on a clean link)

**Status:** ✅ Stable / working. Fullscreen **native 4MP** mains (2560×1440, verified),
**no re-encode pulse**, smooth on a clean wired LAN link. Low-res grid (subs) re-encoded.

This is the known-good checkpoint. Branch `stable`, tag `stable-4mp-no-freeze`.
Rollback-before-this-line point: branch `backup/pre-udp-ee5e419`.

## Architecture (why it's stable)
- **Relay:** go2rtc on the server (the server's path to the cameras is clean, 0% loss).
- **Mains:** RAW passthrough, direct from each camera IP — **native 4MP, the camera's own
  encode → no GOP-breathing pulse.** (Re-encode was a crutch for the freeze, which was
  actually the client link — so we dropped it on mains.)
- **Subs (grid):** re-encoded to a 0.5s GOP (small, keeps the grid smooth).
- **Transport to browser:** MSE over WebSocket = **TCP** (retransmits hide link loss;
  WebRTC/UDP was tried and shredded frames on a lossy link — do NOT use it here).
- **Player:** resolution-aware live cushion (4s main / 3s sub) + gentle ±8% catch-up.

## Working server config (`backend/.env` — non-secret keys)
```
REENCODE_ENABLED=true
REENCODE_QUALITIES=sub            # mains = RAW native 4MP; subs re-encoded
REENCODE_VCODEC=auto              # → libx264 here (no GPU); used only for subs
REENCODE_MAIN_SCALE=              # EMPTY = no downscale = native 4MP
REENCODE_INPUT_RTSP_TRANSPORT=tcp # server→camera path is clean; keep tcp on the server
GO2RTC_CONFIG_PATH=C:\deploy\dahua_mahua\.go2rtc\go2rtc.yaml
```
After changing `.env`, restart the backend so it reconciles, then **hard-restart go2rtc**
(POST /api/restart does not reload the stream registry — see start.ps1).

## Verified
- `ch1_main` probed: `h264 2560×1440` (native 4MP), raw passthrough.
- Delivery to a clean-link client: ~4 Mbps, first-byte ~1.3s, **0 stalls**, no pulse.

## Known caveats (NOT this version's fault — environmental)
1. **Residual freezes = the viewing client's intermittent link.** The test Mac's USB
   ethernet (AX88179B @ 100baseTX) drops packets in bursts; clean link = smooth, lossy
   blip = freeze. Fix = a healthy gigabit NIC / cable / switch port, not code.
2. **ch18–ch32 mains pull via the NVR** (those cameras' direct IPs/credentials —
   `post2626…` for most, 3 unknown — aren't in the DSS DB yet). They're 4MP but
   freeze-prone on the NVR's packet drops until provisioned with per-camera creds.
