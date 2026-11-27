/** Formatting helpers shared by the admin pages. */
import { intlLocale } from "@viewallet/shared";
import type { BadgeTone } from "@viewallet/ui";
import { useTranslation } from "react-i18next";

export function useDateFormat() {
  const { i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  return (iso: string | null | undefined) => (iso ? format.format(new Date(iso)) : "—");
}

export const HEALTH_TONE: Record<string, BadgeTone> = {
  UP: "success",
  DEGRADED: "warning",
  DOWN: "danger",
  NOT_CONFIGURED: "neutral",
};
