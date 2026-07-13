'use strict';

// Minimal, locked-down bridge exposed ONLY to the local branded connect screen
// (renderer/index.html). The remote VMS window loads with NO preload, so this
// IPC surface is never reachable from remote web content.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vms', {
  /** { serverUrl, recentServers } */
  getState: () => ipcRenderer.invoke('vms:getState'),
  /** Client-side-ish validation via the main process. -> { ok, url } */
  validate: (raw) => ipcRenderer.invoke('vms:validate', raw),
  /** Persist + load the VMS. -> { ok, url } | { ok:false, error } */
  connect: (raw) => ipcRenderer.invoke('vms:connect', raw),
});
