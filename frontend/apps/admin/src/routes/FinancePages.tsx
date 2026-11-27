import {
  type AdjustmentInput,
  type AdminWallet,
  type Approval,
  ApiError,
  type ApprovalStatus,
  fieldErrors,
  financeKeys,
  newIdempotencyKey,
  useFinanceApi,
  useStaffMe,
} from "@viewallet/api-client";
import { intlLocale } from "@viewallet/shared";
import {
  AmountText,
  Badge,
  type BadgeTone,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";

import { ApprovalDetails } from "../features/ApprovalDetails";
import { FormError } from "../features/auth/FormError";
import { ProviderReconciliation } from "../features/ProviderReconciliation";
import { needsStepUp, useStepUp } from "../features/auth/StepUp";

const REASONS: AdjustmentInput["reason_code"][] = [
  "DEMO_FUNDING",
  "GOODWILL",
  "CORRECTION",
  "RECONCILIATION",
];
const APPROVAL_FILTERS: ApprovalStatus[] = [
  "PENDING",
  "EXECUTED",
  "FAILED",
  "REJECTED",
  "CANCELLED",
  "EXPIRED",
];
const STATUS_TONE: Record<ApprovalStatus, BadgeTone> = {
  PENDING: "warning",
  APPROVED: "info",
  EXECUTED: "success",
  FAILED: "danger",
  REJECTED: "neutral",
  EXPIRED: "neutral",
  CANCELLED: "neutral",
};

function useDateFormat() {
  const { i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  return (iso: string) => format.format(new Date(iso));
}

function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="text-[1.75rem] font-bold text-fg">{title}</h1>
      <p className="max-w-3xl text-fg-muted">{description}</p>
    </header>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  labelFor,
}: {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
  labelFor: (value: T) => string;
}) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {labelFor(o)}
          </option>
        ))}
      </select>
    </label>
  );
}

// ------------------------------------------------------------------ wallet lookup

function WalletActions({ wallet }: { wallet: AdminWallet }) {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const me = useStaffMe();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { guard, dialog } = useStepUp();
  const [mode, setMode] = useState<"freeze" | "unfreeze" | null>(null);
  const [reason, setReason] = useState("");
  const permissions = me.data?.permissions ?? [];
  const close = () => {
    setMode(null);
    setReason("");
  };
  const action = useMutation({
    mutationFn: async (): Promise<void> => {
      if (mode === "freeze") await api.adminFreeze(wallet.wallet_number, reason);
      else await api.requestUnfreeze(wallet.wallet_number, reason);
    },
    onSuccess: async () => {
      toast.show({
        title: mode === "freeze" ? t("wallets.frozenDone") : t("wallets.unfreezeRequested"),
        tone: "success",
      });
      close();
      await queryClient.invalidateQueries({
        queryKey: financeKeys.adminWallet(wallet.wallet_number),
      });
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => action.mutate());
    },
  });
  const minReason = mode === "freeze" ? 5 : 10;
  return (
    <div className="flex flex-wrap gap-3">
      {wallet.status === "ACTIVE" && permissions.includes("wallet:freeze:any") && (
        <Button variant="danger" onClick={() => setMode("freeze")}>
          {t("wallets.freeze")}
        </Button>
      )}
      {wallet.status === "FROZEN" && permissions.includes("wallet:unfreeze:any") && (
        <Button variant="secondary" onClick={() => setMode("unfreeze")}>
          {t("wallets.requestUnfreeze")}
        </Button>
      )}
      {permissions.includes("adjustment:propose:any") && (
        <Link
          to={`/adjustments?wallet=${wallet.wallet_number}`}
          className="inline-flex h-11 items-center rounded-md px-4 text-sm font-medium text-primary hover:bg-primary-soft"
        >
          {t("wallets.proposeAdjustment")}
        </Link>
      )}
      <Dialog
        open={mode !== null}
        onOpenChange={(open) => !open && close()}
        title={mode === "freeze" ? t("wallets.freezeTitle") : t("wallets.unfreezeTitle")}
        description={mode === "freeze" ? t("wallets.freezeBody") : t("wallets.unfreezeBody")}
        closeLabel={t("common.close")}
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button
              variant={mode === "freeze" ? "danger" : "primary"}
              loading={action.isPending}
              disabled={reason.trim().length < minReason}
              onClick={() => action.mutate()}
            >
              {t("common.confirm")}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={t("wallets.reason")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={200}
          />
          {!needsStepUp(action.error) && <FormError error={action.error} />}
        </div>
      </Dialog>
      {dialog}
    </div>
  );
}

