# SmartPSS-parity — spike findings + plan (2026-07-03)

## Measured on-server (time to first playable fMP4 fragment)
| Stream | Cold | Warm | Warm again |
|---|---|---|---|
| direct-cam sub (ch2) | 2.6s | 1.0s | **0.5s** |
| testik sub (.39 via-NVR) | 5.1s | 0.67s | **0.48s** |
| direct-cam main (4MP) | 1.7s | 1.9s | 2.0s |

## Conclusions (binding for the implementation)
1. **A warm producer opens a SUB in ~0.5s (5–10× faster than cold).** go2rtc
   instant-starts an MSE client from its cached keyframe once the producer is
   connected. This is the core SmartPSS trick.
2. **Mains do NOT benefit from warming** (mpegts-pipe/exec path, no keyframe
   cache; stays ~2s). → Fullscreen shows the SUB instantly, upgrades to the 4MP
   main in the background (the ~2s becomes invisible).
3. Cameras+NVR run **GOP=50 @ 25fps = keyframe every 2s** — the cold-open tax.
   Halving sub GOP to 25 is a *camera-config* change (owner's call; not applied
   here).
4. **ONVIF SetSynchronizationPoint works** (HTTP 200, ok=True) — force-IDR is
   available but largely redundant once producers are warm; kept in reserve.

## Implementation
### Warm pool (backend, config-gated, DEFAULT OFF)
- New `app/services/warm_pool.py`: maintains a bounded set of server-side
  consumers (httpx drain of `http://127.0.0.1:1984/api/stream.mp4?src=<sub>`),
  one per desired SUB, with reconnect + a drop-grace when de-selected.
- **SUBS ONLY** (mains don't cache; warming them is wasted NVR load).
- **NvrBudget-aware + global cap** (`warm_pool_max_streams`, default 24): never
  exceed what an NVR can take (protects testik's concurrent-pull cap).
- `POST /api/v1/live/warm {camera_ids:[...]}` sets the desired set (auth
  required); backend diffs → start/stop warmers. Lifespan closes all on
  shutdown. `warm_pool_enabled=False` by default → endpoint 202-no-ops.
### Frontend
- **Instant fullscreen**: FullscreenView mounts the SUB first (warm → instant),
  starts the main hidden in parallel, cross-fades to the main when it reaches
  "live" (keeps audio on the main). Fall back gracefully if no sub.
- **Pointerdown preconnect**: begin opening on `pointerdown`, ~150ms sooner.
- **Warm-set reporting**: LiveWall POSTs current-page + next-page camera ids to
  `/live/warm` (debounced) so paging/patrol/first-open hit warm producers.
