/**
 * Constants & configuration.
 */

export const CONFIG = {
  apiBase:        `${location.protocol}//${location.hostname}:9997/v3`,
  webrtcBase:     `${location.protocol}//${location.hostname}:8889`,
  hlsBase:        `${location.protocol}//${location.hostname}:8888`,
  pollInterval:   10000,
  maxConcurrent:  8,
  reconnectBase:  2000,
  reconnectMax:   30000,
  gridPresets:    [2, 4, 8, 16, 32, 64],
  patrolIntervals:[5, 10, 15, 30, 60],
};

export const LS = {
  groups:  "dss_groups",
  layouts: "dss_layouts",
  prefs:   "dss_prefs",
};

export const STALL_CHECK_INTERVAL = 4000;
export const STALL_THRESHOLD = 8;
