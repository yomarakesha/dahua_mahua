import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { login } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { LogoMark } from "@/components/Logo";

/** 01 · Sign in — centered dark glass card over a radial-glow backdrop. */
export default function LoginPage() {
  const { me, setMe } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Already authenticated → bounce to the live wall.
  if (me) return <Navigate to="/" replace />;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      const result = await login(username.trim(), password);
      setMe(result.me);
      // Always land on the live wall — no forced password change after login.
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setPending(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg font-sans">
      {/* radial accent glow + faint grid backdrop */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(90% 70% at 50% 0%,#0f1418 0%,#080a0c 60%,#060708 100%)",
        }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(50% 40% at 50% 38%,rgba(46,204,113,.10) 0%,transparent 70%)",
        }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px)",
          backgroundSize: "46px 46px",
          maskImage: "radial-gradient(70% 60% at 50% 45%,#000 0%,transparent 80%)",
          WebkitMaskImage: "radial-gradient(70% 60% at 50% 45%,#000 0%,transparent 80%)",
        }}
      />

      {/* corner brand */}
      <div className="absolute left-6 top-7 flex items-center gap-2.5 sm:left-9 sm:top-8">
        <LogoMark size={30} />
        <div className="text-sm font-bold tracking-wider text-ink-mute">
          KANAGATLY <span className="text-accent">VMS</span>
        </div>
      </div>

      {/* footer meta — honest: the server host this client is pointed at */}
      <div className="absolute bottom-7 right-6 font-mono text-xs tracking-wide text-ink-faint sm:right-9">
        {window.location.host}
      </div>

      {/* sign-in card */}
      <form
        onSubmit={onSubmit}
        className="relative z-10 w-[404px] max-w-[calc(100vw-32px)] rounded-[18px] border border-white/[.07] p-10 shadow-[0_30px_80px_rgba(0,0,0,.55),inset_0_1px_0_rgba(255,255,255,.05)] backdrop-blur-xl"
        style={{
          background:
            "linear-gradient(180deg,rgba(20,26,30,.82),rgba(13,17,20,.92))",
        }}
      >
        {/* heading */}
        <div className="mb-1.5 flex items-center gap-3">
          <LogoMark size={40} />
          <div>
            <div className="text-xl font-extrabold tracking-tight text-ink-bright">
              Kanagatly <span className="text-accent">VMS</span>
            </div>
            <div className="text-sm font-medium tracking-wide text-ink-dim">
              Video surveillance console
            </div>
          </div>
        </div>

        <div className="my-6 h-px bg-gradient-to-r from-transparent via-white/[.08] to-transparent" />

        {/* username */}
        <label htmlFor="login-username" className="dss-label mb-2 block">
          Username
        </label>
        <div className="mb-[18px] flex h-[46px] items-center gap-2.5 rounded-[11px] border border-white/[.07] bg-deep px-3.5 focus-within:border-accent/40 focus-within:shadow-[0_0_0_3px_rgba(46,204,113,.10)]">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="shrink-0 text-ink-faint"
          >
            <circle cx="12" cy="8" r="4" />
            <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
          </svg>
          <input
            id="login-username"
            type="text"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
            className="w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-ink-faint"
          />
        </div>

        {/* password */}
        <label htmlFor="login-password" className="dss-label mb-2 block">
          Password
        </label>
        <div className="mb-[26px] flex h-[46px] items-center gap-2.5 rounded-[11px] border border-white/[.07] bg-deep px-3.5 focus-within:border-accent/40 focus-within:shadow-[0_0_0_3px_rgba(46,204,113,.10)]">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="shrink-0 text-ink-faint"
          >
            <rect x="4" y="10" width="16" height="11" rx="2" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
          <input
            id="login-password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-ink-faint"
          />
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            className="ml-auto shrink-0 text-ink-faint transition hover:text-ink-mute"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </button>
        </div>

        {error && (
          <div
            aria-live="polite"
            className="mb-3 rounded-md border border-danger/25 bg-danger/[.10] px-3 py-2 text-sm text-danger"
          >
            {error}
          </div>
        )}

        {/* submit */}
        <button
          type="submit"
          disabled={pending}
          className="group flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-bold tracking-wide text-deep shadow-[0_10px_28px_rgba(46,204,113,.28),inset_0_1px_0_rgba(255,255,255,.3)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-60"
          style={{ background: "linear-gradient(180deg,#34d97e,#22b864)" }}
        >
          {pending ? "Signing in…" : "Sign in"}
          {!pending && (
            <svg
              width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}
              className="transition-transform group-hover:translate-x-0.5"
            >
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          )}
        </button>

        <div className="mt-[18px] text-center text-sm text-ink-dim">
          Forgot credentials?{" "}
          <span className="font-semibold text-accent">Contact admin</span>
        </div>
      </form>
    </div>
  );
}
