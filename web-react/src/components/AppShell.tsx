import { NavLink, Outlet } from "react-router-dom";
import { logout } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { LogoWordmark } from "./Logo";
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

/** Single persistent top header: one logo + primary nav + sign-out. Screens
 *  render below (each with its own toolbar). No separate left rail — keeps a
 *  single brand mark and puts navigation in the header. */
export function AppShell() {
  const { isAdmin } = useAuth();
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-bg">
      <header className="flex h-14 flex-none items-center gap-3 border-b border-white/[.06] bg-gradient-to-b from-[#10151a] to-[#0c1014] px-4">
        <LogoWordmark size={30} />
        <div className="mx-1 h-6 w-px bg-white/[.08]" />
        <nav className="flex items-center gap-1">
          {NAV.filter((n) => !n.adminOnly || isAdmin).map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                [
                  "flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-semibold transition",
                  isActive
                    ? "bg-accent/[.14] text-accent-light ring-1 ring-accent/30"
                    : "text-ink-dim hover:bg-white/[.05] hover:text-ink-soft",
                ].join(" ")
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <button
          onClick={() => void logout()}
          title="Sign out"
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg text-ink-dim transition hover:bg-danger/[.12] hover:text-danger"
        >
          <PowerIcon size={18} />
        </button>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
