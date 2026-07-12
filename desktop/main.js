'use strict';

const {
  app,
  BrowserWindow,
  Menu,
  shell,
  ipcMain,
  session,
  dialog,
  nativeImage,
} = require('electron');
const path = require('path');
const Store = require('electron-store');

// ---------------------------------------------------------------------------
// Persistent config (window bounds, saved server URL(s)) in userData.
// electron-store writes a JSON file under app.getPath('userData').
// ---------------------------------------------------------------------------
const store = new Store({
  name: 'kanagatly-vms',
  defaults: {
    serverUrl: '',        // currently active VMS URL
    recentServers: [],    // most-recent-first list of URLs (nice-to-have)
    windowBounds: null,   // { x, y, width, height }
  },
});

const PRODUCT_NAME = 'Kanagatly VMS';
const ICON_PNG = path.join(__dirname, 'build', 'icon.png');

// The single host we are allowed to trust a self-signed cert for.
// Format: "host:port" (e.g. "10.10.1.152:8443"). Empty = trust nothing extra.
let trustedOrigin = ''; // full origin, e.g. "https://10.10.1.152:8443"

let mainWindow = null;    // hosts the remote VMS
let connectWindow = null; // local branded connect screen

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------
function normalizeServerUrl(raw) {
  if (!raw || typeof raw !== 'string') return null;
  let s = raw.trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s;
  let u;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (!u.hostname) return null;
  // Keep origin + any path the user typed (usually none).
  return u.toString().replace(/\/$/, '');
}

function originOf(urlStr) {
  try {
    return new URL(urlStr).origin;
  } catch {
    return '';
  }
}

// Update the module-level trusted origin from the active server URL.
function setTrustedFromUrl(urlStr) {
  trustedOrigin = originOf(urlStr);
}

// ---------------------------------------------------------------------------
// Self-signed cert handling — SCOPED to the configured server host only.
// We do NOT globally ignore certificate errors. Two layers:
//   1. session.setCertificateVerifyProc: trust (return 0) only when the request
//      host:port matches the configured server; otherwise defer to Chromium (-3).
//   2. app 'certificate-error': same scoping as a belt-and-braces fallback.
// ---------------------------------------------------------------------------
function hostMatchesTrusted(hostname, port) {
  if (!trustedOrigin) return false;
  let t;
  try {
    t = new URL(trustedOrigin);
  } catch {
    return false;
  }
  const tPort = t.port || (t.protocol === 'https:' ? '443' : '80');
  const rPort = String(port || (t.protocol === 'https:' ? '443' : '80'));
  return t.hostname === hostname && tPort === rPort;
}

function installCertHandling() {
  session.defaultSession.setCertificateVerifyProc((request, callback) => {
    // request: { hostname, certificate, verificationResult, errorCode, ... }
    if (hostMatchesTrusted(request.hostname, request.port)) {
      callback(0); // 0 = trust this certificate
      return;
    }
    callback(-3); // -3 = use Chromium's default verification result
  });
}

// app-level fallback (fires for certificate errors Chromium raises directly).
app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
  let host, port;
  try {
    const u = new URL(url);
    host = u.hostname;
    port = u.port || (u.protocol === 'https:' ? 443 : 80);
  } catch {
    callback(false);
    return;
  }
  if (hostMatchesTrusted(host, port)) {
    event.preventDefault();
    callback(true); // trust
  } else {
    callback(false); // keep default (reject untrusted)
  }
});

