# Per-camera main-stream source toggle (direct ⇄ NVR)

**Date:** 2026-06-16
**Status:** approved design

## Goal

In fullscreen, the main stream should default to **direct from the camera**
(0 packet loss — proven: nvr15 ch1 relay lost 1594 RTP pkts vs 0 direct), while
keeping the **NVR relay** available as a one-click alternative per camera. The
operator's choice is remembered per camera.

Scope: the toggle affects **only the main stream** (fullscreen). The grid uses
the sub stream, which always comes via the NVR and relays cleanly — nothing to
toggle there.

## Decisions (from brainstorming)

- **Control:** per-camera button in the fullscreen overlay. Default = direct.
- **Persistence:** remembered per camera in browser `localStorage`.
- **Relay-only cameras** (no reachable direct IP): button **hidden** — they
  already play via NVR and there's no alternative.
- **Placement:** a small control in the existing fullscreen control bar.

## Approach (chosen)

**Dual on-demand MediaMTX paths + frontend toggle.**

Both paths are `sourceOnDemand`, so the alternate costs nothing unless selected.
Per-operator and race-free (each browser picks its own path; no shared state).

Rejected alternatives:
- *Backend swaps one path's source on toggle* — global, slow, racy; yanks every
  operator's view. No.
- *Frontend-only* — impossible; MediaMTX must have the path defined.

## Backend (`backend/app/services/path_sync.py`)

For each enabled camera with `has_main`:

| `camera.ip` | paths emitted |
|---|---|
| set (direct reachable) | `{nvr}_ch{N}_main` → **direct** (camera IP, ch1) **and** `{nvr}_ch{N}_main_nvr` → **relay** (NVR, chN) |
| `None` | `{nvr}_ch{N}_main` → **relay** only (as today); no `_main_nvr` |

Changes:
- `_desired_paths`: when `cam.ip` is set, also emit the `_main_nvr` relay variant.
- `_build_path_config`: add a `force_relay: bool` (or a small variant builder) so
  the `_main_nvr` path always uses the NVR source even when `camera.ip` is set.
- `path_name`: support the `_main_nvr` suffix.
- `_is_dss_managed`: recognize `…_ch{N}_main_nvr` as DSS-managed (so reconcile
  creates/patches/cleans it and does not treat it as a foreign orphan).

## Watchdog (`backend/app/services/source_watch.py`)

`_parse_path` must map `…_ch{N}_main_nvr` to the same `(nvr_id, channel)` as
`_main`, so per-NVR / per-channel failure accounting is unaffected by the new
path name. (A `_main_nvr` path that is unready while pulled should count toward
the same channel, exactly like `_main`.)

## Frontend (`web/js/fullscreen.js` + control bar)

- Source resolver: `mainPath = path + (source === "nvr" ? "_main_nvr" : "_main")`.
  Default `source = "direct"`.
- Toggle control in the fullscreen control bar, **shown only when the camera has
  a direct IP** (the camera record already carries `ip` via `CameraRead`; the
  frontend's path→camera side-table exposes it).
- On click: flip `direct ⇄ nvr`, persist, tear down the current main connection
  and reconnect to the resolved path. Update the control's label/state.
- On fullscreen open: read the saved choice; if `"nvr"`, start on `_main_nvr`.

### Persistence

`localStorage["dss.mainSource"]` = JSON map `{ [camPath]: "direct" | "nvr" }`.
Absent / unknown ⇒ `"direct"`. A tiny module (`getMainSource(path)` /
`setMainSource(path, v)`) wraps read/write with safe JSON parse.

## Behaviour / edge cases

- Existing HLS-fallback + reconnect logic is unchanged and applies to whichever
  source is active.
- Switching is **manual only** — no auto-fallback from direct→NVR on a blip
  (YAGNI; can add later if operators want it).
- Steady state unchanged: default direct opens a camera session; the NVR variant
  opens an NVR session only if toggled — no extra standing NVR load, connection
  cap unaffected.

## Testing

- **Backend (pytest):**
  - camera with `ip` ⇒ `_desired_paths` contains both `…_main` (source = camera
    IP) and `…_main_nvr` (source = NVR IP).
  - camera without `ip` ⇒ only `…_main` (source = NVR), no `…_main_nvr`.
  - `_is_dss_managed("nvrX_ch3_main_nvr") is True`.
  - watchdog `_parse_path("nvrX_ch3_main_nvr")` → `(nvrX, 3)`.
- **Frontend:** unit-test the source resolver + `localStorage` wrapper (default
  direct, persisted nvr, malformed JSON → direct). Fullscreen toggle verified
  manually (no fullscreen test harness exists).

## Out of scope

- Grid-tile main streams (grid stays on sub).
- Auto-fallback / health-based source selection.
- Server-persisted (cross-device) preference — local per browser is enough.
</content>
