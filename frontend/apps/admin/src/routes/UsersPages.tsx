import {
  adminKeys,
  ApiError,
  financeKeys,
  newIdempotencyKey,
  type RevealedPii,
  useAdminApi,
  useFinanceApi,
  useStaffMe,
} from "@viewallet/api-client";
import {
  Badge,
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";

import { FormError } from "../features/auth/FormError";
import { needsStepUp, useStepUp } from "../features/auth/StepUp";
import { DataTable, Field, Money, PageHeader } from "../features/common";
import { useDateFormat } from "../features/format";

// ------------------------------------------------------------------ search

export function UsersPage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [input, setInput] = useState(q);
  const date = useDateFormat();
  const results = useQuery({
    queryKey: adminKeys.users(q),
    queryFn: () => api.searchUsers(q),
    enabled: q.length >= 3,
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    setParams(input.trim() ? { q: input.trim() } : {});
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("users.title")} description={t("users.description")} />
      <Card>
        <form onSubmit={submit} role="search" className="flex flex-wrap items-end gap-3">
          <TextField
            label={t("users.search")}
            hint={t("users.searchHint")}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="min-w-72"
          />
          <Button type="submit" variant="secondary" disabled={input.trim().length < 3}>
            {t("common.search")}
          </Button>
        </form>
      </Card>
      {q.length >= 3 &&
        (results.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : results.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("users.error")}
            onRetry={() => void results.refetch()}
          />
        ) : results.data.length === 0 ? (
          <Card>
            <EmptyState headingLevel={2} title={t("users.none")} />
          </Card>
        ) : (
          <Card>
            <DataTable
              caption={t("users.results")}
              headers={[
                t("users.name"),
                t("users.email"),
                t("users.wallet"),
                t("users.kyc"),
                t("users.created"),
              ]}
            >
              {results.data.map((u) => (
                <tr key={u.id}>
                  <td className="py-2 pr-3">
                    <Link
                      to={`/users/${u.id}`}
                      className="font-medium text-primary underline-offset-4 hover:underline"
                    >
                      {u.name}
                    </Link>
                  </td>
                  <td className="py-2 pr-3">{u.email}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{u.wallet_number ?? "—"}</td>
                  <td className="py-2 pr-3">{t("users.tier", { tier: u.kyc_tier })}</td>
                  <td className="py-2 pr-3">{date(u.created_at)}</td>
                </tr>
              ))}
            </DataTable>
          </Card>
        ))}
    </div>
  );
}

// ------------------------------------------------------------------ detail

