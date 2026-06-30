/**
 * usePlaybackSession — owns ONE persistent playback WebSocket.
 *
 * Controls (seek/pause/play/speed) are sent as JSON messages over this single
 * socket; we never open a second one. There is NO auto-reconnect — a reconnect
 * only ever happens because the caller mounts a fresh session (new nvr/channel)
 * or the user explicitly seeks (task-14 brief / Contract: "no auto-reconnect").
 *
 * Not unit-tested: jsdom has no real WebSocket. The hook opens the socket only
 * inside an effect (never at import), and guards `typeof WebSocket` so it stays
 * inert under the test runner. Live behavior is in the DEFERRED checklist.
 */
import { useEffect, useRef, useState } from "react";
import { getToken } from "@/api/client";
import { buildPlaybackWsUrl } from "./playback-utils";
import type { ClientMsg, ServerMsg } from "./types";

export interface PlaybackSessionOptions {
  nvrId: string;
  channel: number;
  /** Initial seek target (footage epoch). Sent as {seek:N} once the WS opens. */
  initialSeek: number;
  /**
   * Increment to force a fresh WS session (Retry after ws_close error).
   * Bumping this tears down any existing socket and opens a new one, which
   * sends {seek: initialSeek} on open. Safe to omit (treated as 0).
   */
  reconnectNonce?: number;
  /** A typed JSON signal arrived from the server. */
  onSignal: (msg: ServerMsg) => void;
  /** A binary fMP4 fragment arrived. */
  onData: (data: ArrayBuffer) => void;
  /** The socket closed UNEXPECTEDLY (not via our own close()/teardown). */
  onClose: () => void;
}

export interface PlaybackSession {
  send: (msg: ClientMsg) => void;
  close: () => void;
}

const KEEPALIVE_MS = 30_000; // under Caddy's 300 s idle timeout

export function usePlaybackSession(
  opts: PlaybackSessionOptions | null,
): PlaybackSession | null {
  // Latest callbacks / initial-seek kept in a ref so they don't re-open the WS.
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const [session, setSession] = useState<PlaybackSession | null>(null);

  const enabled = !!opts;
  const nvrId = opts?.nvrId;
  const channel = opts?.channel;
  // reconnectNonce: a bump forces the effect to re-run, tearing down the old socket
  // and opening a fresh one. optsRef.current.initialSeek is always current (caller
  // updates opts every render when seekTarget changes), so the fresh WS seeks to the
  // right position.
  const reconnectNonce = opts?.reconnectNonce ?? 0;

  useEffect(() => {
    if (!enabled || nvrId == null || channel == null) {
      setSession(null);
      return;
    }
    // jsdom / non-browser guard — keep the module inert under tests.
    if (typeof WebSocket === "undefined") {
      setSession(null);
      return;
    }

    const url = buildPlaybackWsUrl(nvrId, channel, getToken() ?? "");
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    let keepaliveId: ReturnType<typeof setInterval> | null = null;
    let closedByUs = false;

    const send = (msg: ClientMsg) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    };

    const clearKeepalive = () => {
      if (keepaliveId != null) {
        clearInterval(keepaliveId);
        keepaliveId = null;
      }
    };

    ws.onopen = () => {
      send({ seek: optsRef.current?.initialSeek ?? 0 });
      keepaliveId = setInterval(() => send({ keepalive: true }), KEEPALIVE_MS);
    };

    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data === "string") {
        try {
          optsRef.current?.onSignal(JSON.parse(ev.data) as ServerMsg);
        } catch {
          // Malformed JSON from the server — ignore (binary frames are the data path).
        }
      } else {
        optsRef.current?.onData(ev.data as ArrayBuffer);
      }
    };

    ws.onclose = () => {
      clearKeepalive();
      if (!closedByUs) optsRef.current?.onClose();
    };

    // onerror is followed by onclose; the close handler owns the teardown/notify.
    ws.onerror = () => {};

    const close = () => {
      closedByUs = true;
      clearKeepalive();
      ws.close();
    };

    setSession({ send, close });

    return () => {
      closedByUs = true;
      clearKeepalive();
      ws.close();
      setSession(null);
    };
  // reconnectNonce is intentionally in deps: a bump tears down the old socket and
  // opens a fresh one (Retry-after-ws_close fix). nvrId/channel changes also reconnect.
  }, [enabled, nvrId, channel, reconnectNonce]);

  return session;
}
