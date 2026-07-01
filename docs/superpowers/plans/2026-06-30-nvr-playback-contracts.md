# NVR Playback — Binding Contracts (Phases 2–3)

These resolve every cross-task ambiguity the planning pass surfaced. They are
**binding**: every implementer and reviewer treats them as the spec. Where they
conflict with the design doc's prose, **these win** (they encode the verified
spike findings + the user's RBAC decision of 2026-06-30).

1. **RBAC = per-camera.** All playback endpoints — `/index`, `/availability`,
   `/thumb`, and the `/stream` WS — authorize with
   `user_can_access_camera(user, camera)`, where `camera` is the `Camera` row
   matching `(nvr_id, channel)`. Admin bypasses. If the camera row is missing or
   not accessible → 404 (HTTP) / close `4004` (WS). **This supersedes the spec's
   "user_can_access_nvr" text and changes the already-shipped `/index` +
   `/availability`.** (User decision, 2026-06-30.)

2. **WS auth.** JWT comes from the `?token=` query param (browsers can't set WS
   headers), validated via `app.security.decode_token` **before**
   `websocket.accept()`. Close codes: `4001` unauthenticated, `4003` forbidden,
   `4004` NVR/camera not found or disabled, `4429` resource exhausted
   (NVR busy / lockout / global cap).

3. **`clock.wall_ts` = current footage epoch** (UTC seconds). The client sets
   `playhead = wall_ts` directly. (Field name is legacy; semantics = footage
   epoch. Backend sends `sess.footage_now()`.)

4. **`t0`** in `init`/`reinit` = footage epoch of the requested seek target.
   MVP does not parse the fMP4 TRUN box; error is bounded by one GOP (~0.5 s
   after re-encode) and the `clock` heartbeat corrects drift.

5. **`{stream}` is a main-only no-op.** The NVR records the 4 MP main only
   (spike V4). Backend silently ignores `{stream}`; the frontend shows **no**
   sub/quality toggle. Keep the message in the parser for forward-compat.

6. **`no_coverage` vs `end`.** `no_coverage` is a *frontend* state shown when
   `/index` returns zero clips for the selected day — the WS is **never opened**.
   At runtime, `{type:"gap", next:null}` or `{type:"eof"}` after the last clip
   → `end` state.

7. **Thumbnail.** `/thumb?at=<epoch>` exists (Task 9). The Timeline drag-preview
   uses it (throttled: fire on drag-settle/end, **not** every `pointermove`),
   never a live decode.

8. **`build_playback_url` percent-encodes `user` and `pw`** with
   `urllib.parse.quote(..., safe="")` — the new NVR's password contains `*`, and
   `@`/`:`/`/`/`?` would otherwise corrupt the RTSP authority. Include a test for
   `pw="pa@ss*word"`.

9. **Ports.** `nvr.port` (554) is the **RTSP** port for the playback URL. The
   HTTP CGI (`mediaFileFind`, Phase-1) is port **80**. Never mix them.

10. **ffmpeg I/O.** Sessions output **fMP4** (`-f mp4 -movflags
    frag_keyframe+empty_moov+default_base_moof pipe:1`); audio transcoded to
    **AAC**. One-shot snapshots pull **TCP** (reliability over speed). argv is
    always a **list** (no shell).

    The RTSP **transport is now selectable per playback session** via the WS
    `?transport=udp|tcp` query param (validated server-side; anything other
    than `"udp"`/`"tcp"` falls back to `"udp"`), threaded through
    `PlaybackSession.transport` → `_build_ffmpeg_argv(transport=...)` →
    `-rtsp_transport <transport>`. Frontend toggle: **Smooth = UDP** (default)
    vs **Clear = TCP**; toggling reopens the WS (same teardown/reconnect path
    as a seek/Retry) so the new transport takes effect on respawn.

    Verified 2026-07-01 on the 4MP NVR: **UDP** ≈ 0.73× realtime, near-live but
    **lossy** (occasional corruption); **TCP** is clean but ≈ 0.15× realtime
    (slow — buffers noticeably). TCP being slow is expected/by design for MVP;
    no realtime-clock changes were made for the TCP path.

11. **No orphan ffmpeg.** `close()` kills the process, `await`s it, and cancels
    the drain + stderr tasks. Windows: assign a **Job Object (kill-on-close)** on
    spawn. Lifespan shutdown closes all active sessions. Back-pressure: bounded
    ring buffer drops oldest chunks; the stdout reader **never** blocks on a slow
    WS client.

12. **Credential hygiene.** The password and the credentialed RTSP URL never
    appear in logs, error messages, or WS payloads — redact to `***`.

13. **Speed = backend-owned.** Server-side frame-decimation / I-frame-stride;
    `<video>.playbackRate` stays **1.0**; audio **muted when speed > 1**; speed
    whitelisted to `{1,2,4,8}`. Whether the NVR honors RTSP `Scale` for a cheaper
    2× is unmeasured (spike V1) — decimation is the baseline; `Scale` is a later
    optimization, not a dependency.

14. **`init.codec`.** The backend emits the **full MIME type** required by
    `MediaSource.addSourceBuffer()`: **`video/mp4; codecs="avc1.640032"`**
    (H.264 **High** profile, level 5.0). This is the ACTUAL avcC of the libx264
    re-encode — verified on-network against 192.168.20.15 (2026-07-01): the
    output is High profile, so the earlier `avc1.42E01E` (Baseline L3.0) was
    wrong and MSE could reject it. High L5.0 covers every camera here (≤4MP; a
    declared level ≥ the real level is accepted). Bare codec strings (no
    `video/mp4; codecs=` wrapper) are rejected. Follow-up: derive the string
    from the pinned init segment's avcC box for encoder-independence.

**Detailed per-task specs:** see
`2026-06-30-nvr-playback-phase2-tasks.md` (Tasks 5–10, backend) and
`2026-06-30-nvr-playback-phase3-tasks.md` (Tasks 11–15, frontend).
