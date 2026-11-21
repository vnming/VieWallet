import { ApiError, useSystemStatus } from "@viewallet/api-client";
import { intlLocale } from "@viewallet/shared";
import { Card, SystemStatusPanel } from "@viewallet/ui";
import { useTranslation } from "react-i18next";

export function OverviewPage() {
  const { t, i18n } = useTranslation();
  const query = useSystemStatus();
  const formatter = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <h1 className="text-[1.75rem] font-bold text-fg">{t("overview.title")}</h1>
        <p className="max-w-3xl text-fg-muted">{t("overview.intro")}</p>
      </section>
      <Card
        title={t("overview.statusTitle")}
        className="max-w-3xl"
        aria-busy={query.isLoading || undefined}
      >
        <SystemStatusPanel
          labels={{
            heading: t("overview.statusTitle"),
            loading: t("status.loading"),
            overallOk: t("status.overallOk"),
            overallFail: t("status.overallFail"),
            version: t("status.version"),
            environment: t("status.environment"),
            serverTime: t("status.serverTime"),
            checks: {
              database: t("status.checks.database"),
              migrations: t("status.checks.migrations"),
              redis: t("status.checks.redis"),
            },
            ok: t("status.ok"),
            fail: t("status.fail"),
            errorTitle: t("status.errorTitle"),
            errorDescription: t("status.errorDescription"),
            retry: t("status.retry"),
            correlation: t("status.correlation"),
          }}
          data={query.data}
          isLoading={query.isLoading}
          error={
            query.error
              ? {
                  correlationId: query.error instanceof ApiError ? query.error.correlationId : null,
                }
              : null
          }
          onRetry={() => void query.refetch()}
          formatTime={(iso) => formatter.format(new Date(iso))}
        />
      </Card>
    </div>
  );
}
