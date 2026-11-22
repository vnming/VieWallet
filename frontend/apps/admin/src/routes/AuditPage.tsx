import { ApiError, identityKeys, useIdentityApi, useStaffMe } from "@viewallet/api-client";
import { intlLocale } from "@viewallet/shared";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  InlineAlert,
  PinField,
  Skeleton,
  TextField,
} from "@viewallet/ui";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { FormError } from "../features/auth/FormError";

function VerifyChain() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const me = useStaffMe();
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [code, setCode] = useState("");
  const verify = useMutation({
    mutationFn: api.verifyAudit,
    onError: (error) => {
      if (error instanceof ApiError && error.code === "MFA_STEP_UP_REQUIRED") setStepUpOpen(true);
    },
  });
  const stepUp = useMutation({
    mutationFn: api.stepUp,
    onSuccess: () => {
      setStepUpOpen(false);
      setCode("");
      verify.mutate();
    },
  });

  if (!me.data?.permissions.includes("audit:verify:any")) return null;
  const stepUpNeeded =
    verify.error instanceof ApiError && verify.error.code === "MFA_STEP_UP_REQUIRED";

  return (
    <div className="flex flex-col gap-3">
      <Button
        variant="secondary"
        size="sm"
        loading={verify.isPending}
        onClick={() => verify.mutate()}
      >
        {t("audit.verify")}
      </Button>
      {verify.data &&
        (verify.data.ok ? (
          <InlineAlert tone="success" title={t("audit.chainOk", { count: verify.data.checked })} />
        ) : (
          <InlineAlert
            tone="danger"
            title={t("audit.chainBroken", { seq: verify.data.first_broken_seq })}
          />
        ))}
      {!stepUpNeeded && <FormError error={verify.error} />}
      <Dialog
        open={stepUpOpen}
        onOpenChange={setStepUpOpen}
        title={t("mfa.stepUp.title")}
        description={t("mfa.stepUp.description")}
        closeLabel={t("common.close")}
        footer={
          <Button
            loading={stepUp.isPending}
            disabled={code.length !== 6}
            onClick={() => stepUp.mutate(code)}
          >
            {t("mfa.stepUp.submit")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <FormError error={stepUp.error} />
          <PinField label={t("mfa.code")} masked={false} value={code} onValueChange={setCode} />
        </div>
      </Dialog>
    </div>
  );
}

export function AuditPage() {
  const { t, i18n } = useTranslation();
  const api = useIdentityApi();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [filter, setFilter] = useState("");
  const logs = useQuery({
    queryKey: identityKeys.audit(page, filter),
    queryFn: () => api.auditLogs(page, filter),
    placeholderData: keepPreviousData,
  });
  const format = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  const totalPages = logs.data ? Math.max(1, Math.ceil(logs.data.total / logs.data.page_size)) : 1;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-[1.75rem] font-bold text-fg">{t("audit.title")}</h1>
          <p className="max-w-3xl text-fg-muted">{t("audit.description")}</p>
        </div>
        <VerifyChain />
      </header>
      <Card>
        <form
          className="mb-4 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setFilter(action.trim());
          }}
        >
          <TextField
            label={t("audit.filterAction")}
            placeholder="identity.login"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="min-w-64"
          />
          <Button type="submit" variant="secondary">
            {t("audit.apply")}
          </Button>
        </form>
        {logs.isPending ? (
          <div role="status" aria-label={t("audit.loading")} className="flex flex-col gap-2">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : logs.isError ? (
          <ErrorState
            title={t("status.errorTitle")}
            correlationId={logs.error instanceof ApiError ? logs.error.correlationId : null}
            correlationLabel={t("status.correlation")}
            retryLabel={t("status.retry")}
            onRetry={() => void logs.refetch()}
          />
        ) : logs.data.items.length === 0 ? (
          <EmptyState title={t("audit.empty")} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <caption className="sr-only">{t("audit.title")}</caption>
              <thead className="border-b border-border text-xs uppercase tracking-wide text-fg-muted">
                <tr>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    #
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("audit.columns.time")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("audit.columns.action")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("audit.columns.actor")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("audit.columns.target")}
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    {t("audit.columns.result")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {logs.data.items.map((row) => (
                  <tr key={row.id}>
                    <td className="tabular py-2 pr-3 text-fg-muted">{row.seq}</td>
                    <td className="tabular py-2 pr-3 whitespace-nowrap">
                      {format.format(new Date(row.at))}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">{row.action}</td>
                    <td className="py-2 pr-3">
                      {row.actor_type}
                      {row.actor_id && (
                        <span className="block font-mono text-xs text-fg-muted">
                          {row.actor_id.slice(0, 8)}…
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {row.target_id ? `${row.target_type}:${row.target_id.slice(0, 8)}…` : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone={row.result === "SUCCESS" ? "success" : "danger"}>
                        {row.result}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <nav
          aria-label={t("audit.pagination")}
          className="mt-4 flex items-center justify-between text-sm"
        >
          <span className="text-fg-muted">{t("audit.pageOf", { page, total: totalPages })}</span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              {t("audit.previous")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              {t("audit.next")}
            </Button>
          </div>
        </nav>
      </Card>
    </div>
  );
}
