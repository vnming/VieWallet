import { adminKeys, ApiError, type TicketStatus, useAdminApi } from "@viewallet/api-client";
import {
  Badge,
  type BadgeTone,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Skeleton,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { FormError } from "../features/auth/FormError";
import { DataTable, PageHeader, SelectField } from "../features/common";
import { useDateFormat } from "../features/format";

const STATUSES: TicketStatus[] = ["OPEN", "PENDING_CUSTOMER", "RESOLVED", "CLOSED"];
const TONE: Record<TicketStatus, BadgeTone> = {
  OPEN: "warning",
  PENDING_CUSTOMER: "info",
  RESOLVED: "success",
  CLOSED: "neutral",
};

export function TicketsPage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const date = useDateFormat();
  const [status, setStatus] = useState<TicketStatus | "ALL">("OPEN");
  const list = useQuery({
    queryKey: adminKeys.tickets(status),
    queryFn: () => api.tickets(status === "ALL" ? null : status),
  });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("tickets.title")} description={t("tickets.description")} />
      <SelectField
        label={t("tickets.filter")}
        value={status}
        options={["ALL", ...STATUSES] as const}
        onChange={setStatus}
        labelFor={(s) => t(`tickets.status.${s}`)}
        className="w-64"
      />
      <Card>
        {list.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : list.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("tickets.error")}
            onRetry={() => void list.refetch()}
          />
        ) : list.data.items.length === 0 ? (
          <EmptyState headingLevel={2} title={t("tickets.empty")} />
        ) : (
          <DataTable
            caption={t("tickets.title")}
            headers={[
              t("tickets.reference"),
              t("tickets.subject"),
              t("tickets.statusLabel"),
              t("tickets.updated"),
            ]}
          >
            {list.data.items.map((ticket) => (
              <tr key={ticket.reference}>
                <td className="py-2 pr-3 font-mono text-xs">
                  <Link
                    to={`/tickets/${ticket.reference}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    {ticket.reference}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  {ticket.subject}
                  <span className="block text-xs text-fg-muted">
                    {t(`tickets.category.${ticket.category}`, { defaultValue: ticket.category })}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  <Badge tone={TONE[ticket.status]}>{t(`tickets.status.${ticket.status}`)}</Badge>
                </td>
                <td className="py-2 pr-3">{date(ticket.updated_at)}</td>
              </tr>
            ))}
          </DataTable>
        )}
      </Card>
    </div>
  );
}

export function TicketDetailPage() {
  const { t } = useTranslation();
  const { reference = "" } = useParams();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const date = useDateFormat();
  const bodyId = useId();
  const hintId = useId();
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const [status, setStatus] = useState<TicketStatus | "KEEP">("KEEP");
  const ticket = useQuery({
    queryKey: adminKeys.ticket(reference),
    queryFn: () => api.ticket(reference),
  });
  const reply = useMutation({
    mutationFn: () =>
      api.replyTicket(reference, { body, internal, status: status === "KEEP" ? null : status }),
    onSuccess: async (updated) => {
      queryClient.setQueryData(adminKeys.ticket(reference), updated);
      setBody("");
      setInternal(false);
      setStatus("KEEP");
      toast.show({ title: t("tickets.sent"), tone: "success" });
      await queryClient.invalidateQueries({ queryKey: adminKeys.allTickets });
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    reply.mutate();
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={ticket.data?.subject ?? t("tickets.detailTitle", { reference })} />
      {ticket.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : ticket.isError ? (
        ticket.error instanceof ApiError && ticket.error.status === 404 ? (
          <EmptyState headingLevel={2} title={t("tickets.notFound")} />
        ) : (
          <ErrorState
            headingLevel={2}
            title={t("tickets.error")}
            onRetry={() => void ticket.refetch()}
          />
        )
      ) : (
        <>
          <Card>
            <p className="flex flex-wrap items-center gap-2 text-sm text-fg-muted">
              <span className="font-mono">{ticket.data.reference}</span>
              <Badge tone={TONE[ticket.data.status]}>
                {t(`tickets.status.${ticket.data.status}`)}
              </Badge>
              {ticket.data.transaction_reference && (
                <Link
                  to={`/transactions/${ticket.data.transaction_reference}`}
                  className="font-mono text-primary underline-offset-4 hover:underline"
                >
                  {ticket.data.transaction_reference}
                </Link>
              )}
              <Link
                to={`/users/${ticket.data.user_id}`}
                className="text-primary underline-offset-4 hover:underline"
              >
                {t("tickets.customer")}
              </Link>
            </p>
          </Card>
          <Card title={t("tickets.conversation")}>
            <ol className="flex flex-col gap-3">
              {ticket.data.messages.map((m) => (
                <li
                  key={m.id}
                  className={
                    m.internal
                      ? "rounded-lg border border-dashed border-warning-500 bg-warning-100/40 p-3"
                      : m.author === "STAFF"
                        ? "rounded-lg bg-primary-soft p-3"
                        : "rounded-lg bg-bg-subtle p-3"
                  }
                >
                  <p className="mb-1 text-xs text-fg-muted">
                    {m.internal ? t("tickets.internalNote") : t(`tickets.author.${m.author}`)} ·{" "}
                    {date(m.created_at)}
                  </p>
                  <p className="whitespace-pre-wrap text-sm text-fg">{m.body}</p>
                </li>
              ))}
            </ol>
          </Card>
          <Card title={t("tickets.reply")}>
            <form onSubmit={submit} className="flex flex-col gap-3">
              <label htmlFor={bodyId} className="text-sm font-medium text-fg">
                {t("tickets.message")}
              </label>
              <textarea
                id={bodyId}
                aria-describedby={hintId}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                maxLength={4000}
                rows={4}
                className="rounded-md border border-border-strong bg-surface p-3 text-sm"
              />
              <p id={hintId} className="text-xs text-fg-muted">
                {t("tickets.messageHint")}
              </p>
              <label className="flex items-center gap-2 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={internal}
                  onChange={(e) => setInternal(e.target.checked)}
                />
                {t("tickets.markInternal")}
              </label>
              <SelectField
                label={t("tickets.setStatus")}
                value={status}
                options={["KEEP", ...STATUSES] as const}
                onChange={setStatus}
                labelFor={(s) =>
                  s === "KEEP" ? t("tickets.keepStatus") : t(`tickets.status.${s}`)
                }
                className="w-64"
              />
              <FormError error={reply.error} />
              <Button
                type="submit"
                className="w-fit"
                loading={reply.isPending}
                disabled={body.trim() === "" && status === "KEEP"}
              >
                {t("tickets.send")}
              </Button>
            </form>
          </Card>
        </>
      )}
    </div>
  );
}
