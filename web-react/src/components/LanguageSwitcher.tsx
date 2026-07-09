import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES, setLanguage, type LanguageCode } from "@/i18n";

/** Compact EN / RU / TK segmented selector. Persists the choice to
 *  localStorage (via setLanguage) so it survives reloads. Lives in the
 *  AppShell header next to sign-out. */
export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const active = (i18n.resolvedLanguage ?? i18n.language ?? "en").slice(0, 2);

  return (
    <div
      role="group"
      aria-label={t("appShell.language")}
      className="flex items-center gap-0.5 rounded-lg border border-white/[.07] bg-white/[.03] p-0.5"
    >
      {SUPPORTED_LANGUAGES.map((lang) => {
        const isActive = active === lang.code;
        return (
          <button
            key={lang.code}
            type="button"
            onClick={() => setLanguage(lang.code as LanguageCode)}
            aria-pressed={isActive}
            title={lang.name}
            className={[
              "flex h-7 min-w-[30px] items-center justify-center rounded-md px-1.5 text-2xs font-bold tracking-wide transition",
              isActive
                ? "bg-accent/[.16] text-accent-light ring-1 ring-accent/30"
                : "text-ink-dim hover:bg-white/[.05] hover:text-ink-soft",
            ].join(" ")}
          >
            {lang.label}
          </button>
        );
      })}
    </div>
  );
}
