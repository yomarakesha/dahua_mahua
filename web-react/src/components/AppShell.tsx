import { NavLink, Outlet } from "react-router-dom";
import { logout } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { LogoMark } from "./Logo";
import { GridIcon, ServerIcon, GearIcon, PowerIcon } from "./icons";
import type { ComponentType } from "react";

interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<{ size?: number }>;
  adminOnly?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Live", Icon: GridIcon },
  { to: "/nvrs", label: "NVRs", Icon: ServerIcon, adminOnly: true },
  { to: "/settings", label: "Settings", Icon: GearIcon },
];

/** Persistent left icon rail; routed screens render in the outlet. */
export function AppShell() {
  const { isAdmin } = useAuth();
  return (
    <div className="flex h-full w-full overflow-hidden bg-bg">
      <nav className="flex w-14 flex-none flex-col items-center gap-1 border-r border-white/[.06] bg-gradient-to-b from-[#0c1014] to-[#090c0f] py-3">
        <div className="mb-3">
          <LogoMark size={30} />
        </div>
        {NAV.filter((n) => !n.adminOnly || isAdmin).map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={label}
            className={({ isActive }) =>
              [
                "group relative flex h-11 w-11 items-center justify-center rounded-lg transition",
                isActive
                  ? "bg-accent/[.14] text-accent-light ring-1 ring-accent/30"
                  : "text-ink-dim hover:bg-white/[.05] hover:text-ink-soft",
              ].join(" ")
            }
          >
            <Icon size={19} />
            <span className="pointer-events-none absolute left-14 z-20 hidden whitespace-nowrap rounded-md bg-deep px-2 py-1 text-2xs text-ink-soft shadow-panel group-hover:block">
              {label}
            </span>
          </NavLink>
        ))}
        <button
          onClick={() => void logout()}
          title="Sign out"
          className="mt-auto flex h-11 w-11 items-center justify-center rounded-lg text-ink-dim transition hover:bg-danger/[.12] hover:text-danger"
        >
          <PowerIcon size={18} />
        </button>
      </nav>
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
