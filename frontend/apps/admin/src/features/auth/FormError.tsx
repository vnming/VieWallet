import { ApiError } from "@viewallet/api-client";
import { InlineAlert } from "@viewallet/ui";
import { useTranslation } from "react-i18next";

export function FormError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  if (!(error instanceof ApiError)) {
    return <InlineAlert tone="danger" title={t("errors.network")} />;
  }
  return (
    <InlineAlert tone="danger" title={error.problem?.title ?? t("errors.generic")}>
      {error.correlationId && (
        <span className="block text-xs opacity-80">
          {t("status.correlation")}: <code className="font-mono">{error.correlationId}</code>
        </span>
      )}
    </InlineAlert>
  );
}
