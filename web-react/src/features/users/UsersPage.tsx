import { useMemo, useState } from "react";
import {
  useUsers,
  useCameras,
  useNvrs,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
} from "@/api/hooks";
import { useAuth } from "@/lib/auth";
import type { Camera, Nvr, Role, User } from "@/api/types";
import { PlusIcon, PencilIcon, TrashIcon, XIcon, KeyIcon, ServerIcon } from "@/components/icons";
import { PasswordInput } from "@/components/PasswordInput";

export default function UsersPage() {
  const { data: users, isLoading } = useUsers();
  const { me } = useAuth();
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const del = useDeleteUser();
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <div className="flex h-full flex-col bg-bg">
      <header className="flex h-14 flex-none items-center gap-3 border-b border-white/[.06] px-5">
        <h1 className="text-base font-bold text-ink-bright">Users</h1>
        <span className="text-2xs text-ink-faint">Accounts &amp; per-camera access</span>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="dss-btn-primary ml-auto"
        >
          <PlusIcon size={15} /> Add user
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto p-5">
        <div className="dss-panel overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-white/[.06] text-2xs uppercase tracking-wider text-ink-faint">
                <th className="px-4 py-2.5 font-semibold">User</th>
                <th className="px-4 py-2.5 font-semibold">Role</th>
                <th className="px-4 py-2.5 font-semibold">Status</th>
                <th className="px-4 py-2.5 font-semibold">Cameras</th>
                <th className="px-4 py-2.5 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-ink-faint">
                    Loading…
                  </td>
                </tr>
              ) : (users ?? []).length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-ink-faint">
                    No users.
                  </td>
                </tr>
              ) : (
                (users ?? []).map((u) => (
                  <tr key={u.id} className="border-b border-white/[.04] last:border-0">
                    <td className="px-4 py-2.5 font-medium text-ink">
                      {u.username}
                      {u.id === me?.id && (
                        <span className="ml-2 text-3xs text-ink-faint">(you)</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <RoleBadge role={u.role} />
                    </td>
                    <td className="px-4 py-2.5">
                      {u.is_active ? (
                        <span className="text-accent-light">active</span>
                      ) : (
                        <span className="text-ink-faint">disabled</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink-mute">
                      {u.role === "admin" ? "all" : u.camera_ids.length}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          type="button"
                          onClick={() => setEditing(u)}
                          title="Edit"
                          className="dss-btn-ghost h-8 w-8 !p-0"
                        >
                          <PencilIcon size={14} />
                        </button>
                        {confirmId === u.id ? (
                          <>
                            <button
                              type="button"
                              onClick={() => del.mutate(u.id, { onSettled: () => setConfirmId(null) })}
                              className="dss-btn-danger h-8 px-2 text-xs"
                            >
                              Confirm
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmId(null)}
                              className="dss-btn-ghost h-8 w-8 !p-0"
                            >
                              <XIcon size={14} />
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            disabled={u.id === me?.id}
                            onClick={() => setConfirmId(u.id)}
                            title={u.id === me?.id ? "You can't delete yourself" : "Delete"}
                            className="dss-btn-danger h-8 w-8 !p-0"
                          >
                            <TrashIcon size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <UserEditor
          user={editing === "new" ? null : editing}
          meId={me?.id ?? ""}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function RoleBadge({ role }: { role: Role }) {
  return role === "admin" ? (
    <span className="rounded-sm border border-accent/30 bg-accent/[.12] px-1.5 py-0.5 text-3xs font-bold uppercase tracking-wide text-accent-light">
      admin
    </span>
  ) : (
    <span className="rounded-sm border border-white/[.08] bg-white/[.04] px-1.5 py-0.5 text-3xs font-bold uppercase tracking-wide text-ink-dim">
      operator
    </span>
  );
}

function UserEditor({
  user,
  meId,
  onClose,
}: {
  user: User | null;
  meId: string;
  onClose: () => void;
}) {
  const isNew = user === null;
  const { data: cameras } = useCameras();
  const { data: nvrs } = useNvrs();
  const create = useCreateUser();
  const update = useUpdateUser();

  const [username, setUsername] = useState(user?.username ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>(user?.role ?? "operator");
  const [isActive, setIsActive] = useState(user?.is_active ?? true);
  const [grants, setGrants] = useState<Set<string>>(new Set(user?.camera_ids ?? []));
  const [error, setError] = useState<string | null>(null);

  const isSelf = !isNew && user.id === meId;
  const pending = create.isPending || update.isPending;

  const toggle = (id: string) =>
    setGrants((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function save() {
    setError(null);
    try {
      if (isNew) {
        if (username.trim().length < 2) throw new Error("Username must be at least 2 characters");
        if (password.length < 8) throw new Error("Password must be at least 8 characters");
        await create.mutateAsync({
          username: username.trim(),
          password,
          role,
          is_active: isActive,
          camera_ids: role === "admin" ? [] : [...grants],
        });
      } else {
        if (password && password.length < 8) throw new Error("Password must be at least 8 characters");
        await update.mutateAsync({
          id: user.id,
          body: {
            role: isSelf ? undefined : role,
            is_active: isSelf ? undefined : isActive,
            new_password: password || undefined,
            camera_ids: role === "admin" ? [] : [...grants],
          },
        });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-6 backdrop-blur-sm" onClick={onClose}>
      <div
        className="dss-panel flex max-h-full w-full max-w-2xl flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-none items-center gap-2 border-b border-white/[.06] px-5 py-3">
          <h2 className="text-sm font-bold text-ink-bright">
            {isNew ? "New user" : `Edit ${user.username}`}
          </h2>
          <button type="button" onClick={onClose} className="dss-btn-ghost ml-auto h-8 w-8 !p-0">
            <XIcon size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-auto p-5">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="dss-label">Username</span>
              <input
                className="dss-input mt-1"
                value={username}
                disabled={!isNew}
                autoComplete="username"
                onChange={(e) => setUsername(e.target.value)}
                placeholder="operator1"
              />
            </label>
            <label className="block">
              <span className="dss-label">{isNew ? "Password" : "Reset password"}</span>
              <PasswordInput
                className="mt-1"
                value={password}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isNew ? "min 8 chars" : "leave blank to keep"}
              />
            </label>
          </div>

          <div className="flex items-center gap-4">
            <label className="block">
              <span className="dss-label">Role</span>
              <select
                className="dss-input mt-1 w-40"
                value={role}
                disabled={isSelf}
                onChange={(e) => setRole(e.target.value as Role)}
              >
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label className="mt-5 flex cursor-pointer items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={isActive}
                disabled={isSelf}
                onChange={(e) => setIsActive(e.target.checked)}
                className="h-4 w-4 accent-accent"
              />
              Active
            </label>
            {isSelf && (
              <span className="mt-5 text-2xs text-warn">You can't change your own role/status.</span>
            )}
          </div>

          {role === "admin" ? (
            <div className="rounded-md border border-accent/20 bg-accent/[.06] px-3 py-2 text-xs text-ink-mute">
              Admins can see <span className="text-accent-light">all cameras</span> — no per-camera
              grants needed.
            </div>
          ) : (
            <CameraPicker
              cameras={cameras ?? []}
              nvrs={nvrs ?? []}
              grants={grants}
              onToggle={toggle}
              onSetAll={(ids, on) =>
                setGrants((prev) => {
                  const next = new Set(prev);
                  ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
                  return next;
                })
              }
            />
          )}

          {error && <div className="text-xs text-danger">{error}</div>}
        </div>

        <div className="flex flex-none items-center justify-end gap-2 border-t border-white/[.06] px-5 py-3">
          <button type="button" onClick={onClose} className="dss-btn-ghost">
            Cancel
          </button>
          <button type="button" onClick={() => void save()} disabled={pending} className="dss-btn-primary">
            <KeyIcon size={14} /> {pending ? "Saving…" : isNew ? "Create user" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CameraPicker({
  cameras,
  nvrs,
  grants,
  onToggle,
  onSetAll,
}: {
  cameras: Camera[];
  nvrs: Nvr[];
  grants: Set<string>;
  onToggle: (id: string) => void;
  onSetAll: (ids: string[], on: boolean) => void;
}) {
  const byNvr = useMemo(() => {
    const m = new Map<string, Camera[]>();
    for (const c of cameras) {
      const arr = m.get(c.nvr_id) ?? [];
      arr.push(c);
      m.set(c.nvr_id, arr);
    }
    for (const arr of m.values()) arr.sort((a, b) => a.channel - b.channel);
    return m;
  }, [cameras]);

  const nvrLabel = (id: string) => nvrs.find((n) => n.id === id)?.label ?? id;
  const allIds = cameras.map((c) => c.id);

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="dss-label">Camera access ({grants.size} selected)</span>
        <div className="flex gap-2 text-2xs">
          <button type="button" onClick={() => onSetAll(allIds, true)} className="text-accent-light hover:underline">
            Select all
          </button>
          <button type="button" onClick={() => onSetAll(allIds, false)} className="text-ink-dim hover:underline">
            Clear
          </button>
        </div>
      </div>
      <div className="max-h-64 space-y-3 overflow-auto rounded-md border border-white/[.06] bg-deep/50 p-3">
        {[...byNvr.entries()].map(([nvrId, cams]) => {
          const ids = cams.map((c) => c.id);
          const allOn = ids.every((id) => grants.has(id));
          return (
            <div key={nvrId}>
              <div className="mb-1 flex items-center gap-2">
                <ServerIcon size={13} className="text-ink-dim" />
                <span className="text-xs font-semibold text-ink-soft">{nvrLabel(nvrId)}</span>
                <button
                  type="button"
                  onClick={() => onSetAll(ids, !allOn)}
                  className="text-2xs text-accent-light hover:underline"
                >
                  {allOn ? "none" : "all"}
                </button>
              </div>
              <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
                {cams.map((c) => (
                  <label
                    key={c.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-white/[.04]"
                  >
                    <input
                      type="checkbox"
                      checked={grants.has(c.id)}
                      onChange={() => onToggle(c.id)}
                      className="h-3.5 w-3.5 accent-accent"
                    />
                    <span className="truncate text-xs text-ink-mute">{c.display_name}</span>
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
