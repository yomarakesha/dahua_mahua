import { useState, type FormEvent } from "react";
import { useCreateNvr } from "@/api/hooks";
import type { NvrCreate, Vendor } from "@/api/types";
import { PlusIcon, ChevronDown, ChevronRight } from "@/components/icons";
import { PasswordInput } from "@/components/PasswordInput";

const VENDORS: Vendor[] = ["dahua", "hikvision"];

/** Inline "ADD NVR" form panel. Calls useCreateNvr on submit, clears on success. */
export function AddNvrForm() {
  const create = useCreateNvr();
  const [label, setLabel] = useState("");
  const [ip, setIp] = useState("");
  const [password, setPassword] = useState("");
  const [channels, setChannels] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [port, setPort] = useState("");
  const [username, setUsername] = useState("");
  const [vendor, setVendor] = useState<Vendor>("dahua");
  const [group, setGroup] = useState("");

  function reset() {
    setLabel("");
    setIp("");
    setPassword("");
    setChannels("");
    setPort("");
    setUsername("");
    setVendor("dahua");
    setGroup("");
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (create.isPending) return;
    const body: NvrCreate = {
      label: label.trim(),
      ip: ip.trim(),
      rtsp_password: password,
      channels: channels.trim() ? Number(channels) : null,
      vendor,
    };
    if (port.trim()) body.port = Number(port);
    if (username.trim()) body.rtsp_username = username.trim();
    if (group.trim()) body.group = group.trim();
    create.mutate(body, { onSuccess: () => reset() });
  }

  const canSubmit = label.trim() !== "" && ip.trim() !== "" && password !== "";

  return (
    <form onSubmit={submit} className="dss-panel p-4">
      <div className="dss-label mb-3 tracking-[1.4px]">ADD NVR</div>
      <div className="flex flex-wrap items-end gap-3">
        <Field className="min-w-[160px] flex-[2.2]" label="Name">
          <input
            className="dss-input h-[42px]"
            placeholder="Lobby NVR"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </Field>
        <Field className="min-w-[140px] flex-[2]" label="IP address">
          <input
            className="dss-input h-[42px] font-mono"
            placeholder="192.168.1.10"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
          />
        </Field>
        <Field className="min-w-[140px] flex-[2]" label="Password">
          <PasswordInput
            className="h-[42px]"
            placeholder="••••••••"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field className="min-w-[90px] flex-[1.1]" label="Channels">
          <input
            className="dss-input h-[42px]"
            placeholder="auto"
            inputMode="numeric"
            value={channels}
            onChange={(e) => setChannels(e.target.value.replace(/[^\d]/g, ""))}
          />
        </Field>
        <button
          type="submit"
          disabled={!canSubmit || create.isPending}
          className="dss-btn-primary h-[42px] px-6 font-extrabold"
        >
          <PlusIcon size={15} />
          {create.isPending ? "Adding…" : "Add"}
        </button>
      </div>

      <button
        type="button"
        onClick={() => setAdvanced((v) => !v)}
        className="mt-3 flex items-center gap-1.5 text-sm font-semibold text-accent-light"
      >
        Advanced
        {advanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>

      {advanced && (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <Field className="min-w-[100px] flex-1" label="Port">
            <input
              className="dss-input h-[42px] font-mono"
              placeholder="554"
              inputMode="numeric"
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/[^\d]/g, ""))}
            />
          </Field>
          <Field className="min-w-[120px] flex-1" label="Username">
            <input
              className="dss-input h-[42px]"
              placeholder="admin"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>
          <Field className="min-w-[120px] flex-1" label="Vendor">
            <select
              className="dss-input h-[42px]"
              value={vendor}
              onChange={(e) => setVendor(e.target.value as Vendor)}
            >
              {VENDORS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field className="min-w-[120px] flex-1" label="Group">
            <input
              className="dss-input h-[42px]"
              placeholder="optional"
              value={group}
              onChange={(e) => setGroup(e.target.value)}
            />
          </Field>
        </div>
      )}

      {create.isError && (
        <p className="mt-3 text-xs text-danger">{(create.error as Error).message}</p>
      )}
      {create.isSuccess && create.data?.create_notice && (
        <p className="mt-3 text-xs text-warn">{create.data.create_notice}</p>
      )}
    </form>
  );
}

function Field({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={className}>
      <span className="mb-1.5 block text-xs font-semibold text-ink-dim">{label}</span>
      {children}
    </label>
  );
}
