# DSS web (Kanagatly VMS) — React frontend

Rebuild of the DSS operator UI on **React 18 + TypeScript + Vite + Tailwind**,
matching the Kanagatly VMS designer export (dark console theme, green accent).
Same backend, same go2rtc relay, same video pipeline as the legacy `web/` — this
only replaces the frontend.

## Develop

```bash
cd web-react
npm install
npm run dev        # http://localhost:5173 — talks to backend at <host>:8000, go2rtc at <host>:1984
```

Endpoints are derived from `window.location.hostname` (see `src/lib/config.ts`),
so a client browser reaches whatever server served the page — no per-host config.

## Build (static, no server runtime needed)

```bash
npm run build      # → dist/ (plain static files: html + js + css + bundled fonts)
npm run test       # vitest
```

`dist/` is fully self-contained and offline-capable (fonts bundled). Uses
`HashRouter`, so it works on any dumb static file server (no SPA fallback needed).

## Cut over on the server (10.10.1.152)

The server already serves the legacy static UI from a folder via
`python -m http.server 8080`. To switch to this app **without losing rollback**:

1. Build locally: `npm run build`.
2. Copy `dist/` to the server as `C:\dss\web-react\` (e.g. via the LAN HTTP push,
   or zip + transfer like the rest of the deploy).
3. Point the frontend static server at the new folder:
   ```powershell
   cd C:\dss\web-react
   C:\Python311\python.exe -m http.server 8080 --bind 0.0.0.0
   ```
4. The legacy `C:\dss\web` stays untouched — to roll back, just serve it again.

Backend (`:8000`) and go2rtc (`:1984`) are unchanged. View from a **GPU client**
(the server itself has no GPU — it's the relay).

## Structure

```
src/
  lib/config.ts          endpoints (host-derived) + storage keys
  lib/auth.tsx           AuthProvider + RequireAuth route guard
  lib/vendor/            vendored go2rtc player (video-rtc.js, real-time 1.0x fix)
  api/                   types, JWT fetch client, TanStack Query hooks
  components/            AppShell (nav rail), Logo, icons
  components/video/      MsePlayer (React wrapper around <dss-mse>)
  features/auth/         Sign in
  features/live/         Live wall (grid, sidebar, tiles, fullscreen, patrol)
  features/nvrs/         NVR management + Camera channels
  features/settings/     Account + change password
```