export function WalletsPage() {
  const { t, i18n } = useTranslation();
  const api = useFinanceApi();
  const [input, setInput] = useState("");
  const [number, setNumber] = useState("");
  const date = useDateFormat();
  const wallet = useQuery({
    queryKey: financeKeys.adminWallet(number),
    queryFn: () => api.adminWallet(number),
    enabled: number.length === 10,
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setNumber(input.replace(/\D/g, ""));
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("wallets.title")} description={t("wallets.description")} />
      <Card>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <TextField
            label={t("finance.walletNumber")}
            inputMode="numeric"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="min-w-64"
          />
          <Button type="submit" variant="secondary">
            {t("common.search")}
          </Button>
        </form>
      </Card>
      {wallet.isFetching && !wallet.data && <Skeleton className="h-40 w-full" />}
      {wallet.isError &&
        (wallet.error instanceof ApiError && wallet.error.status === 404 ? (
          <EmptyState title={t("wallets.notFound")} />
        ) : (
          <FormError error={wallet.error} />
        ))}
      {wallet.data && (
        <Card title={`${t("finance.walletNumber")} ${wallet.data.wallet_number}`}>
          <dl className="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t("wallets.balance")}>
              <AmountText
                money={wallet.data.balance}
                size="lg"
                locale={intlLocale(i18n.language)}
              />
            </Field>
            <Field label={t("wallets.status")}>
              <Badge tone={wallet.data.status === "ACTIVE" ? "success" : "warning"}>
                {wallet.data.status}
              </Badge>
            </Field>
            <Field label={t("wallets.owner")}>
              <code className="font-mono text-xs">{wallet.data.owner_user_id}</code>
            </Field>
            {wallet.data.frozen_at && (
              <Field label={t("wallets.freezeSource")}>
                {wallet.data.freeze_source} · {date(wallet.data.frozen_at)}
              </Field>
            )}
          </dl>
          <WalletActions wallet={wallet.data} />
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ adjustments

export function AdjustmentsPage() {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const { guard, dialog } = useStepUp();
  const empty: AdjustmentInput = {
    wallet_number: params.get("wallet") ?? "",
    direction: "CREDIT",
    amount: "",
    reason_code: "DEMO_FUNDING",
    note: "",
    description: "",
    ticket_ref: null,
  };
  const [form, setForm] = useState<AdjustmentInput>(empty);
  // One key per intent: a retry after a network error reuses it, so no duplicate proposal.
  const [key, setKey] = useState(newIdempotencyKey);
  const [created, setCreated] = useState<string | null>(null);
  const set = <K extends keyof AdjustmentInput>(field: K, value: AdjustmentInput[K]) =>
    setForm((f) => ({ ...f, [field]: value }));

  const propose = useMutation({
    mutationFn: () => api.proposeAdjustment({ ...form, ticket_ref: form.ticket_ref || null }, key),
    onSuccess: async (result) => {
      setCreated(result.transaction_reference);
      toast.show({ title: t("finance.actions.ADJUSTMENT"), tone: "success" });
      setForm({ ...empty, wallet_number: "" });
      setKey(newIdempotencyKey());
      await queryClient.invalidateQueries({ queryKey: financeKeys.allApprovals });
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => propose.mutate());
    },
  });
  const valid =
    /^\d{10}$/.test(form.wallet_number) &&
    /^[1-9]\d{0,12}$/.test(form.amount) &&
    form.note.trim().length >= 10;
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setCreated(null);
    propose.mutate();
  };
  const fields = fieldErrors(propose.error);
  const fieldError = (name: string): string | undefined => fields[name];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("adjustments.title")} description={t("adjustments.description")} />
      <Card>
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <TextField
            label={t("finance.walletNumber")}
            inputMode="numeric"
            value={form.wallet_number}
            onChange={(e) => set("wallet_number", e.target.value.replace(/\D/g, "").slice(0, 10))}
            error={fieldError("wallet_number")}
          />
          <TextField
            label={t("finance.amount")}
            inputMode="numeric"
            hint={t("finance.amountHint")}
            value={form.amount}
            onChange={(e) => set("amount", e.target.value.replace(/\D/g, "").slice(0, 13))}
            error={fieldError("amount")}
          />
          <SelectField
            label={t("finance.direction")}
            value={form.direction}
            options={["CREDIT", "DEBIT"] as const}
            onChange={(v) => set("direction", v)}
            labelFor={(v) => t(`finance.directions.${v}`)}
          />
          <SelectField
            label={t("adjustments.reason")}
            value={form.reason_code}
            options={REASONS}
            onChange={(v) => set("reason_code", v)}
            labelFor={(v) => t(`finance.reasons.${v}`)}
          />
          <TextField
            label={t("adjustments.note")}
            hint={t("adjustments.noteHint")}
            value={form.note}
            onChange={(e) => set("note", e.target.value)}
            maxLength={500}
            className="md:col-span-2"
          />
          <TextField
            label={t("adjustments.customerText")}
            value={form.description}
            onChange={(e) => set("description", e.target.value)}
            maxLength={140}
          />
          <TextField
            label={t("adjustments.ticket")}
            value={form.ticket_ref ?? ""}
            onChange={(e) => set("ticket_ref", e.target.value)}
            maxLength={64}
          />
          <div className="flex flex-col gap-3 md:col-span-2">
            {!needsStepUp(propose.error) && <FormError error={propose.error} />}
            {created && (
              <InlineAlert tone="success" title={t("adjustments.created", { reference: created })}>
                <Link to="/approvals" className="font-medium underline underline-offset-4">
                  {t("nav.approvals")}
                </Link>
              </InlineAlert>
            )}
            <Button type="submit" loading={propose.isPending} disabled={!valid} className="w-fit">
              {t("adjustments.submit")}
            </Button>
          </div>
        </form>
      </Card>
      {dialog}
    </div>
  );
}

