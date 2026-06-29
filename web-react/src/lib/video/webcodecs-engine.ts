/**
 * WebCodecs engine — the "native-app-grade" 4MP main-stream path.
 *
 * THE PROBLEM. The 4MP main freezes under congestion on MSE (TCP): MSE owns the
 * buffer + clock and must play EVERY frame in order, so when the network falls
 * behind it stalls instead of skipping. WebRTC (UDP) drops late frames but loses
 * the big 4MP keyframes on a lossy link (one missing packet in a ~100-packet IDR
 * → the decoder never gets a clean keyframe → 0 frames). Proven dead end here.
 *
 * THE FIX. Pull the SAME fMP4 go2rtc serves to MSE (over the WS/TCP, so keyframes
 * arrive intact), but demux it ourselves and feed the raw H.264 to the browser's
 * hardware decoder via WebCodecs (VideoDecoder). Then WE own the frame policy:
 * render the newest decoded frame and drop the rest — stay live under load, like
 * Smart PSS / iVMS — at full 4MP. TCP keeps keyframes whole; WebCodecs drops late
 * frames. Best of both.
 *
 * Pipeline:  go2rtc WS (fMP4) → mp4box demux → EncodedVideoChunk → VideoDecoder
 *            → VideoFrame queue → rAF render (paint newest, close older) → canvas
 */
import {
  createFile,
  DataStream,
  type ISOFile,
  type MP4ArrayBuffer,
  type MP4Box,
  type MP4Sample,
  type MP4VideoTrack,
} from "mp4box";

export type EngineStatus = "connecting" | "live" | "error";

// Codecs we advertise to go2rtc. go2rtc still sends the stream's REAL codec
// (the camera main is H.264 Main, avc1.4d00xx) — this list just tells it the
// client can play H.264, so it passes the main through instead of transcoding.
// Broad profile/level coverage (High/Main/Baseline) so any camera passes through.
const ADVERTISE_CODECS = [
  "avc1.640033", "avc1.640032", "avc1.640029", "avc1.640028",
  "avc1.4d0033", "avc1.4d0032", "avc1.4d0029", "avc1.4d0028",
  "avc1.42e033", "avc1.42e032", "avc1.42e029", "avc1.42e028", "avc1.42e01f", "avc1.42e01e",
].join();

// Jitter buffer depths (in frames). The renderer plays in order at the source
// rate; it only skips ahead when it falls behind, so a burst of frames arriving
// together is played out smoothly instead of being dropped.
//   TARGET — normal cushion; below this we pace strictly to source fps.
//   MAX    — hard latency cap; above this we drop oldest to snap back to live.
const TARGET_DEPTH = 2;
const MAX_DEPTH = 6;
// Absolute memory guard (frames are GPU-backed; never let the queue grow wild).
const HARD_CAP = 30;
const DEFAULT_FRAME_MS = 40; // 25 fps until measured from timestamps

export interface EngineCallbacks {
  onStatus?: (s: EngineStatus) => void;
}

/** Pull the avcC/hvcC bytes mp4box parsed → the `description` VideoDecoder needs. */
function codecDescription(mp4: ISOFile, trackId: number): Uint8Array | undefined {
  const trak = mp4.getTrackById(trackId);
  const entries = trak?.mdia?.minf?.stbl?.stsd?.entries ?? [];
  for (const entry of entries) {
    const box = (entry.avcC ?? entry.hvcC ?? entry.vpcC ?? entry.av1C) as MP4Box | undefined;
    if (box) {
      const ds = new DataStream(undefined, 0, DataStream.BIG_ENDIAN);
      box.write(ds);
      return new Uint8Array(ds.buffer, 8); // strip the 8-byte box header
    }
  }
  return undefined;
}

export class WebCodecsEngine {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly cb: EngineCallbacks;

  private ws: WebSocket | null = null;
  private mp4: ISOFile | null = null;
  private decoder: VideoDecoder | null = null;

  private appendPos = 0;
  private sawKey = false;
  private firstFrame = false;
  private destroyed = false;
  private rafId = 0;
  private queue: VideoFrame[] = [];
  // Render pacing state.
  private lastPresentMs = 0;
  private frameIntervalMs = DEFAULT_FRAME_MS;
  private lastFrameTs = -1; // µs, last decoded frame timestamp (for fps measurement)

