import {
  accountKeys,
  type TicketInput,
  useAccountApi,
  useHelpArticle,
  useHelpArticles,
  useTicket,
  useTickets,
} from "@viewallet/api-client";
import { isLocale } from "@viewallet/shared";
import {
  Badge,
  type BadgeTone,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { type SyntheticEvent, useDeferredValue, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { PageHeader } from "../../features/money/fields";
import { Markdown } from "../../features/support/Markdown";
import { formatDateTime } from "../../features/wallet/format";

const CATEGORIES: TicketInput["category"][] = [
  "TRANSACTION",
  "ACCOUNT",
  "KYC",
  "SECURITY",
  "OTHER",
];
const STATUS_TONE: Record<string, BadgeTone> = {
  OPEN: "info",
  PENDING_CUSTOMER: "warning",
  RESOLVED: "success",
  CLOSED: "neutral",
};

function useLocale(): "vi" | "en" {
  const { i18n } = useTranslation();
  return isLocale(i18n.language) ? i18n.language : "vi";
}

// ------------------------------------------------------------------ help center

export function HelpPage() {
  const { t } = useTranslation();
  const locale = useLocale();
  const [query, setQuery] = useState("");
  const deferred = useDeferredValue(query.trim());
  const articles = useHelpArticles(locale, deferred.length >= 2 ? deferred : "");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("help.title")} description={t("help.description")} />
      <div role="search">
        <TextField
          label={t("help.search")}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <Card>
        {articles.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : articles.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void articles.refetch()} />
        ) : articles.data.length === 0 ? (
          <EmptyState
            title={t("help.noResults")}
            action={
              <Link to="/support/tickets?new=1" className="font-medium text-primary underline">
                {t("help.askUs")}
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {articles.data.map((a) => (
              <li key={a.slug}>
                <Link
                  to={`/help/${a.slug}`}
                  className="-mx-2 flex flex-col gap-0.5 rounded-md px-2 py-3 hover:bg-bg-subtle focus-visible:outline-2 focus-visible:outline-primary"
                >
                  <span className="text-sm font-semibold text-fg">{a.title}</span>
                  <span className="text-sm text-fg-muted">{a.summary}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <p className="text-sm text-fg-muted">
        {t("help.stillStuck")}{" "}
        <Link to="/support/tickets?new=1" className="font-medium text-primary underline">
          {t("help.askUs")}
        </Link>
      </p>
    </div>
  );
}

export function HelpArticlePage() {
  const { t, i18n } = useTranslation();
  const locale = useLocale();
  const { slug = "" } = useParams();
  const article = useHelpArticle(locale, slug);
  const back = (
    <Link
      to="/help"
      className="inline-flex w-fit items-center gap-1 text-sm font-medium text-primary"
    >
      <ArrowLeft aria-hidden className="size-4" />
      {t("help.back")}
    </Link>
  );
  if (article.isPending) return <Skeleton className="h-64 w-full" />;
  if (article.isError) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <EmptyState title={t("help.notFound")} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      {back}
      <article className="flex flex-col gap-4">
        <header className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold text-fg">{article.data.title}</h1>
          <p className="text-fg-muted">{article.data.summary}</p>
        </header>
        <Card>
          <Markdown source={article.data.body} />
        </Card>
        <p className="text-xs text-fg-muted">
          {t("help.updated", { time: formatDateTime(article.data.updated_at, i18n.language) })}
        </p>
      </article>
    </div>
  );
}

// ------------------------------------------------------------------ tickets

function NewTicketForm({ transactionReference }: { transactionReference: string | null }) {
  const { t } = useTranslation();
  const api = useAccountApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<TicketInput["category"]>(
    transactionReference ? "TRANSACTION" : "OTHER",
  );
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const messageId = useId();
  const open = useMutation({
    mutationFn: () =>
      api.openTicket({
        category,
        subject,
        message,
        ...(transactionReference ? { transaction_reference: transactionReference } : {}),
      }),
    onSuccess: async (ticket) => {
      await queryClient.invalidateQueries({ queryKey: accountKeys.tickets });
      void navigate(`/support/tickets/${ticket.reference}`);
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    open.mutate();
  };
  return (
    <Card title={t("support.new")}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        {transactionReference && (
          <p className="text-sm text-fg-muted">
            {t("support.about")} <code className="font-mono">{transactionReference}</code>
          </p>
        )}
        <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
          {t("support.category")}
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as TicketInput["category"])}
            className="h-11 max-w-sm rounded-md border border-border-strong bg-surface px-3 text-sm"
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`support.categories.${c}`)}
              </option>
            ))}
          </select>
        </label>
        <TextField
          label={t("support.subject")}
          maxLength={120}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <label htmlFor={messageId} className="text-sm font-medium text-fg">
            {t("support.message")}
          </label>
          <textarea
            id={messageId}
            rows={5}
            maxLength={4000}
            aria-describedby={`${messageId}-hint`}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="rounded-md border border-border-strong bg-surface p-3 text-sm"
          />
          <span id={`${messageId}-hint`} className="text-xs text-fg-muted">
            {t("support.noSecrets")}
          </span>
        </div>
        <FormError error={open.error} />
        <Button
          type="submit"
          loading={open.isPending}
          disabled={subject.trim().length < 5 || message.trim().length < 10}
          className="w-fit"
        >
          {t("support.send")}
        </Button>
      </form>
    </Card>
  );
}

