import {
  newIdempotencyKey,
  useAccountApi,
  useCategories,
  useExportJob,
  financeKeys,
  type TransactionFilters,
  type TxnStatus,
  type TxnType,
  useFinanceApi,
  useTransaction,
  useTransactions,
  useWallet,
} from "@viewallet/api-client";
import {
  AmountText,
  Button,
  buttonClassName,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  InlineAlert,
  PinField,
  Skeleton,
  STATUS_STYLES,
  StatusBadge,
  StatusTimeline,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Snowflake } from "lucide-react";
import { type ReactNode, type SyntheticEvent, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { BalanceCard } from "../../features/wallet/BalanceCard";
import { amountLocale, formatDateTime, uiLocale } from "../../features/wallet/format";
import { TransactionList } from "../../features/wallet/TransactionList";

const TYPES: TxnType[] = [
  "DEPOSIT",
  "WITHDRAWAL",
  "TRANSFER",
  "PAYMENT",
  "REFUND",
  "REVERSAL",
  "ADJUSTMENT",
];
const STATUSES: TxnStatus[] = ["PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"];

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-10 rounded-full" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  );
}

/** Home-page block: balance card and the five latest transactions. */
export function WalletOverview() {
  const { t } = useTranslation();
  const wallet = useWallet();
  const recent = useTransactions({}, 5);
  if (wallet.isError) {
    return <ErrorState title={t("wallet.loadError")} onRetry={() => void wallet.refetch()} />;
  }
  const items = recent.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-6">
      <BalanceCard wallet={wallet.data} />
      <nav aria-label={t("wallet.quickActions")} className="flex flex-wrap gap-3">
        <Link to="/transfer" className={buttonClassName()}>
          {t("nav.transfer")}
        </Link>
        <Link to="/scan" className={buttonClassName({ variant: "secondary" })}>
          {t("nav.scan")}
        </Link>
        <Link to="/receive" className={buttonClassName({ variant: "secondary" })}>
          {t("receive.title")}
        </Link>
        <Link to="/request" className={buttonClassName({ variant: "ghost" })}>
          {t("request.title")}
        </Link>
      </nav>
      <Card
        title={t("wallet.recent")}
        actions={
          items.length > 0 && (
            <Link
              to="/history"
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              {t("wallet.viewAll")}
            </Link>
          )
        }
      >
        {recent.isPending ? (
          <ListSkeleton />
        ) : recent.isError ? (
          <ErrorState title={t("history.error")} onRetry={() => void recent.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState title={t("history.empty")} description={t("history.emptyBody")} />
        ) : (
          <TransactionList items={items} />
        )}
      </Card>
    </div>
  );
}

function FreezeControls({ status, freezeSource }: { status: string; freezeSource: string | null }) {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [pin, setPin] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: financeKeys.wallet });

  const freeze = useMutation({
    mutationFn: () => api.freezeWallet("self-service"),
    onSuccess: async () => {
      setOpen(false);
      toast.show({ title: t("wallet.frozenDone"), tone: "success" });
      await refresh();
    },
  });
  const unfreeze = useMutation({
    mutationFn: () => api.unfreezeWallet(pin),
    onSuccess: async () => {
      setOpen(false);
      setPin("");
      toast.show({ title: t("wallet.unfreezeDone"), tone: "success" });
      await refresh();
    },
  });

  if (status === "ACTIVE") {
    return (
      <>
        <Button variant="secondary" onClick={() => setOpen(true)}>
          <Snowflake aria-hidden className="size-4" />
          {t("wallet.freeze")}
        </Button>
        <Dialog
          open={open}
          onOpenChange={setOpen}
          title={t("wallet.freezeTitle")}
          description={t("wallet.freezeBody")}
          closeLabel={t("common.close")}
          footer={
            <>
              <Button variant="secondary" onClick={() => setOpen(false)}>
                {t("wallet.cancel")}
              </Button>
              <Button variant="danger" loading={freeze.isPending} onClick={() => freeze.mutate()}>
                {t("wallet.freezeConfirm")}
              </Button>
            </>
          }
        >
          <FormError error={freeze.error} />
        </Dialog>
      </>
    );
  }
  if (status !== "FROZEN") return null;
  const self = freezeSource === "SELF";
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    unfreeze.mutate();
  };
  return (
    <div className="flex flex-col gap-3">
      <InlineAlert tone="warning" title={t("wallet.frozenTitle")}>
        {self ? t("wallet.frozenSelf") : t("wallet.frozenStaff")}
      </InlineAlert>
      {self && (
        <>
          <Button onClick={() => setOpen(true)} className="w-fit">
            {t("wallet.unfreeze")}
          </Button>
          <Dialog
            open={open}
            onOpenChange={setOpen}
            title={t("wallet.unfreezeTitle")}
            description={t("wallet.unfreezeBody")}
            closeLabel={t("common.close")}
          >
            <form onSubmit={submit} className="flex flex-col gap-4">
              <PinField label={t("pin.current")} value={pin} onValueChange={setPin} />
              <FormError error={unfreeze.error} />
              <Button type="submit" loading={unfreeze.isPending} disabled={pin.length !== 6}>
                {t("wallet.unfreeze")}
              </Button>
            </form>
          </Dialog>
        </>
      )}
    </div>
  );
}

