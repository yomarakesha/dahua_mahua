import { describe, it, expect } from "vitest";
import { playerReducer, INITIAL_PLAYER_STATE } from "./player-machine";
import type { PlayerState } from "./types";

// The reducer is the single source of truth for the playback state machine.
// Transitions are driven by BACKEND SIGNALS + USER ACTIONS (never "currentTime
// stopped"). See task-14 brief transition table.

describe("playerReducer", () => {
  it("starts in 'loading'", () => {
    expect(INITIAL_PLAYER_STATE).toBe<PlayerState>("loading");
  });

  // ── init / first-data → playing ───────────────────────────────────────────
  it("init keeps state in 'loading' (MSE rebuilding, awaiting data)", () => {
    expect(playerReducer("loading", { type: "init" })).toBe("loading");
    expect(playerReducer("seeking", { type: "init" })).toBe("loading");
  });

  it("loading → playing on first data append", () => {
    expect(playerReducer("loading", { type: "playing" })).toBe("playing");
  });

  it("'playing' event does not resurrect a paused/terminal state", () => {
    // Backend may keep sending data while user-paused; must NOT flip back to playing.
    expect(playerReducer("paused", { type: "playing" })).toBe("paused");
    expect(playerReducer("end", { type: "playing" })).toBe("end");
    expect(playerReducer("error", { type: "playing" })).toBe("error");
  });

  // ── pause / play ────────────────────────────────────────────────────────────
  it("playing → paused on user pause", () => {
    expect(playerReducer("playing", { type: "pause" })).toBe("paused");
  });

  it("pause is a no-op when not playing", () => {
    expect(playerReducer("loading", { type: "pause" })).toBe("loading");
    expect(playerReducer("seeking", { type: "pause" })).toBe("seeking");
  });

  it("paused → playing on user play", () => {
    expect(playerReducer("paused", { type: "play" })).toBe("playing");
  });

  it("play is a no-op when not paused", () => {
    expect(playerReducer("playing", { type: "play" })).toBe("playing");
  });

  // ── seek (and speed change) → seeking ────────────────────────────────────────
  it("playing → seeking on user seek", () => {
    expect(playerReducer("playing", { type: "seek" })).toBe("seeking");
  });

  it("paused → seeking on user seek", () => {
    expect(playerReducer("paused", { type: "seek" })).toBe("seeking");
  });

  it("seek recovers from terminal states (re-seek = reconnect)", () => {
    expect(playerReducer("end", { type: "seek" })).toBe("seeking");
    expect(playerReducer("error", { type: "seek" })).toBe("seeking");
  });

  // ── reinit → loading → playing ────────────────────────────────────────────────
  it("seeking → loading on reinit, then → playing on data", () => {
    const afterReinit = playerReducer("seeking", { type: "reinit" });
    expect(afterReinit).toBe("loading");
    expect(playerReducer(afterReinit, { type: "playing" })).toBe("playing");
  });

  // ── gap ───────────────────────────────────────────────────────────────────────
  it("gap with a next clip → seeking (auto-skip)", () => {
    expect(playerReducer("playing", { type: "gap", next: 12345 })).toBe("seeking");
  });

  it("gap with next === null → end", () => {
    expect(playerReducer("playing", { type: "gap", next: null })).toBe("end");
  });

  // ── eof ────────────────────────────────────────────────────────────────────────
  it("eof → end", () => {
    expect(playerReducer("playing", { type: "eof" })).toBe("end");
  });

  // ── error sources ───────────────────────────────────────────────────────────────
  it("server {error} → error", () => {
    expect(playerReducer("playing", { type: "error" })).toBe("error");
  });

  it("unrecoverable quota → error", () => {
    expect(playerReducer("playing", { type: "quota_failed" })).toBe("error");
  });

  it("ws close without eof → error", () => {
    expect(playerReducer("playing", { type: "ws_close" })).toBe("error");
    expect(playerReducer("loading", { type: "ws_close" })).toBe("error");
    expect(playerReducer("paused", { type: "ws_close" })).toBe("error");
  });

  it("ws close AFTER end/no_coverage is benign (graceful backend close)", () => {
    expect(playerReducer("end", { type: "ws_close" })).toBe("end");
    expect(playerReducer("no_coverage", { type: "ws_close" })).toBe("no_coverage");
    expect(playerReducer("error", { type: "ws_close" })).toBe("error");
  });

  // ── no_coverage (told by parent; player never synthesizes it) ─────────────────────
  it("no_coverage event sets no_coverage", () => {
    expect(playerReducer("loading", { type: "no_coverage" })).toBe("no_coverage");
  });

  // ── error is otherwise terminal ───────────────────────────────────────────────────
  it("error absorbs non-recovery events", () => {
    expect(playerReducer("error", { type: "init" })).toBe("loading"); // re-mount/re-seek path
    expect(playerReducer("error", { type: "pause" })).toBe("error");
    expect(playerReducer("error", { type: "play" })).toBe("error");
    expect(playerReducer("error", { type: "eof" })).toBe("error");
  });

  it("error + late eof → still error (passive event must not un-stick error overlay)", () => {
    expect(playerReducer("error", { type: "eof" })).toBe("error");
  });

  // ── reconnect (Retry forces fresh WS session) ─────────────────────────────────
  it("error + reconnect → loading (Retry opens fresh WS, not seek over dead socket)", () => {
    expect(playerReducer("error", { type: "reconnect" })).toBe("loading");
  });

  it("reconnect → loading from any state (defensive: e.g. double-click Retry)", () => {
    expect(playerReducer("playing", { type: "reconnect" })).toBe("loading");
    expect(playerReducer("seeking", { type: "reconnect" })).toBe("loading");
    expect(playerReducer("paused",  { type: "reconnect" })).toBe("loading");
    expect(playerReducer("end",     { type: "reconnect" })).toBe("loading");
  });
});
