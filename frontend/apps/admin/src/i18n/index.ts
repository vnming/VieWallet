import { createI18n, type Locale } from "@viewallet/shared";

import en from "./en.json";
import vi from "./vi.json";

export const resources = { vi: { translation: vi }, en: { translation: en } };

export function createAdminI18n(locale?: Locale) {
  return createI18n(resources, locale);
}