  // Diagnostics (enable with `localStorage.dssDebug = "1"`, reload). Lets us tell
  // real corruption (low decoded/s) from intentional drop-late skips (high
  // dropped/s while staying live) — there is NO transport packet loss (TCP).
  private dbg = { fed: 0, decoded: 0, rendered: 0, dropped: 0 };
  private dbgTimer = 0;

  constructor(canvas: HTMLCanvasElement, cb: EngineCallbacks = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.cb = cb;
  }

  /** Feature gate: Safari < 26 and old browsers lack WebCodecs → caller uses MSE. */
  static isSupported(): boolean {
    return (
      typeof window !== "undefined" &&
      typeof window.VideoDecoder === "function" &&
      typeof window.EncodedVideoChunk === "function"
    );
  }

  start(wsUrl: string): void {
    this.setStatus("connecting");
    this.openWs(wsUrl);
    this.rafId = requestAnimationFrame(this.render);
    let debugOn = false;
    try { debugOn = localStorage.getItem("dssDebug") === "1"; } catch { /* ignore */ }
    if (debugOn) {
      this.dbgTimer = window.setInterval(() => {
        const d = this.dbg;
        // eslint-disable-next-line no-console
        console.log(
          `[webcodecs] fed=${d.fed}/s decoded=${d.decoded}/s rendered=${d.rendered}/s ` +
          `dropped-late=${d.dropped}/s queue=${this.queue.length}`,
        );
        this.dbg = { fed: 0, decoded: 0, rendered: 0, dropped: 0 };
      }, 1000);
    }
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.rafId);
    if (this.dbgTimer) window.clearInterval(this.dbgTimer);
    for (const f of this.queue) safeClose(f);
    this.queue = [];
    if (this.decoder) {
      try { if (this.decoder.state !== "closed") this.decoder.close(); } catch { /* ignore */ }
      this.decoder = null;
    }
    if (this.mp4) {
      try { this.mp4.stop(); this.mp4.flush(); } catch { /* ignore */ }
      this.mp4 = null;
    }
    if (this.ws) {
      this.ws.onopen = this.ws.onmessage = this.ws.onerror = this.ws.onclose = null;
      try { this.ws.close(); } catch { /* ignore */ }
      this.ws = null;
    }
  }

  private setStatus(s: EngineStatus): void {
    if (!this.destroyed) this.cb.onStatus?.(s);
  }

  private fail(reason: string): void {
    if (this.destroyed) return;
    // eslint-disable-next-line no-console
    console.warn("[webcodecs] falling back:", reason);
    this.setStatus("error"); // FullscreenView sees this → switches to MSE → destroy()
  }

  private openWs(wsUrl: string): void {
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      // Ask go2rtc for the MSE (fMP4) stream — same request the <video> MSE path
      // sends — but we demux it ourselves instead of feeding a SourceBuffer.
      ws.send(JSON.stringify({ type: "mse", value: ADVERTISE_CODECS }));
      this.initMp4();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return; // control frame (codec echo) — ignore
      this.onFmp4(ev.data as ArrayBuffer);
    };
    ws.onerror = () => this.fail("websocket error");
    ws.onclose = () => { if (!this.destroyed) this.fail("websocket closed"); };
  }

  private initMp4(): void {
    const mp4 = createFile();
    this.mp4 = mp4;
    mp4.onError = (e) => this.fail(`mp4box: ${e}`);
    mp4.onReady = (info) => {
      const track = info.videoTracks[0];
      if (!track) return this.fail("no video track in fMP4");
      this.configureDecoder(mp4, track);
      mp4.setExtractionOptions(track.id, null, { nbSamples: 1 });
      mp4.start();
    };
    mp4.onSamples = (_id, _user, samples) => {
      for (const s of samples) this.decodeSample(s);
    };
  }

  private configureDecoder(mp4: ISOFile, track: MP4VideoTrack): void {
    const description = codecDescription(mp4, track.id);
    const decoder = new VideoDecoder({
      output: (frame) => this.onDecoded(frame),
      error: (e) => this.fail(`decoder: ${e.message}`),
    });
    const config: VideoDecoderConfig = {
      codec: track.codec,
      codedWidth: track.video.width,
      codedHeight: track.video.height,
      optimizeForLatency: true,
      hardwareAcceleration: "prefer-hardware",
      ...(description ? { description } : {}),
    };
    try {
      decoder.configure(config);
    } catch (e) {
      return this.fail(`configure: ${(e as Error).message}`);
    }
    this.decoder = decoder;
    // Canvas pixel buffer = source resolution (full 4MP); CSS object-fit letterboxes it.
    this.canvas.width = track.video.width;
    this.canvas.height = track.video.height;
  }

  private decodeSample(s: MP4Sample): void {
    const decoder = this.decoder;
    if (!decoder || decoder.state !== "configured") return;
    // The decoder must start on a keyframe; skip any leading deltas.
    if (!this.sawKey) {
      if (!s.is_sync) return;
      this.sawKey = true;
    }
    try {
      decoder.decode(
        new EncodedVideoChunk({
          type: s.is_sync ? "key" : "delta",
          timestamp: (s.cts * 1e6) / s.timescale, // microseconds, monotonic from the stream
          duration: s.duration ? (s.duration * 1e6) / s.timescale : undefined,
          data: s.data,
        }),
      );
      this.dbg.fed++;
    } catch (e) {
      this.fail(`decode: ${(e as Error).message}`);
    }
  }

  private onDecoded(frame: VideoFrame): void {
    if (this.destroyed) return safeClose(frame);
    this.dbg.decoded++;
    // Measure source frame interval from decode timestamps (EMA) so pacing tracks
    // the real fps (25/15/30…) instead of a fixed guess.
    if (this.lastFrameTs >= 0) {
      const dMs = (frame.timestamp - this.lastFrameTs) / 1000;
      if (dMs > 5 && dMs < 200) this.frameIntervalMs = this.frameIntervalMs * 0.8 + dMs * 0.2;
    }
    this.lastFrameTs = frame.timestamp;
    this.queue.push(frame); // FIFO — render() paces & drops, not us
    while (this.queue.length > HARD_CAP) { safeClose(this.queue.shift()!); this.dbg.dropped++; }
    if (!this.firstFrame) {
      this.firstFrame = true;
      this.setStatus("live");
    }
  }

  // Paced renderer with catch-up. Plays frames in ORDER at the measured source
  // rate (a small jitter buffer absorbs bursts → smooth), and only skips ahead
  // when it falls behind — so a clump of frames arriving together is played out,
  // not thrown away. When latency exceeds MAX_DEPTH it snaps back to live.
  private render = (): void => {
    if (this.destroyed) return;
    this.rafId = requestAnimationFrame(this.render);
    if (!this.ctx || !this.queue.length) return;

    // Hard latency cap: too far behind → drop oldest to snap to live.
    while (this.queue.length > MAX_DEPTH) { safeClose(this.queue.shift()!); this.dbg.dropped++; }

    const now = performance.now();
    const due = now - this.lastPresentMs >= this.frameIntervalMs * 0.85;
    const behind = this.queue.length > TARGET_DEPTH;
    // Pace to source fps unless we're behind (then drain every tick to catch up).
    if (!due && !behind) return;

    const frame = this.queue.shift()!; // oldest — in-order playback
    try {
      this.ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height);
      this.dbg.rendered++;
    } catch { /* ignore transient draw errors */ }
    safeClose(frame);
    this.lastPresentMs = now;
  };

  private onFmp4(data: ArrayBuffer): void {
    if (this.destroyed || !this.mp4) return;
    const buf = data as MP4ArrayBuffer;
    buf.fileStart = this.appendPos;
    this.appendPos += buf.byteLength;
    try {
      this.mp4.appendBuffer(buf);
    } catch (e) {
      this.fail(`append: ${(e as Error).message}`);
    }
  }
}

function safeClose(frame: VideoFrame): void {
  try { frame.close(); } catch { /* already closed */ }
}
