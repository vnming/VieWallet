import { ApiError } from "@viewallet/api-client";
import { InlineAlert } from "@viewallet/ui";
import { useTranslation } from "react-i18next";

/**
 * Shows a mutation/query error. API errors already carry a localized title (the backend
 * honours Accept-Language); a few codes get an extra, actionable sentence.
 */
export function FormError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  if (!(error instanceof ApiError)) {
    return <InlineAlert tone="danger" title={t("errors.network")} />;
  }
  let extra: string | null = null;
  if (error.code === "PIN_INVALID") {
    const remaining = /remaining_attempts=(\d+)/.exec(error.problem?.detail ?? "")?.[1];
    if (remaining) extra = t("errors.pinRemaining", { count: Number(remaining) });
  } else if (error.code === "ACCOUNT_LOCKED" || error.code === "RATE_LIMITED") {
    extra = t("errors.tryLater");
  } else if (error.code === "PIN_LOCKED") {
    extra =
      error.problem?.detail === "reset_required"
        ? t("errors.pinResetRequired")
        : t("errors.tryLater");
  }
  return (
    <InlineAlert tone="danger" title={error.problem?.title ?? t("errors.generic")}>
      {extra}
      {error.correlationId && (
        <span className="mt-1 block text-xs opacity-80">
          {t("status.correlation")}: <code className="font-mono">{error.correlationId}</code>
        </span>
      )}
    </InlineAlert>
  );
}
