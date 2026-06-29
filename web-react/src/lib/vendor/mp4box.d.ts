// Minimal typings for mp4box.js (no official @types). Covers only the fMP4
// demux surface the WebCodecs engine uses: createFile → onReady/onSamples, plus
// DataStream for extracting the avcC/hvcC decoder description.
declare module "mp4box" {
  /** An ArrayBuffer tagged with its byte offset in the stream (mp4box needs it). */
  export interface MP4ArrayBuffer extends ArrayBuffer {
    fileStart: number;
  }

  export class DataStream {
    constructor(buffer?: ArrayBuffer, byteOffset?: number, endianness?: boolean);
    static BIG_ENDIAN: boolean;
    static LITTLE_ENDIAN: boolean;
    buffer: ArrayBuffer;
  }

  export interface MP4VideoTrack {
    id: number;
    codec: string; // e.g. "avc1.4d0028"
    timescale: number;
    nb_samples: number;
    video: { width: number; height: number };
  }

  export interface MP4Info {
    videoTracks: MP4VideoTrack[];
  }

  export interface MP4Sample {
    is_sync: boolean;
    cts: number;
    dts: number;
    duration: number;
    timescale: number;
    data: Uint8Array;
  }

  /** A box that can serialise itself (used to pull raw avcC bytes). */
  export interface MP4Box {
    write(stream: DataStream): void;
  }

  export interface MP4Track {
    mdia: {
      minf: { stbl: { stsd: { entries: Array<Record<string, MP4Box | undefined>> } } };
    };
  }

  export interface ISOFile {
    onReady: (info: MP4Info) => void;
    onError: (e: string) => void;
    onSamples: (id: number, user: unknown, samples: MP4Sample[]) => void;
    appendBuffer(data: MP4ArrayBuffer): number;
    start(): void;
    stop(): void;
    flush(): void;
    setExtractionOptions(id: number, user?: unknown, opts?: { nbSamples?: number }): void;
    getTrackById(id: number): MP4Track | undefined;
  }

  export function createFile(): ISOFile;
}
