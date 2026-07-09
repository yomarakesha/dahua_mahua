import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { logout } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { LogoWordmark } from "./Logo";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { GridIcon, ServerIcon, GearIcon, PowerIcon, UsersIcon, FilmIcon, KeyIcon } from "./icons";
import type { ComponentType } from "react";

interface NavItem {
  to: string;
  labelKey: string;
  Icon: ComponentType<{ size?: number }>;
  adminOnly?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", labelKey: "nav.live", Icon: GridIcon },
  { to: "/playback", labelKey: "nav.playback", Icon: FilmIcon },
  { to: "/nvrs", labelKey: "nav.nvrs", Icon: ServerIcon, adminOnly: true },
  { to: "/users", labelKey: "nav.users", Icon: UsersIcon, adminOnly: true },
  { to: "/license", labelKey: "nav.license", Icon: KeyIcon, adminOnly: true },
  { to: "/settings", labelKey: "nav.settings", Icon: GearIcon },
];

/** Single persistent top header: one logo + primary nav + sign-out. Screens
 *  render below (each with its own toolbar). No separate left rail — keeps a
 *  single brand mark and puts navigation in the header. */
export function AppShell() {
  const { isAdmin } = useAuth();
  const { t } = useTranslation();
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-bg">
      <header className="flex h-14 flex-none items-center gap-3 border-b border-white/[.06] bg-gradient-to-b from-[#10151a] to-[#0c1014] px-4">
        <LogoWordmark size={30} />
        <div className="mx-1 h-6 w-px bg-white/[.08]" />
        <nav className="flex items-center gap-1">
          {NAV.filter((n) => !n.adminOnly || isAdmin).map(({ to, labelKey, Icon }) => (
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
              {t(labelKey)}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LanguageSwitcher />
          <button
            onClick={() => void logout()}
            title={t("appShell.signOut")}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-dim transition hover:bg-danger/[.12] hover:text-danger"
          >
            <PowerIcon size={18} />
          </button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