// ---------------------------------------------------------------------------
// External links -> OS browser (never spawn new Electron windows).
// ---------------------------------------------------------------------------
function wireExternalLinks(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  // Block in-page navigations that leave the VMS origin (open them externally).
  contents.on('will-navigate', (event, url) => {
    const cur = contents.getURL();
    if (originOf(url) && originOf(cur) && originOf(url) !== originOf(cur)) {
      // Allow navigation within the same origin; anything else -> browser.
      event.preventDefault();
      shell.openExternal(url);
    }
  });
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------
function persistBounds(win) {
  if (!win || win.isDestroyed()) return;
  if (win.isFullScreen() || win.isMinimized()) return;
  store.set('windowBounds', win.getBounds());
}

function createMainWindow(serverUrl) {
  setTrustedFromUrl(serverUrl);

  const bounds = store.get('windowBounds') || { width: 1440, height: 900 };

  mainWindow = new BrowserWindow({
    ...bounds,
    minWidth: 900,
    minHeight: 600,
    title: PRODUCT_NAME,
    backgroundColor: '#0a0d10',
    icon: ICON_PNG,
    show: false,
    autoHideMenuBar: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // Chromium in Electron supports WebRTC/WebCodecs/MSE out of the box.
      // Allow muted video to autoplay without a user gesture.
      autoplayPolicy: 'no-user-gesture-required',
      // No preload for the remote VMS — remote content gets zero IPC surface.
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('close', () => persistBounds(mainWindow));
  mainWindow.on('resize', () => persistBounds(mainWindow));
  mainWindow.on('move', () => persistBounds(mainWindow));
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  wireExternalLinks(mainWindow.webContents);

  mainWindow.webContents.on('did-fail-load', (e, errorCode, errorDesc, validatedURL, isMainFrame) => {
    if (!isMainFrame) return;
    if (errorCode === -3 /* ABORTED */) return;
    dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Cannot reach VMS server',
      message: `Failed to load ${validatedURL || serverUrl}`,
      detail: `${errorDesc} (${errorCode})\n\nCheck that the server is reachable and the URL is correct, then use "Server → Switch server…".`,
      buttons: ['Switch server…', 'Retry', 'OK'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) openConnectWindow();
      else if (response === 1 && mainWindow) mainWindow.loadURL(serverUrl);
    });
  });

  mainWindow.loadURL(serverUrl);
  return mainWindow;
}

function openConnectWindow() {
  if (connectWindow && !connectWindow.isDestroyed()) {
    connectWindow.focus();
    return;
  }
  connectWindow = new BrowserWindow({
    width: 560,
    height: 640,
    resizable: false,
    title: PRODUCT_NAME,
    backgroundColor: '#0a0d10',
    icon: ICON_PNG,
    parent: mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
    modal: !!(mainWindow && !mainWindow.isDestroyed()),
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  connectWindow.setMenuBarVisibility(false);
  connectWindow.once('ready-to-show', () => connectWindow.show());
  connectWindow.on('closed', () => {
    connectWindow = null;
    // If no main window exists and no server was set, quit (user closed setup).
    if (!mainWindow && !store.get('serverUrl')) app.quit();
  });

  wireExternalLinks(connectWindow.webContents);
  connectWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

// Decide first screen: saved server -> VMS, else connect screen.
function bootstrap() {
  const saved = store.get('serverUrl');
  if (saved) {
    createMainWindow(saved);
  } else {
    openConnectWindow();
  }
}

// ---------------------------------------------------------------------------
// IPC from the connect screen
// ---------------------------------------------------------------------------
ipcMain.handle('vms:getState', () => ({
  serverUrl: store.get('serverUrl'),
  recentServers: store.get('recentServers') || [],
}));

// Validate only (no persist) — lets the renderer show inline feedback.
ipcMain.handle('vms:validate', (_e, raw) => {
  const url = normalizeServerUrl(raw);
  return { ok: !!url, url };
});

// Persist + connect.
ipcMain.handle('vms:connect', (_e, raw) => {
  const url = normalizeServerUrl(raw);
  if (!url) return { ok: false, error: 'Enter a valid URL, e.g. https://10.10.1.152:8443' };

  store.set('serverUrl', url);
  const recent = (store.get('recentServers') || []).filter((u) => u !== url);
  recent.unshift(url);
  store.set('recentServers', recent.slice(0, 8));

  setTrustedFromUrl(url);

  // Load into the main window (create if needed), then close the connect screen.
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(url);
  } else {
    createMainWindow(url);
  }
  if (connectWindow && !connectWindow.isDestroyed()) connectWindow.close();

  return { ok: true, url };
});

// ---------------------------------------------------------------------------
// Application menu
// ---------------------------------------------------------------------------
function buildMenu() {
  const isMac = process.platform === 'darwin';

  const template = [
    ...(isMac
      ? [{
          label: app.name,
          submenu: [
            { role: 'about' },
            { type: 'separator' },
            { role: 'services' },
            { type: 'separator' },
            { role: 'hide' },
            { role: 'hideOthers' },
            { role: 'unhide' },
            { type: 'separator' },
            { role: 'quit' },
          ],
        }]
      : []),
    {
      label: 'Server',
      submenu: [
        {
          label: 'Switch server…',
          accelerator: 'CmdOrCtrl+Shift+O',
          click: () => openConnectWindow(),
        },
        {
          label: 'Reload VMS',
          accelerator: 'CmdOrCtrl+R',
          click: () => {
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.reload();
          },
        },
        { type: 'separator' },
        ...(isMac ? [] : [{ role: 'quit' }]),
      ].filter(Boolean),
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac
          ? [{ type: 'separator' }, { role: 'front' }, { type: 'separator' }, { role: 'window' }]
          : [{ role: 'close' }]),
      ],
    },
    {
      role: 'help',
      submenu: [
        {
          label: `About ${PRODUCT_NAME}`,
          click: () => {
            dialog.showMessageBox({
              type: 'info',
              title: `About ${PRODUCT_NAME}`,
              message: PRODUCT_NAME,
              detail:
                `Desktop client v${app.getVersion()}\n` +
                `Electron ${process.versions.electron} · Chromium ${process.versions.chrome}\n\n` +
                `Active server: ${store.get('serverUrl') || '(none)'}`,
              icon: nativeImage.createFromPath(ICON_PNG),
              buttons: ['OK'],
            });
          },
        },
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// Single-instance lock + lifecycle
// ---------------------------------------------------------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const win = mainWindow || connectWindow;
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    if (process.platform === 'darwin') {
      const img = nativeImage.createFromPath(ICON_PNG);
      if (!img.isEmpty()) app.dock.setIcon(img);
    }
    installCertHandling();
    buildMenu();
    bootstrap();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) bootstrap();
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
