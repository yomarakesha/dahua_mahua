/**
 * Tests for ICE connection-state decision logic.
 *
 * Run: node --test web/js/ice.test.js
 *
 * The bug being fixed: the WebRTC handler treated `disconnected` exactly like
 * `failed` and reconnected immediately. But `disconnected` is transient and
 * usually self-recovers — reconnecting at once tears down a connection that
 * would have healed, and (because each reconnect re-opens the NVR RTSP source)
 * snowballs into the reconnect storm seen in the logs. The fix: on
 * `disconnected`, start a grace timer instead of reconnecting; only reconnect
 * if it stays disconnected (timer fires) or goes to `failed`/`closed`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { decideIceAction } from "./ice.js";

test("disconnected with no grace pending starts a grace timer (does NOT reconnect)", () => {
  assert.equal(decideIceAction("disconnected", false), "start-grace");
});

test("disconnected while a grace timer is already pending is ignored (no stacking)", () => {
  assert.equal(decideIceAction("disconnected", true), "ignore");
});

test("failed reconnects immediately", () => {
  assert.equal(decideIceAction("failed", false), "reconnect");
});

test("closed reconnects immediately", () => {
  assert.equal(decideIceAction("closed", false), "reconnect");
});

test("connected cancels any pending grace timer", () => {
  assert.equal(decideIceAction("connected", true), "cancel-grace");
});

test("completed cancels any pending grace timer", () => {
  assert.equal(decideIceAction("completed", true), "cancel-grace");
});

test("intermediate states (checking/new) are ignored", () => {
  assert.equal(decideIceAction("checking", false), "ignore");
  assert.equal(decideIceAction("new", false), "ignore");
});
