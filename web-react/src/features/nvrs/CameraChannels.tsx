import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useCameras,
  useCreateCamera,
  useDeleteCamera,
  useImportCameraIps,
  useNvrs,
  useSetChannels,
  useUpdateCamera,
} from "@/api/hooks";
import type { Camera, CameraUpdate } from "@/api/types";
import {
  CameraIcon,
  CheckIcon,
  ChevronRight,
  PlusIcon,
  RefreshIcon,
  TrashIcon,
} from "@/components/icons";

const GRID = "grid-cols-[40px_44px_minmax(0,1.6fr)_minmax(0,1.2fr)_56px_56px_64px_88px]";

/** Admin screen: per-NVR channel (camera) configuration. Route /nvrs/:nvrId/channels */
export default function CameraChannels() {
  const { nvrId = "" } = useParams<{ nvrId: string }>();
  const nvrs = useNvrs();
  const cameras = useCameras();

  const setChannels = useSetChannels();
  const createCamera = useCreateCamera();
  const importIps = useImportCameraIps();

  const nvr = (nvrs.data ?? []).find((n) => n.id === nvrId);

  const channels = useMemo<Camera[]>(
    () =>
      (cameras.data ?? [])
        .filter((c) => c.nvr_id === nvrId)
        .sort((a, b) => a.channel - b.channel),
    [cameras.data, nvrId],
  );

  const [count, setCount] = useState<string>("");
  const [prune, setPrune] = useState(false);

  // keep the channel-count input in sync with the live count until the user edits it
  const [countDirty, setCountDirty] = useState(false);
  const effectiveCount = countDirty ? count : String(channels.length);

  const nextChannel = useMemo(() => {
    const used = new Set(channels.map((c) => c.channel));
    let n = 1;
    while (used.has(n)) n += 1;
    return n;
  }, [channels]);

  // NVR not found (only once loaded)
  if (!nvrs.isLoading && !nvr) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto flex max-w-[820px] flex-col gap-4 p-5 lg:p-7">
          <BackLink />
          <div className="dss-panel p-8 text-center">
            <p className="text-base font-semibold text-ink">Recorder not found</p>
            <p className="mt-1 text-sm text-ink-dim">
              No NVR with id <span className="font-mono text-ink-soft">{nvrId}</span>.
            </p>
          </div>
        </div>
      </div>
    );
  }

  function applyCount() {
    const parsed = Number.parseInt(effectiveCount, 10);
    if (!Number.isFinite(parsed) || parsed < 0) return;
    setChannels.mutate(
      { id: nvrId, count: parsed, prune },
      {
        onSuccess: () => {
          setCountDirty(false);
        },
      },
    );
  }

  const importResult = importIps.data;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[820px] flex-col gap-4 p-5 lg:p-7">
        <BackLink />

        {/* header */}
        <header className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/25 bg-accent/[.12] text-accent-light">
            <CameraIcon size={18} />
          </div>
          <div>
            <h1 className="flex items-baseline gap-2 text-[17px] font-extrabold text-ink-bright">
              Cameras —{" "}
              <span className="font-mono text-sm font-bold text-ink-soft">
                {nvr ? nvr.label : nvrId}
              </span>
            </h1>
            <p className="text-sm text-ink-dim">
              Channel configuration · <span className="font-mono">{nvrId}</span>
            </p>
          </div>
        </header>

        {/* control row: set total channels */}
        <section className="dss-panel p-4">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
            <div className="flex-1">
              <div className="dss-label mb-2.5 tracking-[1.2px]">Set total channels</div>
              <div className="flex flex-wrap items-center gap-3">
                <input
                  type="number"
                  min={0}
                  value={effectiveCount}
                  onChange={(e) => {
                    setCountDirty(true);
                    setCount(e.target.value);
                  }}
                  className="dss-input h-10 w-24 font-mono text-sm font-bold"
                  aria-label="Total channels"
                />
                <label className="flex select-none items-center gap-2 text-sm text-ink-mute">
                  <input
                    type="checkbox"
                    checked={prune}
                    onChange={(e) => setPrune(e.target.checked)}
                    className="h-[17px] w-[17px] rounded accent-accent"
                  />
                  Prune extra
                </label>
              </div>
            </div>
            <div className="flex flex-col items-start gap-1.5">
              <button
                type="button"
                disabled={setChannels.isPending}
                onClick={applyCount}
                className="dss-btn-primary h-10 px-8 text-sm"
              >
                {setChannels.isPending ? "Applying…" : "Apply"}
              </button>
            </div>
          </div>
          {setChannels.isError && (
            <p className="mt-2 text-xs text-danger">{(setChannels.error as Error).message}</p>
          )}
          {setChannels.isSuccess && !setChannels.isPending && (
            <p className="mt-2 flex items-center gap-1 text-xs text-accent-light">
              <CheckIcon size={11} /> Channels set to {setChannels.data.camera_count}.
            </p>
          )}
        </section>

        {/* add channel + refresh ips */}
        <section className="dss-panel p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="dss-label tracking-[1.2px]">
              Channels ({cameras.isLoading ? "…" : channels.length})
            </span>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={createCamera.isPending}
                onClick={() =>
                  createCamera.mutate({ nvr_id: nvrId, channel: nextChannel })
                }
                className="flex h-[30px] items-center gap-1.5 rounded-md border border-white/[.12] bg-panel px-3 text-sm font-semibold text-ink hover:text-ink-bright disabled:opacity-50"
              >
                <PlusIcon size={13} className="text-accent-light" />
                {createCamera.isPending ? "Adding…" : "Add channel"}
              </button>
              <button
                type="button"
                disabled={importIps.isPending}
                onClick={() => importIps.mutate(nvrId)}
                className="flex h-[30px] items-center gap-1.5 rounded-md border border-white/[.07] bg-panel px-3 text-sm font-semibold text-ink-mute hover:text-ink-soft disabled:opacity-50"
              >
                <RefreshIcon size={13} className={importIps.isPending ? "animate-spin" : ""} />
                {importIps.isPending ? "Refreshing…" : "Refresh IPs"}
              </button>
            </div>
          </div>

          {createCamera.isError && (
            <p className="mb-2 text-xs text-danger">{(createCamera.error as Error).message}</p>
          )}
          {importIps.isError && (
            <p className="mb-2 text-xs text-danger">{(importIps.error as Error).message}</p>
          )}
          {importResult && (
            <p className="mb-2 flex items-center gap-1 text-xs text-accent-light">
              <CheckIcon size={11} /> {importResult.message} (found {importResult.found}, updated{" "}
              {importResult.updated})
            </p>
          )}

          {/* table header */}
          <div
            className={`grid ${GRID} gap-2.5 px-3.5 pb-2 text-2xs font-extrabold uppercase tracking-wider text-ink-faint`}
          >
            <span>On</span>
            <span>Ch</span>
            <span>Name</span>
            <span>Camera IP</span>
            <span>Sub</span>
            <span>Main</span>
            <span />
            <span />
          </div>

          {cameras.isLoading ? (
            <ListSkeleton />
          ) : cameras.isError ? (
            <p className="px-2 py-6 text-sm text-danger">
              Failed to load cameras: {(cameras.error as Error).message}
            </p>
          ) : channels.length === 0 ? (
            <div className="rounded-xl border border-white/[.06] bg-deep/60 px-4 py-10 text-center text-sm text-ink-dim">
              No channels yet — set a total above or add one.
            </div>
          ) : (
            <div className="space-y-1.5">
              {channels.map((cam) => (
                <ChannelRow key={cam.id} cam={cam} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/nvrs"
      className="flex w-fit items-center gap-1 text-sm font-semibold text-ink-mute hover:text-ink-soft"
    >
      <ChevronRight size={14} className="rotate-180" />
      Back to recorders
    </Link>
  );
}

function ChannelRow({ cam }: { cam: Camera }) {
  const update = useUpdateCamera();
  const del = useDeleteCamera();

  const [name, setName] = useState(cam.name ?? "");
  const [ip, setIp] = useState(cam.ip ?? "");
  const [confirmDel, setConfirmDel] = useState(false);

  const nameDirty = name !== (cam.name ?? "");
  const ipDirty = ip !== (cam.ip ?? "");
  const dirty = nameDirty || ipDirty;

  function commit() {
    if (!dirty) return;
    update.mutate({
      id: cam.id,
      body: {
        ...(nameDirty ? { name: name.trim() === "" ? null : name.trim() } : {}),
        ...(ipDirty ? { ip: ip.trim() === "" ? null : ip.trim() } : {}),
      },
    });
  }

  function toggle(field: "enabled" | "has_sub" | "has_main") {
    const body: CameraUpdate = { [field]: !cam[field] };
    update.mutate({ id: cam.id, body });
  }

  const error = update.isError ? (update.error as Error).message : del.isError ? (del.error as Error).message : null;

  return (
    <div className="rounded-xl border border-white/[.05] bg-deep/60">
      <div className={`grid ${GRID} items-center gap-2.5 px-3.5 py-2.5`}>
        {/* enabled toggle */}
        <Toggle
          on={cam.enabled}
          disabled={update.isPending}
          title={cam.enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
          onClick={() => toggle("enabled")}
        />

        {/* channel number */}
        <span className="font-mono text-sm font-semibold text-ink-soft">{cam.channel}</span>

        {/* name (editable) */}
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          placeholder={cam.display_name}
          className="h-8 w-full rounded-lg border border-white/[.06] bg-panel px-2.5 font-mono text-sm text-ink-soft outline-none focus:border-accent/40"
        />

        {/* camera ip (editable; empty clears → via NVR) */}
        <div className="flex items-center gap-1.5">
          {!cam.ip && (
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-light shadow-glow"
              title="No direct IP — streamed via NVR"
            />
          )}
          <input
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            placeholder="via NVR"
            className="h-8 w-full rounded-lg border border-white/[.06] bg-panel px-2.5 font-mono text-xs text-ink-soft outline-none placeholder:text-ink-faint focus:border-accent/40"
          />
        </div>

        {/* sub */}
        <Toggle
          on={cam.has_sub}
          disabled={update.isPending}
          title="Sub stream"
          onClick={() => toggle("has_sub")}
        />
        {/* main */}
        <Toggle
          on={cam.has_main}
          disabled={update.isPending}
          title="Main stream"
          onClick={() => toggle("has_main")}
        />

        {/* save (only when dirty) */}
        <div className="flex justify-center">
          {dirty && (
            <button
              type="button"
              disabled={update.isPending}
              onClick={commit}
              className="flex h-6 items-center rounded-md border border-accent/20 bg-accent/[.10] px-2.5 text-xs font-semibold text-accent-light hover:bg-accent/20 disabled:opacity-50"
            >
              {update.isPending ? "…" : "Save"}
            </button>
          )}
        </div>

        {/* delete */}
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setConfirmDel(true)}
            title="Delete channel"
            className="flex h-6 w-6 items-center justify-center rounded-md border border-danger/20 bg-danger/[.10] text-danger hover:bg-danger/20"
          >
            <TrashIcon size={12} />
          </button>
        </div>
      </div>

      {error && <p className="px-3.5 pb-2 text-xs text-danger">{error}</p>}

      {confirmDel && (
        <div className="flex items-center gap-2 border-t border-white/[.06] px-3.5 py-2.5">
          <span className="text-xs text-ink-soft">Delete channel {cam.channel}?</span>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmDel(false)}
              className="dss-btn-ghost h-7 px-3 text-xs"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={del.isPending}
              onClick={() => del.mutate(cam.id, { onSuccess: () => setConfirmDel(false) })}
              className="dss-btn-danger h-7 px-3 text-xs"
            >
              {del.isPending ? "Deleting…" : "Delete"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Toggle({
  on,
  disabled,
  title,
  onClick,
}: {
  on: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={title}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={[
        "dss-focus flex h-[18px] w-[18px] items-center justify-center rounded transition-colors disabled:opacity-50",
        on
          ? "bg-accent text-green-tint"
          : "border border-white/[.18] bg-bg text-transparent hover:border-white/30",
      ].join(" ")}
    >
      <CheckIcon size={11} strokeWidth={3.5} />
    </button>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-1.5">
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className="h-12 animate-pulse rounded-xl border border-white/[.05] bg-deep/60"
        />
      ))}
    </div>
  );
}
