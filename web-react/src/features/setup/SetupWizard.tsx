import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useChangePassword, useNvrs } from "@/api/hooks";
import { ApiError } from "@/api/client";
import { useAuth } from "@/lib/auth";
import { useBranding, splitBrandName } from "@/lib/branding";
import { LogoMark } from "@/components/Logo";
import { PasswordInput } from "@/components/PasswordInput";
import { AddNvrForm } from "@/features/nvrs/AddNvrForm";
import { CheckIcon, KeyIcon, ServerIcon } from "@/components/icons";
import { dismissSetup, useSetupStatus } from "./useSetupStatus";
import type { ComponentType } from "react";

type StepId = "password" | "nvr" | "done";

interface StepDef {
  id: StepId;
  labelKey: string;
  Icon: ComponentType<{ size?: number }>;
}

/**
 * First-run setup wizard — a full-screen stepper that takes a fresh deployment
 * from zero to live: (1) change the bootstrap password, (2) add the first NVR,
 * (3) done. Steps are dynamic: the password step is dropped once the account is
 * already secured. Reuses the change-password mutation and AddNvrForm so there
 * is no duplicated form logic.
 */
export default function SetupWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const brand = useBranding();
  const { head, tail } = splitBrandName(brand.name);
  const status = useSetupStatus();

  // Freeze the step set on mount: whether the password step is present is
  // decided once (it disappears mid-wizard as soon as the password changes,
  // which would otherwise shuffle indices under the user).
  const [includePassword] = useState(() => status.needsPasswordChange);
  const steps = useMemo<StepDef[]>(() => {
    const list: StepDef[] = [];
    if (includePassword) list.push({ id: "password", labelKey: "setup.stepPassword", Icon: KeyIcon });
    list.push({ id: "nvr", labelKey: "setup.stepNvr", Icon: ServerIcon });
    list.push({ id: "done", labelKey: "setup.stepDone", Icon: CheckIcon });
    return list;
  }, [includePassword]);

  const [index, setIndex] = useState(0);
  const step = steps[index];
  const isLast = index === steps.length - 1;

  function finish() {
    dismissSetup();
    navigate("/", { replace: true });
  }

  return (
    <div className="relative flex min-h-screen flex-col items-center overflow-y-auto bg-bg font-sans">
      {/* radial-glow backdrop matching the sign-in page */}
      <div
        className="pointer-events-none fixed inset-0"
        style={{
          background:
            "radial-gradient(90% 70% at 50% 0%,#0f1418 0%,#080a0c 60%,#060708 100%)",
        }}
      />
      <div
        className="pointer-events-none fixed inset-0"
        style={{
          background:
            "radial-gradient(50% 40% at 50% 30%,rgb(var(--brand-primary) / .10) 0%,transparent 70%)",
        }}
      />

      <div className="relative z-10 flex w-full max-w-[640px] flex-col gap-6 px-4 py-10 sm:py-14">
        {/* brand + heading */}
        <div className="flex items-center gap-3">
          <LogoMark size={40} />
          <div>
            <div className="text-xl font-extrabold tracking-tight text-ink-bright">
              {head ? `${head} ` : ""}
              <span className="text-accent">{tail}</span>
            </div>
            <div className="text-sm font-medium tracking-wide text-ink-dim">
              {t("setup.subtitle")}
            </div>
          </div>
        </div>

        <Stepper steps={steps} index={index} />

        <div
          className="rounded-[18px] border border-white/[.07] p-6 shadow-[0_30px_80px_rgba(0,0,0,.45),inset_0_1px_0_rgba(255,255,255,.05)] backdrop-blur-xl sm:p-8"
          style={{
            background:
              "linear-gradient(180deg,rgba(20,26,30,.82),rgba(13,17,20,.92))",
          }}
        >
          <div className="mb-1 text-2xs font-bold uppercase tracking-[1.4px] text-accent-light">
            {t("setup.stepOf", { current: index + 1, total: steps.length })}
          </div>

          {step.id === "password" && (
            <PasswordStep onDone={() => setIndex((i) => i + 1)} />
          )}
          {step.id === "nvr" && <NvrStep />}
          {step.id === "done" && <DoneStep onFinish={finish} />}

          {/* footer nav */}
          {step.id !== "done" && (
            <div className="mt-7 flex items-center gap-3 border-t border-white/[.06] pt-5">
              <button
                type="button"
                onClick={() => setIndex((i) => Math.max(0, i - 1))}
                disabled={index === 0}
                className="dss-btn-ghost h-[42px] px-4 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("common.back")}
              </button>
              <div className="ml-auto flex items-center gap-3">
                {step.id === "nvr" && (
                  <button
                    type="button"
                    onClick={() => setIndex((i) => i + 1)}
                    className="dss-btn-ghost h-[42px] px-4"
                  >
                    {t("setup.skipNvr")}
                  </button>
                )}
                {step.id === "nvr" && (
                  <button
                    type="button"
                    onClick={() => (isLast ? finish() : setIndex((i) => i + 1))}
                    className="dss-btn-primary h-[42px] px-6"
                  >
                    {t("setup.next")}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={finish}
          className="mx-auto text-sm font-medium text-ink-faint transition hover:text-ink-mute"
        >
          {t("setup.exitToApp")}
        </button>
      </div>
    </div>
  );
}

/** Horizontal progress indicator: numbered/checked dots + labels. */
function Stepper({ steps, index }: { steps: StepDef[]; index: number }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center">
      {steps.map((s, i) => {
        const done = i < index;
        const active = i === index;
        return (
          <div key={s.id} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-2.5">
              <div
                className={[
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-bold transition",
                  active
                    ? "border-accent/50 bg-accent/[.16] text-accent-light shadow-[0_0_0_4px_rgb(var(--brand-primary)_/_.10)]"
                    : done
                      ? "border-accent/40 bg-accent/[.12] text-accent-light"
                      : "border-white/[.10] bg-white/[.03] text-ink-faint",
                ].join(" ")}
              >
                {done ? <CheckIcon size={16} /> : i + 1}
              </div>
              <span
                className={[
                  "hidden text-sm font-semibold sm:block",
                  active ? "text-ink-bright" : done ? "text-ink-mute" : "text-ink-faint",
                ].join(" ")}
              >
                {t(s.labelKey)}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div
                className={[
                  "mx-3 h-px flex-1 transition",
                  done ? "bg-accent/40" : "bg-white/[.08]",
                ].join(" ")}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Step 1 — change the bootstrap password (reuses useChangePassword). */
function PasswordStep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const { me, setMe } = useAuth();
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (change.isPending) return;
    setError(null);
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
          // Clear the must-change flag locally so the gate/settings reflect it
          // without a re-login.
          if (me) setMe({ ...me, must_change_password: false });
          onDone();
        },
        onError: (err) =>
          setError(err instanceof ApiError ? err.message : t("settings.errChangeFailed")),
      },
    );
  }

  return (
    <form onSubmit={submit}>
      <h2 className="text-lg font-extrabold text-ink-bright">{t("setup.passwordTitle")}</h2>
      <p className="mt-1.5 text-sm text-ink-dim">{t("setup.passwordDesc")}</p>

      <div className="mt-5 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="dss-label tracking-[1.2px]">{t("settings.currentPassword")}</span>
          <PasswordInput
            className="h-[42px]"
            value={current}
            autoComplete="current-password"
            autoFocus
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <div className="flex flex-col gap-4 sm:flex-row">
          <label className="flex flex-1 flex-col gap-1.5">
            <span className="dss-label tracking-[1.2px]">{t("settings.newPassword")}</span>
            <PasswordInput
              className="h-[42px]"
              value={next}
              autoComplete="new-password"
              placeholder={t("settings.newPasswordPlaceholder")}
              onChange={(e) => setNext(e.target.value)}
            />
          </label>
          <label className="flex flex-1 flex-col gap-1.5">
            <span className="dss-label tracking-[1.2px]">{t("settings.confirmNewPassword")}</span>
            <PasswordInput
              className="h-[42px]"
              value={confirm}
              autoComplete="new-password"
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
        </div>
        {error && (
          <p aria-live="polite" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div>
          <button
            type="submit"
            disabled={change.isPending || current === "" || next === "" || confirm === ""}
            className="dss-btn-primary h-[42px] px-6"
          >
            {change.isPending ? t("settings.changing") : t("setup.saveAndContinue")}
          </button>
        </div>
      </div>
    </form>
  );
}

/** Step 2 — add the first NVR (reuses AddNvrForm; watches useNvrs for success). */
function NvrStep() {
  const { t } = useTranslation();
  const nvrs = useNvrs();
  // Baseline count captured on mount so we can detect the first NVR the admin
  // adds during this step, then surface its detected channel count.
  const [baseline] = useState(() => nvrs.data?.length ?? 0);
  const list = nvrs.data ?? [];
  const added = list.length > baseline;
  const newest = added
    ? [...list].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
    : null;

  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink-bright">{t("setup.nvrTitle")}</h2>
      <p className="mt-1.5 text-sm text-ink-dim">{t("setup.nvrDesc")}</p>

      <div className="mt-5">
        <AddNvrForm />
      </div>

      {newest && (
        <div className="mt-4 flex items-center gap-2 rounded-md border border-accent/25 bg-accent/[.10] px-3 py-2.5 text-sm font-medium text-accent-light">
          <CheckIcon size={16} className="shrink-0" />
          {t("setup.nvrAdded", { label: newest.label, count: newest.camera_count })}
        </div>
      )}

      {!added && (
        <p className="mt-4 text-2xs text-ink-faint">{t("setup.nvrSkipHint")}</p>
      )}
    </div>
  );
}

/** Step 3 — done. */
function DoneStep({ onFinish }: { onFinish: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="py-2 text-center">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-accent/40 bg-accent/[.14] text-accent-light">
        <CheckIcon size={28} />
      </div>
      <h2 className="mt-4 text-xl font-extrabold text-ink-bright">{t("setup.doneTitle")}</h2>
      <p className="mx-auto mt-2 max-w-[420px] text-sm text-ink-dim">{t("setup.doneDesc")}</p>
      <button type="button" onClick={onFinish} className="dss-btn-primary mx-auto mt-6 h-[46px] px-8 text-[15px]">
        {t("setup.goToLive")}
      </button>
    </div>
  );
}
