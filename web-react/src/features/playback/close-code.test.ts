import { describe, expect, it } from "vitest";
import { mapCloseCode } from "./playback-utils";

describe("mapCloseCode", () => {
  it("maps 4001 → session expired (retryable)", () => {
    expect(mapCloseCode(4001)).toEqual({
      text: "Session expired — sign in again",
      retryable: true,
    });
  });

  it("maps 4003 → no permission (NON-retryable)", () => {
    expect(mapCloseCode(4003)).toEqual({
      text: "No permission for this camera",
      retryable: false,
    });
  });

  it("maps 4004 → camera not found/disabled (NON-retryable)", () => {
    expect(mapCloseCode(4004)).toEqual({
      text: "Camera not found or disabled",
      retryable: false,
    });
  });

  it("maps 4429 → recorder busy (retryable)", () => {
    expect(mapCloseCode(4429)).toEqual({
      text: "Recorder is busy — too many playback sessions",
      retryable: true,
    });
  });

  it("falls back to generic retryable text for unknown / absent codes", () => {
    expect(mapCloseCode(1006)).toEqual({ text: null, retryable: true });
    expect(mapCloseCode(undefined)).toEqual({ text: null, retryable: true });
  });
});
