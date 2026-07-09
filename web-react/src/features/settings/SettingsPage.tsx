import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useChangePassword } from "@/api/hooks";
import { useAuth } from "@/lib/auth";
import { logout, ApiError } from "@/api/client";
import { GearIcon, KeyIcon, PowerIcon } from "@/components/icons";
import { PasswordInput } from "@/components/PasswordInput";

const APP_VERSION = "v3.2.0";

/** Reusable labelled field column. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-1 flex-col gap-1.5">
      <span className="dss-label tracking-[1.2px]">{label}</span>
      {children}
    </label>
  );
}

/** ACCOUNT panel — current user identity + must-change notice. */
function AccountPanel() {
  const { t } = useTranslation();
  const { me } = useAuth();
  if (!me) return null;
  const isAdmin = me.role === "admin";
  return (
    <section className="dss-panel p-5">
      <div className="dss-label mb-4 tracking-[1.4px]">{t("settings.account")}</div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex flex-col gap-1">
          <span className="dss-label">{t("settings.username")}</span>
          <span className="font-mono text-base text-ink-bright">{me.username}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="dss-label">{t("settings.role")}</span>
          <span
            className={[
              "inline-flex w-fit items-center rounded-md px-2 py-0.5 text-2xs font-bold uppercase tracking-wider",
              isAdmin
                ? "border border-accent/25 bg-accent/[.12] text-accent-light"
                : "border border-white/[.08] bg-white/[.04] text-ink-mute",
            ].join(" ")}
          >
            {isAdmin ? t("settings.roleAdmin") : t("settings.roleOperator")}
          </span>
        </div>
      </div>
      {me.must_change_password && (
        <div className="mt-4 rounded-md border border-warn/25 bg-warn/[.10] px-3 py-2 text-sm font-medium text-warn">
          {t("settings.mustChangePassword")}
        </div>
      )}
    </section>
  );
}

/** CHANGE DASHBOARD PASSWORD panel. */
function ChangePasswordPanel() {
  const { t } = useTranslation();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (change.isPending) return;
    setError(null);
    setDone(false);

    if (next.length < 8) {
      setError(t("settings.errNewPasswordMin"));
      return;
    }
    if (next !== confirm) {
      setError(t("settings.errPasswordMismatch"));
      return;
    }

    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setConfirm("");
          setDone(true);
        },
        onError: (err) => {
          setError(err instanceof ApiError ? err.message : t("settings.errChangeFailed"));
        },
      },
    );
  }

  return (
    <section className="dss-panel p-5">
      <div className="mb-4 flex items-center gap-2">
        <KeyIcon size={15} className="text-accent-light" />
        <span className="dss-label tracking-[1.4px]">{t("settings.changePasswordTitle")}</span>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("settings.currentPassword")}>
          <PasswordInput
            className="h-[42px]"
            value={current}
            autoComplete="current-password"
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <div className="flex flex-col gap-4 sm:flex-row">
          <Field label={t("settings.newPassword")}>
            <PasswordInput
              className="h-[42px]"
              value={next}
              autoComplete="new-password"
              placeholder={t("settings.newPasswordPlaceholder")}
              onChange={(e) => setNext(e.target.value)}
            />
          </Field>
          <Field label={t("settings.confirmNewPassword")}>
            <PasswordInput
              className="h-[42px]"
              value={confirm}
              autoComplete="new-password"
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}
        {done && <p className="text-sm font-medium text-accent">{t("settings.passwordChanged")}</p>}

        <div>
          <button
            type="submit"
            className="dss-btn-primary h-[42px] px-6"
            disabled={change.isPending || current === "" || next === "" || confirm === ""}
          >
            {change.isPending ? t("settings.changing") : t("settings.change")}
          </button>
        </div>
      </form>
    </section>
  );
}

/** SYSTEM panel — version + sign out. */
function SystemPanel() {
  const { t } = useTranslation();
  return (
    <section className="dss-panel p-5">
      <div className="dss-label mb-4 tracking-[1.4px]">{t("settings.system")}</div>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="dss-label">{t("settings.appVersion")}</span>
          <span className="font-mono text-base text-ink-soft">{APP_VERSION}</span>
        </div>
        <button type="button" className="dss-btn-danger h-[38px] px-4" onClick={() => void logout()}>
          <PowerIcon size={14} />
          {t("settings.signOut")}
        </button>
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const { t } = useTranslation();
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[760px] flex-col gap-4 p-5 lg:p-7">
        <header className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/25 bg-accent/[.12] text-accent-light">
            <GearIcon size={17} />
          </div>
          <div>
            <h1 className="text-[17px] font-extrabold text-ink-bright">{t("settings.title")}</h1>
            <p className="text-sm text-ink-dim">{t("settings.subtitle")}</p>
          </div>
        </header>

        <AccountPanel />
        <ChangePasswordPanel />
        <SystemPanel />
      </div>
    </div>
  );
}
