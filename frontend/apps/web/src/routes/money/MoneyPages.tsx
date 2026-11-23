import {
  financeKeys,
  isApiError,
  type MoneyFlow,
  moneyKeys,
  newIdempotencyKey,
  useBanks,
  useDestinations,
  useMe,
  useMoneyApi,
  useMoneyFlow,
} from "@viewallet/api-client";
import {
  AmountText,
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  PinField,
  Skeleton,
  StatusBadge,
  StatusTimeline,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, XCircle } from "lucide-react";
import { type SyntheticEvent, useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { validAmount } from "../../features/money/amount";
import { AmountField, PageHeader } from "../../features/money/fields";
import { goToProvider } from "../../features/money/redirect";
import { amountLocale, formatDateTime, uiLocale } from "../../features/wallet/format";

// ------------------------------------------------------------------ deposit

export function DepositPage() {
  const { t } = useTranslation();
  const api = useMoneyApi();
  const navigate = useNavigate();
  const [amount, setAmount] = useState("");
  // One key per intent: a retry after a network error reuses it (no double charge).
  const [key] = useState(newIdempotencyKey);
  const create = useMutation({
    mutationFn: () => api.createDeposit(amount, key),
    onSuccess: (flow) => {
      if (flow.next_action === "REDIRECT" && flow.checkout_url) {
        goToProvider(flow.checkout_url);
      } else {
        void navigate(`/deposits/${flow.reference}`);
      }
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    create.mutate();
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("deposit.title")} description={t("deposit.description")} />
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-5">
          <AmountField value={amount} onChange={setAmount} />
          <InlineAlert tone="info" title={t("money.simulationTitle")}>
            {t("money.scenarioHint")}
          </InlineAlert>
          <FormError error={create.error} />
          <Button
            type="submit"
            loading={create.isPending || create.isSuccess}
            disabled={!validAmount(amount)}
            className="w-fit"
          >
            {create.isSuccess ? t("deposit.redirecting") : t("deposit.submit")}
          </Button>
        </form>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ result pages

function failureText(flow: MoneyFlow, t: TFunction): string | null {
  if (!flow.failure_code) return null;
  return t(`money.failure.${flow.failure_code}`, { defaultValue: flow.failure_code });
}

function headline(flow: MoneyFlow, kind: "deposit" | "withdraw", t: TFunction) {
  if (flow.status === "COMPLETED") return { icon: CheckCircle2, text: t(`${kind}.completed`) };
  if (flow.status === "FAILED") return { icon: XCircle, text: t(`${kind}.failed`) };
  if (flow.status === "EXPIRED") return { icon: XCircle, text: t(`${kind}.expired`) };
  if (flow.status === "CANCELLED") return { icon: XCircle, text: t("withdraw.cancelled") };
  if (flow.status === "REVERSED") return { icon: XCircle, text: t("withdraw.reversed") };
  if (flow.next_action === "WAIT_REVIEW") return { icon: Clock, text: t("withdraw.review") };
  if (flow.status === "PROCESSING") return { icon: Clock, text: t("withdraw.processing") };
  return { icon: Clock, text: t(kind === "deposit" ? "deposit.pending" : "withdraw.pending") };
}

function FlowResult({ kind }: { kind: "deposit" | "withdraw" }) {
  const { t, i18n } = useTranslation();
  const { reference = "" } = useParams();
  const api = useMoneyApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const flow = useMoneyFlow(kind === "deposit" ? "deposit" : "withdrawal", reference);
  const settledStatus = flow.data?.status;
  useEffect(() => {
    // The balance changed once the flow ends: refresh the cached wallet.
    if (settledStatus && !["CREATED", "PENDING", "PROCESSING"].includes(settledStatus)) {
      void queryClient.invalidateQueries({ queryKey: financeKeys.wallet });
    }
  }, [settledStatus, queryClient]);
  const cancel = useMutation({
    mutationFn: () => api.cancelWithdrawal(reference),
    onSuccess: async () => {
      toast.show({ title: t("withdraw.cancelled"), tone: "success" });
      await queryClient.invalidateQueries({ queryKey: moneyKeys.withdrawal(reference) });
      await queryClient.invalidateQueries({ queryKey: financeKeys.wallet });
    },
  });
  if (flow.isPending) return <Skeleton className="h-56 w-full" />;
  if (flow.isError) {
    return <ErrorState title={t("txn.notFound")} onRetry={() => void flow.refetch()} />;
  }
  const data = flow.data;
  const { icon: Icon, text } = headline(data, kind, t);
  const failure = failureText(data, t);
  const final = !["CREATED", "PENDING", "PROCESSING"].includes(data.status);
  const locale = uiLocale(i18n.language);
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col items-center gap-3 text-center" aria-live="polite">
          <Icon
            aria-hidden
            className={
              data.status === "COMPLETED" ? "size-12 text-primary" : "size-12 text-fg-muted"
            }
          />
          <h1 className="text-xl font-bold text-fg">{text}</h1>
          <AmountText
            money={data.total}
            size="display"
            direction={kind === "deposit" ? "IN" : "OUT"}
            locale={amountLocale(i18n.language)}
          />
          <StatusBadge status={data.status} locale={locale} />
          {failure && <p className="text-sm text-fg-muted">{failure}</p>}
          {kind === "withdraw" && data.status === "FAILED" && (
            <p className="text-sm text-fg-muted">{t("withdraw.refunded")}</p>
          )}
          {!final && <p className="max-w-md text-sm text-fg-muted">{t("money.stillWaiting")}</p>}
          {data.checkout_url && (
            <a href={data.checkout_url} className="text-sm font-medium text-primary underline">
              {t("deposit.openCheckout")}
            </a>
          )}
        </div>
        <dl className="mt-5 flex justify-between border-t border-border pt-4 text-sm">
          <dt className="text-fg-muted">{t("money.reference")}</dt>
          <dd className="font-mono">{data.reference}</dd>
        </dl>
        <div className="mt-4 flex flex-wrap gap-3">
          <Link to={`/history/${data.reference}`} className="text-sm font-medium text-primary">
            {t("money.viewDetail")}
          </Link>
          <Link to="/" className="text-sm font-medium text-primary">
            {t("money.backHome")}
          </Link>
          {kind === "withdraw" && (data.status === "CREATED" || data.status === "PENDING") && (
            <Button
              size="sm"
              variant="secondary"
              loading={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {t("withdraw.cancel")}
            </Button>
          )}
        </div>
        <FormError error={cancel.error} />
      </Card>
      <Card title={t("money.timeline")}>
        <StatusTimeline
          locale={locale}
          formatTime={(iso) => formatDateTime(iso, i18n.language)}
          steps={data.events.map((e) => ({ status: e.to_status, at: e.at }))}
        />
      </Card>
    </div>
  );
}

export function DepositResultPage() {
  return <FlowResult kind="deposit" />;
}

export function WithdrawalResultPage() {
  return <FlowResult kind="withdraw" />;
}

// ------------------------------------------------------------------ withdraw

export function WithdrawPage() {
  const { t, i18n } = useTranslation();
  const me = useMe();
  const api = useMoneyApi();
  const navigate = useNavigate();
  const destinations = useDestinations();
  const [destination, setDestination] = useState("");
  const [amount, setAmount] = useState("");
  const [pin, setPin] = useState("");
  const [quoteKey, setQuoteKey] = useState(newIdempotencyKey);
  const [confirmKey] = useState(newIdempotencyKey);
  const quote = useMutation({
    mutationFn: () =>
      api.quoteWithdrawal(destination || destinations.data?.[0]?.id || "", amount, quoteKey),
  });
  const confirm = useMutation({
    mutationFn: (reference: string) => api.confirmWithdrawal(reference, pin, confirmKey),
    onSuccess: (flow) => void navigate(`/withdrawals/${flow.reference}`),
  });

  if (me.data && me.data.kyc_tier < 1) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("withdraw.title")} description={t("withdraw.description")} />
        <InlineAlert tone="warning" title={t("withdraw.kycRequired")}>
          {t("withdraw.kycBody")}{" "}
          <Link to="/kyc" className="font-medium underline">
            {t("withdraw.kycStart")}
          </Link>
        </InlineAlert>
      </div>
    );
  }
  if (destinations.isPending) return <Skeleton className="h-56 w-full" />;
  if (destinations.isError) {
    return <ErrorState title={t("errors.generic")} onRetry={() => void destinations.refetch()} />;
  }
  if (destinations.data.length === 0) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("withdraw.title")} description={t("withdraw.description")} />
        <Card>
          <EmptyState
            title={t("withdraw.noDestination")}
            action={
              <Link to="/wallet/destinations" className="font-medium text-primary underline">
                {t("withdraw.addDestination")}
              </Link>
            }
          />
        </Card>
      </div>
    );
  }
  const quoted = quote.data;
  const chosen = destination || destinations.data[0]?.id || "";
  const money = amountLocale(i18n.language);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("withdraw.title")} description={t("withdraw.description")} />
      {!quoted ? (
        <Card>
          <form
            className="flex flex-col gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              quote.mutate();
            }}
          >
            <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
              {t("withdraw.destination")}
              <select
                value={chosen}
                onChange={(e) => {
                  setDestination(e.target.value);
                  setQuoteKey(newIdempotencyKey());
                }}
                className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
              >
                {destinations.data.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.bank_name} · {d.account_number_masked} · {d.account_name}
                  </option>
                ))}
              </select>
            </label>
            <AmountField
              value={amount}
              onChange={(v) => {
                setAmount(v);
                setQuoteKey(newIdempotencyKey());
              }}
            />
            <FormError error={quote.error} />
            <Button
              type="submit"
              loading={quote.isPending}
              disabled={!validAmount(amount)}
              className="w-fit"
            >
              {t("withdraw.quote")}
            </Button>
          </form>
        </Card>
      ) : (
        <Card title={t("withdraw.review")}>
          <dl className="mb-5 flex flex-col divide-y divide-border text-sm">
            {[
              [
                t("withdraw.destination"),
                `${quoted.destination?.name ?? ""} · ${quoted.destination?.account_masked ?? ""}`,
              ],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4 py-2">
                <dt className="text-fg-muted">{label}</dt>
                <dd className="text-right font-medium">{value}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-4 py-2">
              <dt className="text-fg-muted">{t("txn.amount")}</dt>
              <dd>
                <AmountText money={quoted.amount} locale={money} />
              </dd>
            </div>
            <div className="flex justify-between gap-4 py-2">
              <dt className="text-fg-muted">{t("withdraw.fee")}</dt>
              <dd>
                <AmountText money={quoted.fee} locale={money} />
              </dd>
            </div>
            <div className="flex justify-between gap-4 py-2">
              <dt className="text-fg-muted">{t("withdraw.total")}</dt>
              <dd>
                <AmountText money={quoted.total} locale={money} size="lg" />
              </dd>
            </div>
            {quoted.quote_expires_at && (
              <div className="flex justify-between gap-4 py-2">
                <dt className="text-fg-muted">{t("withdraw.expiresAt")}</dt>
                <dd>{formatDateTime(quoted.quote_expires_at, i18n.language)}</dd>
              </div>
            )}
          </dl>
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              confirm.mutate(quoted.reference);
            }}
          >
            <PinField label={t("pin.current")} value={pin} onValueChange={setPin} />
            <FormError error={confirm.error} />
            {isApiError(confirm.error, "TXN_INVALID_STATE") && (
              <Link
                to={`/withdrawals/${quoted.reference}`}
                className="text-sm text-primary underline"
              >
                {t("money.viewDetail")}
              </Link>
            )}
            <div className="flex flex-wrap gap-3">
              <Button type="submit" loading={confirm.isPending} disabled={pin.length !== 6}>
                {t("withdraw.confirm")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  void api.cancelWithdrawal(quoted.reference);
                  quote.reset();
                  setQuoteKey(newIdempotencyKey());
                }}
              >
                {t("withdraw.cancel")}
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ bank accounts

