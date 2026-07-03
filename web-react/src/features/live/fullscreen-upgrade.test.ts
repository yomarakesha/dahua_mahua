/**
 * Sub→main instant-view upgrade state machine (FullscreenView).
 */
import { describe, it, expect } from "vitest";
import { fullscreenLayers } from "./fullscreen-upgrade";

describe("fullscreenLayers — sub→main upgrade", () => {
  it("sub+main, before the main goes live: sub visible, main hidden behind", () => {
    const l = fullscreenLayers(true, true, /*mainLive*/ false, /*subTorn*/ false);
    expect(l).toEqual({ showSub: true, showMain: true, mainOpaque: false });
  });

  it("sub+main, main just went live: cross-fade — both mounted, main opaque", () => {
    const l = fullscreenLayers(true, true, true, false);
    expect(l).toEqual({ showSub: true, showMain: true, mainOpaque: true });
  });

  it("sub+main, after the fade grace: sub torn down, main only", () => {
    const l = fullscreenLayers(true, true, true, /*subTorn*/ true);
    expect(l).toEqual({ showSub: false, showMain: true, mainOpaque: true });
  });

  it("main only (no sub): behaves as today — main visible immediately", () => {
    const l = fullscreenLayers(false, true, false, false);
    expect(l).toEqual({ showSub: false, showMain: true, mainOpaque: true });
  });

  it("sub only (no main): sub visible, no upgrade layer, never torn", () => {
    const l = fullscreenLayers(true, false, false, /*subTorn even if set*/ true);
    expect(l).toEqual({ showSub: true, showMain: false, mainOpaque: false });
  });

  it("no streams: nothing renders", () => {
    const l = fullscreenLayers(false, false, false, false);
    expect(l).toEqual({ showSub: false, showMain: false, mainOpaque: false });
  });
});
