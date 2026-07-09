/**
 * i18n bootstrap — English (source), Russian, Turkmen.
 *
 * Resources are bundled at build time (no async backend) so first paint is
 * never blocked and tests import synchronously. The active language is read
 * from / written to localStorage under `LANG_STORAGE_KEY`; the default is
 * English, and any missing key falls back to the English value.
 *
 * NOTE: the brand name is NOT translated — it comes from useBranding().
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import ru from "./ru.json";
import tk from "./tk.json";

export const LANG_STORAGE_KEY = "kanagatly.lang";

export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "EN", name: "English" },
  { code: "ru", label: "RU", name: "Русский" },
  { code: "tk", label: "TK", name: "Türkmen" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

const SUPPORTED_CODES = SUPPORTED_LANGUAGES.map((l) => l.code) as string[];

/** Stored language if valid, else the English default. Safe under SSR/tests. */
export function storedLanguage(): LanguageCode {
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY);
    if (v && SUPPORTED_CODES.includes(v)) return v as LanguageCode;
  } catch {
    /* localStorage unavailable — fall through to default */
  }
  return "en";
}

/** Switch language and persist the choice. */
export function setLanguage(code: LanguageCode): void {
  try {
    localStorage.setItem(LANG_STORAGE_KEY, code);
  } catch {
    /* ignore persistence failures */
  }
  void i18n.changeLanguage(code);
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    ru: { translation: ru },
    tk: { translation: tk },
  },
  lng: storedLanguage(),
  fallbackLng: "en",
  supportedLngs: SUPPORTED_CODES,
  interpolation: { escapeValue: false }, // React already escapes
  returnNull: false,
});

export default i18n;