export function DestinationsPage() {
  const { t, i18n } = useTranslation();
  const api = useMoneyApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const destinations = useDestinations();
  const banks = useBanks();
  const [bank, setBank] = useState("");
  const [number, setNumber] = useState("");
  const [name, setName] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: moneyKeys.destinations });
  const add = useMutation({
    mutationFn: () =>
      api.addDestination({
        bank_code: bank || banks.data?.[0]?.code || "",
        account_number: number,
        account_name: name,
      }),
    onSuccess: async () => {
      toast.show({ title: t("destinations.added"), tone: "success" });
      setNumber("");
      setName("");
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: api.removeDestination,
    onSuccess: async () => {
      toast.show({ title: t("destinations.removed"), tone: "success" });
      await refresh();
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    add.mutate();
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("destinations.title")} description={t("destinations.description")} />
      <Card>
        {destinations.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : destinations.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void destinations.refetch()} />
        ) : destinations.data.length === 0 ? (
          <EmptyState title={t("destinations.empty")} />
        ) : (
          <ul className="divide-y divide-border">
            {destinations.data.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 py-3">
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-fg">
                    {d.bank_name} · {d.account_number_masked}
                  </span>
                  <span className="text-xs text-fg-muted">
                    {d.account_name} ·{" "}
                    {t("withdraw.cooldownUntil", {
                      time: formatDateTime(d.usable_from, i18n.language),
                    })}
                  </span>
                </span>
                <Button size="sm" variant="ghost" onClick={() => remove.mutate(d.id)}>
                  {t("destinations.remove")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={t("withdraw.addDestination")}>
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-3">
          <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
            {t("destinations.bank")}
            <select
              value={bank || banks.data?.[0]?.code || ""}
              onChange={(e) => setBank(e.target.value)}
              className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
            >
              {(banks.data ?? []).map((b) => (
                <option key={b.code} value={b.code}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <TextField
            label={t("destinations.accountNumber")}
            inputMode="numeric"
            autoComplete="off"
            value={number}
            onChange={(e) => setNumber(e.target.value.replace(/\D/g, "").slice(0, 20))}
          />
          <TextField
            label={t("destinations.accountName")}
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="flex flex-col gap-3 md:col-span-3">
            <p className="text-xs text-fg-muted">{t("destinations.fictional")}</p>
            <FormError error={add.error} />
            <Button
              type="submit"
              loading={add.isPending}
              disabled={number.length < 6 || name.trim().length < 2}
              className="w-fit"
            >
              {t("destinations.add")}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
