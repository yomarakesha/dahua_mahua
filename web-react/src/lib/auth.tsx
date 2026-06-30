import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { getMe, setMe as persistMe } from "@/api/client";
import type { Me } from "@/api/types";

interface AuthCtx {
  me: Me | null;
  isAdmin: boolean;
  setMe: (me: Me | null) => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMeState] = useState<Me | null>(() => getMe());
  const value = useMemo<AuthCtx>(
    () => ({
      me,
      isAdmin: me?.role === "admin",
      setMe: (next) => {
        persistMe(next);
        setMeState(next);
      },
    }),
    [me],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** Route guard: redirect to /login when there's no session. */
export function RequireAuth({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { me, isAdmin } = useAuth();
  const loc = useLocation();
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if (adminOnly && !isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}