// ------------------------------------------------------------------ approvals

function ApprovalSummary({ approval }: { approval: Approval }) {
  const { t, i18n } = useTranslation();
  const p = approval.payload as Record<string, string | null | undefined>;
  if (approval.action_type === "ADJUSTMENT" && p.amount && p.currency) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <AmountText
          money={{ amount: p.amount, currency: p.currency }}
          direction={p.direction === "DEBIT" ? "OUT" : "IN"}
          size="lg"
          locale={intlLocale(i18n.language)}
        />
        <span className="text-sm text-fg-muted">
          {p.wallet_number_masked} · {p.reason_code ? t(`finance.reasons.${p.reason_code}`) : ""}
          {p.transaction_reference ? ` · ${p.transaction_reference}` : ""}
        </span>
      </span>
    );
  }
  return <ApprovalDetails approval={approval} />;
}

function ApprovalCard({ approval }: { approval: Approval }) {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const me = useStaffMe();
  const toast = useToast();
  const queryClient = useQueryClient();
  const date = useDateFormat();
  const { guard, dialog } = useStepUp();
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: financeKeys.allApprovals });

  const approve = useMutation({
    mutationFn: () => api.approve(approval.id, ""),
    onSuccess: async (result) => {
      toast.show(
        result.status === "EXECUTED"
          ? { title: t("approvals.approved"), tone: "success" }
          : { title: t("approvals.failed", { code: result.failure_code }), tone: "danger" },
      );
      await refresh();
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => approve.mutate());
    },
  });
  const reject = useMutation({
    mutationFn: () => api.reject(approval.id, note),
    onSuccess: async () => {
      setRejecting(false);
      toast.show({ title: t("approvals.rejected"), tone: "success" });
      await refresh();
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => reject.mutate());
    },
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelApproval(approval.id),
    onSuccess: async () => {
      toast.show({ title: t("approvals.cancelled"), tone: "success" });
      await refresh();
    },
  });
  const mine = me.data?.id === approval.maker_id;
  const pending = approval.status === "PENDING";
  const error = [approve.error, cancel.error].find((e) => e && !needsStepUp(e));

  return (
    <Card as="article">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <span className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold text-fg">
              {t(`finance.actions.${approval.action_type}`)}
            </h2>
            <Badge tone={STATUS_TONE[approval.status]}>
              {t(`finance.approvalStatus.${approval.status}`)}
            </Badge>
          </span>
          <ApprovalSummary approval={approval} />
        </div>
        {pending && (
          <div className="flex flex-wrap gap-2">
            {approval.can_decide && (
              <>
                <Button size="sm" loading={approve.isPending} onClick={() => approve.mutate()}>
                  {t("approvals.approve")}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setRejecting(true)}>
                  {t("approvals.reject")}
                </Button>
              </>
            )}
            {mine && (
              <Button
                size="sm"
                variant="ghost"
                loading={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                {t("approvals.cancel")}
              </Button>
            )}
          </div>
        )}
      </div>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Field label={t("approvals.reasonLabel")}>{approval.maker_reason}</Field>
        <Field label={t("approvals.maker")}>
          {mine ? (
            t("approvals.mine")
          ) : (
            <code className="font-mono text-xs">{approval.maker_id.slice(0, 8)}…</code>
          )}
        </Field>
        <Field label={t("approvals.created")}>{date(approval.created_at)}</Field>
        <Field label={t("approvals.expires")}>{date(approval.expires_at)}</Field>
        {approval.failure_code && (
          <Field label={t("approvals.failure")}>{approval.failure_code}</Field>
        )}
        {approval.checker_note && (
          <Field label={t("approvals.note")}>{approval.checker_note}</Field>
        )}
      </dl>
      {error && (
        <div className="mt-3">
          <FormError error={error} />
        </div>
      )}
      <Dialog
        open={rejecting}
        onOpenChange={setRejecting}
        title={t("approvals.rejectTitle")}
        closeLabel={t("common.close")}
        footer={
          <Button
            variant="danger"
            loading={reject.isPending}
            disabled={note.trim().length < 5}
            onClick={() => reject.mutate()}
          >
            {t("approvals.reject")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={t("approvals.rejectNote")}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={500}
          />
          {!needsStepUp(reject.error) && <FormError error={reject.error} />}
        </div>
      </Dialog>
      {dialog}
    </Card>
  );
}

export function ApprovalsPage() {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const [status, setStatus] = useState<ApprovalStatus>("PENDING");
  const list = useQuery({
    queryKey: financeKeys.approvals(status, 1),
    queryFn: () => api.approvals(status, 1),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("approvals.title")} description={t("approvals.description")} />
      <div className="w-56">
        <SelectField
          label={t("approvals.filter")}
          value={status}
          options={APPROVAL_FILTERS}
          onChange={setStatus}
          labelFor={(s) => t(`finance.approvalStatus.${s}`)}
        />
      </div>
      {list.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : list.isError ? (
        <ErrorState title={t("approvals.error")} onRetry={() => void list.refetch()} />
      ) : list.data.items.length === 0 ? (
        <Card>
          <EmptyState title={t("approvals.empty")} />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {list.data.items.map((a) => (
            <ApprovalCard key={a.id} approval={a} />
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ reconciliation

export function ReconciliationPage() {
  const { t } = useTranslation();
  const api = useFinanceApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const date = useDateFormat();
  const runs = useQuery({
    queryKey: financeKeys.reconciliation(1),
    queryFn: () => api.reconciliationRuns(1),
  });
  const run = useMutation({
    mutationFn: api.runReconciliation,
    onSuccess: async (result) => {
      toast.show({
        title: t("reconciliation.ran", { count: result.violations }),
        tone: result.violations === 0 ? "success" : "danger",
      });
      await queryClient.invalidateQueries({ queryKey: financeKeys.allReconciliation });
    },
  });
  const latest = run.data;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PageHeader
          title={t("reconciliation.title")}
          description={t("reconciliation.description")}
        />
        <Button loading={run.isPending} onClick={() => run.mutate()}>
          {t("reconciliation.run")}
        </Button>
      </div>
      <FormError error={run.error} />
      <ProviderReconciliation />
      {latest && latest.items.length > 0 && (
        <Card title={t("reconciliation.items")}>
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-fg-muted">
              <tr>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.invariant")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.subject")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.expected")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.actual")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {latest.items.map((item) => (
                <tr key={item.id}>
                  <td className="py-2 pr-3 font-semibold">{item.invariant}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{item.subject}</td>
                  <td className="tabular py-2 pr-3">{item.expected}</td>
                  <td className="tabular py-2 pr-3">{item.actual}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <Card>
        {runs.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : runs.isError ? (
          <ErrorState title={t("status.errorTitle")} onRetry={() => void runs.refetch()} />
        ) : runs.data.items.length === 0 ? (
          <EmptyState title={t("reconciliation.empty")} />
        ) : (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">{t("reconciliation.title")}</caption>
            <thead className="border-b border-border text-xs uppercase tracking-wide text-fg-muted">
              <tr>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.started")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.status")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.violations")}
                </th>
                <th scope="col" className="py-2 pr-3">
                  {t("reconciliation.trigger")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.data.items.map((r) => (
                <tr key={r.id}>
                  <td className="tabular py-2 pr-3">{date(r.started_at)}</td>
                  <td className="py-2 pr-3">
                    <Badge tone={r.violations === 0 ? "success" : "danger"}>
                      {r.violations === 0 ? t("reconciliation.clean") : r.status}
                    </Badge>
                  </td>
                  <td className="tabular py-2 pr-3">{r.violations}</td>
                  <td className="py-2 pr-3">
                    {r.triggered_by ? (
                      <code className="font-mono text-xs">{r.triggered_by.slice(0, 8)}…</code>
                    ) : (
                      t("reconciliation.system")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