export function WalletPage() {
  const { t } = useTranslation();
  const wallet = useWallet();
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-fg">{t("wallet.title")}</h1>
        <p className="text-fg-muted">{t("wallet.description")}</p>
      </header>
      {wallet.isError ? (
        <ErrorState title={t("wallet.loadError")} onRetry={() => void wallet.refetch()} />
      ) : (
        <>
          <BalanceCard wallet={wallet.data} />
          <nav aria-label={t("wallet.title")} className="flex flex-wrap gap-3">
            <Link to="/transfer" className={buttonClassName()}>
              {t("nav.transfer")}
            </Link>
            <Link to="/deposit" className={buttonClassName({ variant: "secondary" })}>
              {t("nav.deposit")}
            </Link>
            <Link to="/withdraw" className={buttonClassName({ variant: "secondary" })}>
              {t("nav.withdraw")}
            </Link>
            <Link to="/wallet/destinations" className={buttonClassName({ variant: "ghost" })}>
              {t("destinations.title")}
            </Link>
            <Link to="/wallet/limits" className={buttonClassName({ variant: "ghost" })}>
              {t("limits.title")}
            </Link>
          </nav>
          {wallet.data && (
            <FreezeControls status={wallet.data.status} freezeSource={wallet.data.freeze_source} />
          )}
        </>
      )}
    </div>
  );
}

function FilterSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  labelFor,
}: {
  label: string;
  value: T | undefined;
  options: T[];
  onChange: (value: T | undefined) => void;
  labelFor: (value: T) => string;
}) {
  const { t } = useTranslation();
  return (
    <label className="flex flex-col gap-1 text-sm text-fg-muted">
      {label}
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? undefined : (e.target.value as T))}
        className="h-10 rounded-md border border-border bg-surface px-3 text-sm text-fg"
      >
        <option value="">{t("history.all")}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {labelFor(o)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ExportPanel() {
  const { t } = useTranslation();
  const api = useAccountApi();
  const today = todayInVietnam();
  const [from, setFrom] = useState(firstOfMonth(today));
  const [to, setTo] = useState(today);
  const [jobId, setJobId] = useState<string | null>(null);
  const [exportKey, setExportKey] = useState(newIdempotencyKey);
  const job = useExportJob(jobId);
  const create = useMutation({
    mutationFn: () => api.createExport(from, to, exportKey),
    onSuccess: (created) => {
      setJobId(created.id);
      setExportKey(newIdempotencyKey());
    },
  });
  return (
    <Card title={t("history.export.title")}>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <TextField
          label={t("history.from")}
          type="date"
          value={from}
          max={to}
          onChange={(e) => setFrom(e.target.value)}
        />
        <TextField
          label={t("history.to")}
          type="date"
          value={to}
          min={from}
          max={today}
          onChange={(e) => setTo(e.target.value)}
        />
        <Button
          type="submit"
          variant="secondary"
          loading={create.isPending}
          disabled={!from || !to}
        >
          {t("history.export.create")}
        </Button>
      </form>
      <div className="mt-3 text-sm" aria-live="polite">
        <FormError error={create.error} />
        {job.data?.status === "PENDING" && (
          <p className="text-fg-muted">{t("history.export.pending")}</p>
        )}
        {job.data?.status === "READY" && job.data.download_url && (
          <p>
            <a href={job.data.download_url} download className="font-medium text-primary underline">
              {t("history.export.download", { count: job.data.row_count ?? 0 })}
            </a>
            {job.data.truncated && (
              <span className="ml-2 text-fg-muted">{t("history.export.truncated")}</span>
            )}
          </p>
        )}
        {(job.data?.status === "FAILED" || job.data?.status === "EXPIRED") && (
          <p className="text-fg-muted">{t("history.export.gone")}</p>
        )}
      </div>
    </Card>
  );
}

const DIRECTIONS: ("IN" | "OUT")[] = ["IN", "OUT"];

function todayInVietnam(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

function firstOfMonth(day: string): string {
  return `${day.slice(0, 8)}01`;
}

export function HistoryPage() {
  const { t, i18n } = useTranslation();
  const [filters, setFilters] = useState<TransactionFilters>({});
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const query = { ...filters, ...(deferredSearch.length >= 2 ? { q: deferredSearch } : {}) };
  const history = useTransactions(query);
  const items = history.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = Object.values(query).some((v) => v !== undefined && v !== "");
  const statusLabel = (s: TxnStatus) => STATUS_STYLES[s].label[uiLocale(i18n.language)];
  const amountFilter = (value: string) => (value === "" ? undefined : Number(value));

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-fg">{t("history.title")}</h1>
        <p className="text-fg-muted">{t("history.description")}</p>
      </header>
      <div role="search" className="flex flex-col gap-4">
        <TextField
          label={t("history.search")}
          type="search"
          hint={t("history.searchHint")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex flex-wrap gap-4">
          <FilterSelect
            label={t("history.filterType")}
            value={filters.type}
            options={TYPES}
            onChange={(type) => setFilters((f) => ({ ...f, type }))}
            labelFor={(type) => t(`txn.types.${type}`)}
          />
          <FilterSelect
            label={t("history.filterStatus")}
            value={filters.status}
            options={STATUSES}
            onChange={(status) => setFilters((f) => ({ ...f, status }))}
            labelFor={statusLabel}
          />
          <FilterSelect
            label={t("history.filterDirection")}
            value={filters.direction}
            options={DIRECTIONS}
            onChange={(direction) => setFilters((f) => ({ ...f, direction }))}
            labelFor={(d) => t(`history.direction.${d}`)}
          />
          <TextField
            label={t("history.from")}
            type="date"
            value={filters.from ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value || undefined }))}
          />
          <TextField
            label={t("history.to")}
            type="date"
            value={filters.to ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value || undefined }))}
          />
          <TextField
            label={t("history.minAmount")}
            inputMode="numeric"
            value={filters.minAmount?.toString() ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                minAmount: amountFilter(e.target.value.replace(/\D/g, "")),
              }))
            }
          />
          <TextField
            label={t("history.maxAmount")}
            inputMode="numeric"
            value={filters.maxAmount?.toString() ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                maxAmount: amountFilter(e.target.value.replace(/\D/g, "")),
              }))
            }
          />
        </div>
        {filtered && (
          <Button
            variant="ghost"
            size="sm"
            className="w-fit"
            onClick={() => {
              setFilters({});
              setSearch("");
            }}
          >
            {t("history.clear")}
          </Button>
        )}
      </div>
      <Card>
        {history.isPending ? (
          <ListSkeleton />
        ) : history.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("history.error")}
            onRetry={() => void history.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            headingLevel={2}
            title={filtered ? t("history.emptyFiltered") : t("history.empty")}
            description={filtered ? undefined : t("history.emptyBody")}
          />
        ) : (
          <>
            <TransactionList items={items} />
            {history.hasNextPage && (
              <div className="mt-4 flex justify-center">
                <Button
                  variant="secondary"
                  loading={history.isFetchingNextPage}
                  onClick={() => void history.fetchNextPage()}
                >
                  {t("history.loadMore")}
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
      <ExportPanel />
    </div>
  );
}

function CategoryEditor({ reference, code }: { reference: string; code: string }) {
  const { t } = useTranslation();
  const api = useAccountApi();
  const categories = useCategories();
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (next: string) => api.setCategory(reference, next),
    onSuccess: (updated) => {
      queryClient.setQueryData(financeKeys.transaction(reference), updated);
      void queryClient.invalidateQueries({ queryKey: financeKeys.allTransactions });
    },
  });
  return (
    <label className="flex items-center justify-end gap-2">
      <span className="sr-only">{t("txn.category")}</span>
      <select
        value={code}
        disabled={change.isPending || !categories.data}
        onChange={(e) => change.mutate(e.target.value)}
        className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
      >
        {(categories.data ?? [{ code, name: code }]).map((c) => (
          <option key={c.code} value={c.code}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="text-sm text-fg-muted">{label}</dt>
      <dd className="text-right text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

export function TransactionDetailPage() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const { reference = "" } = useParams();
  const query = useTransaction(reference);
  const locale = uiLocale(i18n.language);
  const back = (
    <Link
      to="/history"
      className="inline-flex w-fit items-center gap-1 text-sm font-medium text-primary"
    >
      <ArrowLeft aria-hidden className="size-4" />
      {t("txn.back")}
    </Link>
  );
  if (query.isPending) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="flex flex-col gap-4">
        {back}
        <EmptyState title={t("txn.notFound")} description={t("txn.notFoundBody")} />
      </div>
    );
  }
  const txn = query.data;
  const out = txn.direction === "OUT";
  const copy = async () => {
    await navigator.clipboard.writeText(txn.reference);
    toast.show({ title: t("txn.copied"), tone: "success" });
  };
  return (
    <div className="flex flex-col gap-6">
      {back}
      <Card>
        <div className="flex flex-col items-center gap-2 pb-4 text-center">
          <h1 className="text-base font-medium text-fg-muted">
            {txn.description || t(`txn.types.${txn.type}`)}
          </h1>
          <AmountText
            money={txn.total}
            direction={out ? "OUT" : "IN"}
            size="display"
            locale={amountLocale(i18n.language)}
          />
          <StatusBadge status={txn.status} locale={locale} />
        </div>
        <dl className="divide-y divide-border border-t border-border">
          <Row label={t("txn.reference")}>
            <span className="inline-flex items-center gap-2">
              <code className="font-mono">{txn.reference}</code>
              <button
                type="button"
                onClick={() => void copy()}
                aria-label={t("txn.copy")}
                className="grid size-7 place-items-center rounded-full text-fg-muted hover:bg-bg-subtle"
              >
                <Copy aria-hidden className="size-4" />
              </button>
            </span>
          </Row>
          <Row label={t("history.filterType")}>{t(`txn.types.${txn.type}`)}</Row>
          {txn.counterparty?.name && (
            <Row label={t("txn.counterparty")}>{txn.counterparty.name}</Row>
          )}
          <Row label={t("txn.amount")}>
            <AmountText money={txn.amount} locale={amountLocale(i18n.language)} />
          </Row>
          {out && (
            <Row label={t("txn.fee")}>
              <AmountText money={txn.fee} locale={amountLocale(i18n.language)} />
            </Row>
          )}
          <Row label={t("txn.created")}>{formatDateTime(txn.created_at, i18n.language)}</Row>
          {txn.completed_at && (
            <Row label={t("txn.completed")}>{formatDateTime(txn.completed_at, i18n.language)}</Row>
          )}
          <Row label={t("txn.category")}>
            <CategoryEditor reference={txn.reference} code={txn.category.code} />
          </Row>
          {txn.failure_code && <Row label={t("txn.failure")}>{txn.failure_code}</Row>}
        </dl>
        <div className="mt-4 border-t border-border pt-4">
          <Link
            to={`/support/tickets?txn=${txn.reference}`}
            className="text-sm font-medium text-primary underline"
          >
            {t("txn.report")}
          </Link>
        </div>
      </Card>
      <Card title={t("txn.timeline")}>
        <StatusTimeline
          locale={locale}
          formatTime={(iso) => formatDateTime(iso, i18n.language)}
          steps={txn.events.map((e) => ({ status: e.to_status, at: e.at }))}
        />
      </Card>
    </div>
  );
}