function RevealPii({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const { guard, dialog } = useStepUp();
  const [revealed, setRevealed] = useState<RevealedPii | null>(null);
  const reveal = useMutation({
    mutationFn: () => api.revealPii(userId),
    onSuccess: setRevealed,
    onError: (error) => {
      if (needsStepUp(error)) guard(() => reveal.mutate());
    },
  });
  if (revealed) {
    return (
      <InlineAlert tone="warning" title={t("users.revealedTitle")}>
        <dl className="mt-2 grid gap-2 sm:grid-cols-3">
          <Field label={t("users.fullName")}>{revealed.full_name}</Field>
          <Field label={t("users.email")}>{revealed.email}</Field>
          <Field label={t("users.phone")}>{revealed.phone ?? "—"}</Field>
        </dl>
      </InlineAlert>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="secondary"
        size="sm"
        className="w-fit"
        loading={reveal.isPending}
        onClick={() => reveal.mutate()}
      >
        {t("users.reveal")}
      </Button>
      <p className="text-xs text-fg-muted">{t("users.revealHint")}</p>
      {!needsStepUp(reveal.error) && <FormError error={reveal.error} />}
      {dialog}
    </div>
  );
}

export function UserDetailPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const api = useAdminApi();
  const me = useStaffMe();
  const date = useDateFormat();
  const user = useQuery({ queryKey: adminKeys.user(id), queryFn: () => api.user(id) });
  const permissions = me.data?.permissions ?? [];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("users.detailTitle")} />
      {user.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : user.isError ? (
        user.error instanceof ApiError && user.error.status === 404 ? (
          <EmptyState headingLevel={2} title={t("users.notFound")} />
        ) : (
          <ErrorState
            headingLevel={2}
            title={t("users.error")}
            onRetry={() => void user.refetch()}
          />
        )
      ) : (
        <>
          <Card title={user.data.name}>
            <dl className="mb-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label={t("users.email")}>{user.data.email}</Field>
              <Field label={t("users.phone")}>{user.data.phone ?? "—"}</Field>
              <Field label={t("users.status")}>
                <Badge tone={user.data.status === "ACTIVE" ? "success" : "warning"}>
                  {user.data.status}
                </Badge>
              </Field>
              <Field label={t("users.kyc")}>
                {t("users.tier", { tier: user.data.kyc_tier })}
                {user.data.kyc_status ? ` · ${user.data.kyc_status}` : ""}
              </Field>
              <Field label={t("users.created")}>{date(user.data.created_at)}</Field>
              <Field label={t("users.lastLogin")}>{date(user.data.last_login_at)}</Field>
              <Field label={t("users.roles")}>{user.data.roles.join(", ")}</Field>
            </dl>
            {permissions.includes("user:pii_reveal:any") && <RevealPii userId={user.data.id} />}
          </Card>
          {user.data.wallet && (
            <Card title={t("users.wallet")}>
              <dl className="grid gap-4 sm:grid-cols-3">
                <Field label={t("users.walletNumber")}>
                  <span className="font-mono">{user.data.wallet.wallet_number}</span>
                </Field>
                <Field label={t("users.balance")}>
                  <Money amount={user.data.wallet.balance} currency={user.data.wallet.currency} />
                </Field>
                <Field label={t("users.walletStatus")}>
                  <Badge tone={user.data.wallet.status === "ACTIVE" ? "success" : "warning"}>
                    {user.data.wallet.status}
                  </Badge>
                </Field>
              </dl>
            </Card>
          )}
          <Card title={t("users.flags")}>
            {user.data.risk_flags.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("users.noFlags")}</p>
            ) : (
              <ul className="divide-y divide-border">
                {user.data.risk_flags.map((f) => (
                  <li
                    key={`${f.flag}-${f.created_at}`}
                    className="flex flex-wrap justify-between gap-2 py-2 text-sm"
                  >
                    <span>
                      <Badge tone={f.removed_at ? "neutral" : "danger"}>{f.flag}</Badge> {f.reason}
                    </span>
                    <span className="text-xs text-fg-muted">{date(f.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={t("users.recent")}>
            {user.data.recent_transactions.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("users.noTransactions")}</p>
            ) : (
              <DataTable
                caption={t("users.recent")}
                headers={[
                  t("queue.reference"),
                  t("queue.type"),
                  t("queue.status"),
                  t("queue.amount"),
                  t("users.created"),
                ]}
              >
                {user.data.recent_transactions.map((x) => (
                  <tr key={x.reference}>
                    <td className="py-2 pr-3 font-mono text-xs">
                      <Link
                        to={`/transactions/${x.reference}`}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        {x.reference}
                      </Link>
                    </td>
                    <td className="py-2 pr-3">
                      {t(`txnType.${x.type}`, { defaultValue: x.type })} ·{" "}
                      {x.direction === "IN" ? "+" : "−"}
                    </td>
                    <td className="py-2 pr-3">{x.status}</td>
                    <td className="py-2 pr-3">
                      <Money amount={x.amount} currency={x.currency} />
                    </td>
                    <td className="py-2 pr-3">{date(x.created_at)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ transaction profile

function ProposeDialog({
  open,
  kind,
  reference,
  remaining,
  onClose,
}: {
  open: boolean;
  kind: "refund" | "reversal";
  reference: string;
  remaining: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { guard, dialog } = useStepUp();
  const [amount, setAmount] = useState(remaining);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const propose = useMutation({
    mutationFn: async (): Promise<void> => {
      if (kind === "refund") await api.proposeRefund(reference, amount, reason, key);
      else await api.proposeReversal(reference, reason, key);
    },
    onSuccess: async () => {
      toast.show({ title: t(`txn.${kind}Proposed`), tone: "success" });
      setReason("");
      setKey(newIdempotencyKey());
      onClose();
      await queryClient.invalidateQueries({ queryKey: financeKeys.allApprovals });
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => propose.mutate());
    },
  });
  const valid =
    reason.trim().length >= 10 && (kind === "reversal" || /^[1-9]\d{0,12}$/.test(amount));
  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(o) => !o && onClose()}
        title={t(`txn.${kind}Title`)}
        description={t(`txn.${kind}Body`)}
        closeLabel={t("common.close")}
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button loading={propose.isPending} disabled={!valid} onClick={() => propose.mutate()}>
              {t("txn.submit")}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {kind === "refund" && (
            <TextField
              label={t("finance.amount")}
              hint={t("txn.remaining", { amount: remaining })}
              inputMode="numeric"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 13))}
            />
          )}
          <TextField
            label={t("txn.reason")}
            hint={t("adjustments.noteHint")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
          />
          {!needsStepUp(propose.error) && <FormError error={propose.error} />}
        </div>
      </Dialog>
      {dialog}
    </>
  );
}

export function TransactionPage() {
  const { t } = useTranslation();
  const { reference = "" } = useParams();
  const api = useFinanceApi();
  const me = useStaffMe();
  const date = useDateFormat();
  const [open, setOpen] = useState<"refund" | "reversal" | null>(null);
  const txn = useQuery({
    queryKey: financeKeys.adminTransaction(reference),
    queryFn: () => api.adminTransaction(reference),
  });
  const permissions = me.data?.permissions ?? [];
  const data = txn.data;
  const remaining = data ? String(Number(data.amount.amount) - Number(data.refunded.amount)) : "0";
  const refundable =
    data?.type === "PAYMENT" && ["COMPLETED", "PARTIALLY_REFUNDED"].includes(data.status);
  const reversible =
    data !== undefined &&
    ["TRANSFER", "PAYMENT", "DEPOSIT"].includes(data.type) &&
    data.status === "COMPLETED";
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("txn.title", { reference })} />
      {txn.isPending ? (
        <Skeleton className="h-48 w-full" />
      ) : txn.isError ? (
        txn.error instanceof ApiError && txn.error.status === 404 ? (
          <EmptyState headingLevel={2} title={t("txn.notFound")} />
        ) : (
          <ErrorState headingLevel={2} title={t("txn.error")} onRetry={() => void txn.refetch()} />
        )
      ) : (
        <>
          <Card
            title={t(`txnType.${txn.data.type}`, { defaultValue: txn.data.type })}
            actions={
              <div className="flex flex-wrap gap-2">
                {refundable && permissions.includes("refund:propose:any") && (
                  <Button size="sm" onClick={() => setOpen("refund")}>
                    {t("txn.proposeRefund")}
                  </Button>
                )}
                {reversible && permissions.includes("reversal:propose:any") && (
                  <Button size="sm" variant="secondary" onClick={() => setOpen("reversal")}>
                    {t("txn.proposeReversal")}
                  </Button>
                )}
              </div>
            }
          >
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label={t("queue.status")}>
                <Badge
                  tone={
                    txn.data.status === "COMPLETED"
                      ? "success"
                      : txn.data.status === "FAILED"
                        ? "danger"
                        : "warning"
                  }
                >
                  {txn.data.status}
                </Badge>
              </Field>
              <Field label={t("queue.amount")}>
                <Money amount={txn.data.amount.amount} currency={txn.data.amount.currency} />
              </Field>
              <Field label={t("txn.fee")}>
                <Money amount={txn.data.fee.amount} currency={txn.data.fee.currency} />
              </Field>
              <Field label={t("txn.refunded")}>
                <Money amount={txn.data.refunded.amount} currency={txn.data.refunded.currency} />
              </Field>
              <Field label={t("txn.created")}>{date(txn.data.created_at)}</Field>
              <Field label={t("txn.completed")}>{date(txn.data.completed_at)}</Field>
              <Field label={t("txn.risk")}>{txn.data.risk_decision ?? "—"}</Field>
              <Field label={t("txn.failure")}>{txn.data.failure_code ?? "—"}</Field>
              {txn.data.related_reference && (
                <Field label={t("txn.related")}>
                  <Link
                    to={`/transactions/${txn.data.related_reference}`}
                    className="font-mono text-primary underline-offset-4 hover:underline"
                  >
                    {txn.data.related_reference}
                  </Link>
                </Field>
              )}
              <Field label={t("txn.description")}>{txn.data.description || "—"}</Field>
            </dl>
          </Card>
          <Card title={t("txn.events")}>
            <ol className="flex flex-col divide-y divide-border">
              {txn.data.events.map((e, index) => (
                <li
                  key={`${index}-${e.to_status}`}
                  className="flex flex-wrap justify-between gap-2 py-2 text-sm"
                >
                  <span>
                    <span className="font-medium text-fg">
                      {e.from_status ?? "—"} → {e.to_status}
                    </span>{" "}
                    <span className="text-fg-muted">
                      {[e.reason_code, e.actor_type, e.message].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <span className="text-xs text-fg-muted">{date(e.at)}</span>
                </li>
              ))}
            </ol>
          </Card>
          {open && (
            <ProposeDialog
              open
              kind={open}
              reference={txn.data.reference}
              remaining={remaining}
              onClose={() => setOpen(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
