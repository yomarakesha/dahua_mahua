# Kanagatly VMS — Desktop Client

A professional Electron desktop wrapper around the Kanagatly VMS web app. It
makes the browser-based surveillance UI feel like a real installed application:
native window + menus, a branded "connect to server" screen, saved server URLs,
and clean handling of the server's self-signed TLS certificate.

The VMS relies on modern Chromium media features (WebRTC + WebCodecs + MSE),
which Electron's bundled Chromium supports. The app allows muted video to
autoplay (`autoplayPolicy: 'no-user-gesture-required'`) and does **not** disable
any of those features.

---

## Requirements

- Node.js 18+ and npm (developed on Node 25 / npm 11)
- macOS, Windows, or Linux

## Install & run (development)

```bash
cd desktop
npm install
npm run dev          # launches the app (alias: npm start)
```

On first launch (no server saved) you get the branded **Connect** screen. Enter
your VMS URL — e.g. `https://10.10.1.152:8443` — and click **Connect**. The URL
is validated, persisted, and the main window loads the VMS. Reopen the connect
screen any time via the menu: **Server → Switch server…** (`Cmd/Ctrl+Shift+O`).

## How the server URL is configured & persisted

- Stored with [`electron-store`](https://github.com/sindresorhus/electron-store)
  as `kanagatly-vms.json` in `app.getPath('userData')`
  (e.g. `~/Library/Application Support/Kanagatly VMS/` on macOS,
  `%APPDATA%\Kanagatly VMS\` on Windows).
- Keys: `serverUrl` (active), `recentServers` (last 8, shown as quick-pick
  buttons on the connect screen), and `windowBounds` (window size/position).
- Bare hosts are accepted and normalized (`10.10.1.152:8443` →
  `https://10.10.1.152:8443`).

## Self-signed certificate handling (scoped, not global)

The VMS is served over HTTPS with a self-signed cert (Caddy). The app trusts
that cert **only for the host:port you configured** — it is *not* a blanket
"ignore all certificate errors":

- `session.setCertificateVerifyProc` returns *trust* (`0`) only when the request
  host and port match the configured server origin; every other host falls
  through to Chromium's normal verification (`-3`).
- `app.on('certificate-error')` applies the same host+port match as a fallback.
- `webSecurity` is left **enabled**.

Change servers and the trusted origin moves with it; unrelated HTTPS sites are
still validated normally.

## Security hygiene

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`.
- The **remote VMS window has no preload and no IPC surface.** The minimal
  `preload.js` (three `invoke` bridges) is attached **only** to the local
  connect screen.
- `target="_blank"` / external links open in the OS browser, never new Electron
  windows. Cross-origin navigations are pushed to the OS browser too.
- Single-instance lock (a second launch focuses the existing window).

## Build installers (packaging)

Packaging uses **electron-builder** (`electron-builder.yml`). appId
`com.kanagatly.vms`, productName **Kanagatly VMS**.

```bash
npm run dist          # build for the current OS
npm run dist:win      # Windows NSIS .exe (the primary target)
npm run dist:mac      # macOS .dmg
npm run dist:linux    # Linux AppImage
```

Output lands in `desktop/dist/`.

Notes:
- Cross-building Windows/mac targets from another OS may require extra tooling
  (e.g. Wine for Windows on macOS/Linux; macOS `.dmg` can only be built on
  macOS). Build each target on its native OS for the smoothest result.
- **Code signing** is not configured. For real distribution you'll want an
  Authenticode cert (Windows) and an Apple Developer ID + notarization (macOS);
  unsigned builds trigger SmartScreen / Gatekeeper warnings.

## App icon

- Source of truth: **`build/icon.svg`** — a `#2ecc71` green disc with bold white
  **"KM"**, matching the web app's `LogoMark`.
- **`build/icon.png`** (512×512) is generated from the SVG and committed.
  electron-builder derives the platform `.ico` (Windows) and `.icns` (macOS)
  from it automatically at build time.
- To regenerate the PNG after editing the SVG:
  ```bash
  npm run icons      # uses rsvg-convert / Inkscape / macOS qlmanage, whichever exists
  ```

## Manual verification (needs a live VMS)

The connect screen and main-process boot are verifiable offline. The live 4MP
video path (WebRTC/WebCodecs/MSE) can only be exercised against a running VMS
with cameras — point the app at your `https://<server>:8443` and confirm tiles
play. That step is manual.

## Project layout

```
desktop/
├── main.js               # main process: windows, menu, cert scoping, IPC, store
├── preload.js            # minimal bridge — connect screen only
├── renderer/             # branded local connect screen (html/css/js)
│   ├── index.html
│   ├── connect.css
│   └── connect.js
├── build/
│   ├── icon.svg          # icon source
│   └── icon.png          # 512×512, committed; builder derives .ico/.icns
├── scripts/gen-icon.sh   # regenerate icon.png from the SVG
├── electron-builder.yml  # packaging config (win nsis / mac dmg / linux AppImage)
└── package.json
```
