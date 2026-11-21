import i18next, { type i18n, type Resource } from "i18next";
import { initReactI18next } from "react-i18next";

export const SUPPORTED_LOCALES = ["vi", "en"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "vi";
const STORAGE_KEY = "vw.locale";

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function storedLocale(): Locale {
  try {
    if (typeof localStorage === "undefined") return DEFAULT_LOCALE;
    const value = localStorage.getItem(STORAGE_KEY);
    return isLocale(value) ? value : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

export function rememberLocale(locale: Locale): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable (private mode); the choice then lasts for the session only.
  }
}

/** Create an isolated i18next instance (one per app, and one per test). */
export function createI18n(resources: Resource, locale: Locale = storedLocale()): i18n {
  const instance = i18next.createInstance();
  void instance.use(initReactI18next).init({
    resources,
    lng: locale,
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: [...SUPPORTED_LOCALES],
    interpolation: { escapeValue: false }, // React already escapes output
    initAsync: false,
  });
  return instance;
}

/** BCP 47 tag used for Intl formatting. */
export function intlLocale(locale: string): string {
  return locale === "en" ? "en-US" : "vi-VN";
}