export function TicketsPage() {
  const { t, i18n } = useTranslation();
  const [params] = useSearchParams();
  const tickets = useTickets();
  const creating = params.get("new") === "1" || params.has("txn");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("support.title")} description={t("support.description")} />
      {creating ? (
        <NewTicketForm transactionReference={params.get("txn")} />
      ) : (
        <Link to="/support/tickets?new=1" className="w-fit font-medium text-primary underline">
          {t("support.new")}
        </Link>
      )}
      <Card title={t("support.mine")}>
        {tickets.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : tickets.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void tickets.refetch()} />
        ) : tickets.data.length === 0 ? (
          <EmptyState title={t("support.empty")} />
        ) : (
          <ul className="divide-y divide-border">
            {tickets.data.map((ticket) => (
              <li key={ticket.reference}>
                <Link
                  to={`/support/tickets/${ticket.reference}`}
                  className="-mx-2 flex items-center justify-between gap-3 rounded-md px-2 py-3 hover:bg-bg-subtle focus-visible:outline-2 focus-visible:outline-primary"
                >
                  <span className="flex flex-col">
                    <span className="text-sm font-medium text-fg">{ticket.subject}</span>
                    <span className="text-xs text-fg-muted">
                      {ticket.reference} · {formatDateTime(ticket.updated_at, i18n.language)}
                    </span>
                  </span>
                  <Badge tone={STATUS_TONE[ticket.status] ?? "neutral"}>
                    {t(`support.status.${ticket.status}`)}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function TicketPage() {
  const { t, i18n } = useTranslation();
  const { reference = "" } = useParams();
  const ticket = useTicket(reference);
  const api = useAccountApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const reply = useMutation({
    mutationFn: () => api.replyTicket(reference, body),
    onSuccess: (updated) => {
      setBody("");
      toast.show({ title: t("support.sent"), tone: "success" });
      queryClient.setQueryData(accountKeys.ticket(reference), updated);
      void queryClient.invalidateQueries({ queryKey: accountKeys.tickets });
    },
  });
  const back = (
    <Link
      to="/support/tickets"
      className="inline-flex w-fit items-center gap-1 text-sm font-medium text-primary"
    >
      <ArrowLeft aria-hidden className="size-4" />
      {t("support.back")}
    </Link>
  );
  if (ticket.isPending) return <Skeleton className="h-64 w-full" />;
  if (ticket.isError) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <EmptyState title={t("support.notFound")} />
      </div>
    );
  }
  const data = ticket.data;
  return (
    <div className="flex flex-col gap-6">
      {back}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-fg">{data.subject}</h1>
        <Badge tone={STATUS_TONE[data.status] ?? "neutral"}>
          {t(`support.status.${data.status}`)}
        </Badge>
      </header>
      {data.transaction_reference && (
        <Link
          to={`/history/${data.transaction_reference}`}
          className="w-fit text-sm font-medium text-primary underline"
        >
          {t("support.about")} {data.transaction_reference}
        </Link>
      )}
      <Card title={t("support.conversation")}>
        <ol className="flex flex-col gap-3">
          {data.messages.map((m) => (
            <li
              key={m.id}
              className={
                m.author === "STAFF"
                  ? "rounded-lg bg-primary-soft p-3 text-sm text-fg"
                  : "rounded-lg bg-bg-subtle p-3 text-sm text-fg"
              }
            >
              <p className="mb-1 text-xs font-medium text-fg-muted">
                {t(`support.author.${m.author}`)} · {formatDateTime(m.created_at, i18n.language)}
              </p>
              <p className="whitespace-pre-line">{m.body}</p>
            </li>
          ))}
        </ol>
      </Card>
      {data.status !== "CLOSED" ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              reply.mutate();
            }}
          >
            <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
              {t("support.reply")}
              <textarea
                rows={3}
                maxLength={4000}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                className="rounded-md border border-border-strong bg-surface p-3 text-sm"
              />
            </label>
            <FormError error={reply.error} />
            <Button
              type="submit"
              loading={reply.isPending}
              disabled={body.trim() === ""}
              className="w-fit"
            >
              {t("support.send")}
            </Button>
          </form>
        </Card>
      ) : (
        <p className="text-sm text-fg-muted">{t("support.closedNote")}</p>
      )}
    </div>
  );
}
