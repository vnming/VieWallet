import {
  financeKeys,
  isApiError,
  type MerchantPayment,
  newIdempotencyKey,
  type PaymentRequest,
  type RecipientLookup,
  type Spend,
  type SpendKind,
  spendKeys,
  spendKindOf,
  useMerchant,
  useMerchantPayments,
  usePaymentRequest,
  usePaymentRequests,
  useReceiveQr,
  useRecipients,
  useSpend,
  useSpendApi,
} from "@viewallet/api-client";
import {
  AmountText,
  Badge,
  Button,
  Card,
  Dialog,
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
import type { TFunction } from "i18next";
import { BadgeCheck, CheckCircle2, Clock, QrCode as QrIcon, XCircle } from "lucide-react";
import { type ReactNode, type SyntheticEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { QrCode } from "../../features/spend/QrCode";
import { barcodeDetector, parseScanned, pathFor } from "../../features/spend/scan";
import { amountLocale, formatDateTime, uiLocale } from "../../features/wallet/format";
import { onlyDigits } from "../../features/money/amount";
import { AmountField, PageHeader } from "../../features/money/fields";

const WALLET_NUMBER = /^[1-9]\d{9}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-2">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

function failureText(code: string | null | undefined, t: TFunction): string | null {
  return code ? t(`money.failure.${code}`, { defaultValue: code }) : null;
}

// ------------------------------------------------------------------ review + PIN (shared)

/** Review of a quote, then the PIN. One confirm key per quote: a retry reuses it. */
function ConfirmSpend({ quote, onBack }: { quote: Spend; onBack: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useSpendApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const kind = spendKindOf(quote);
  const [pin, setPin] = useState("");
  const [confirmKey] = useState(newIdempotencyKey);
  const confirm = useMutation({
    mutationFn: () => api.confirm(kind, quote.reference, pin, confirmKey),
    onSuccess: async (result) => {
      queryClient.setQueryData(spendKeys.spend(result.reference), result);
      await queryClient.invalidateQueries({ queryKey: financeKeys.wallet });
      void navigate(`/${kind}/${result.reference}`);
    },
  });
  const money = amountLocale(i18n.language);
  return (
    <Card title={t("spend.review")}>
      <dl className="mb-5 flex flex-col divide-y divide-border text-sm">
        <Row label={t("spend.to")}>
          <span className="inline-flex items-center gap-1.5">
            {quote.counterparty?.kind === "MERCHANT" && (
              <BadgeCheck aria-label={t("pay.verified")} className="size-4 text-primary" />
            )}
            {quote.counterparty?.name}
          </span>
          <span className="block text-xs font-normal text-fg-muted">
            {quote.counterparty?.wallet_number_masked}
          </span>
        </Row>
        {quote.description && <Row label={t("txn.description")}>{quote.description}</Row>}
        <Row label={t("txn.amount")}>
          <AmountText money={quote.amount} locale={money} />
        </Row>
        <Row label={t("txn.fee")}>
          <AmountText money={quote.fee} locale={money} />
        </Row>
        <Row label={t("withdraw.total")}>
          <AmountText money={quote.total} locale={money} size="lg" />
        </Row>
        {quote.quote_expires_at && (
          <Row label={t("withdraw.expiresAt")}>
            {formatDateTime(quote.quote_expires_at, i18n.language)}
          </Row>
        )}
      </dl>
      {quote.warnings.map((w) => (
        <InlineAlert key={w} tone="warning" title={t(`spend.warning.${w}`, { defaultValue: w })} />
      ))}
      <form
        className="mt-4 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          confirm.mutate();
        }}
      >
        <PinField label={t("pin.current")} value={pin} onValueChange={setPin} />
        <FormError error={confirm.error} />
        {isApiError(confirm.error, "TXN_INVALID_STATE") && (
          <Link to={`/${kind}/${quote.reference}`} className="text-sm text-primary underline">
            {t("money.viewDetail")}
          </Link>
        )}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" loading={confirm.isPending} disabled={pin.length !== 6}>
            {quote.type === "PAYMENT" ? t("pay.confirm") : t("transfer.confirm")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              void api.cancel(kind, quote.reference);
              onBack();
            }}
          >
            {t("withdraw.cancel")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

// ------------------------------------------------------------------ transfer

type Mode = "wallet" | "email" | "saved";

export function TransferPage() {
  const { t } = useTranslation();
  const api = useSpendApi();
  const [params] = useSearchParams();
  const recipients = useRecipients();
  const [mode, setMode] = useState<Mode>("wallet");
  const [walletNumber, setWalletNumber] = useState(params.get("to") ?? "");
  const [email, setEmail] = useState("");
  const [savedId, setSavedId] = useState("");
  const [found, setFound] = useState<RecipientLookup | null>(null);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [quoteKey, setQuoteKey] = useState(newIdempotencyKey);
  const changed = () => setQuoteKey(newIdempotencyKey());

  const lookup = useMutation({
    mutationFn: () => api.lookup(mode === "email" ? { email } : { wallet_number: walletNumber }),
    onSuccess: setFound,
  });
  const quote = useMutation({
    mutationFn: () =>
      api.quoteTransfer(
        {
          ...(mode === "saved"
            ? { recipient_id: savedId }
            : mode === "email"
              ? { email }
              : { wallet_number: walletNumber }),
          amount,
          note,
        },
        quoteKey,
      ),
  });
  const save = useMutation({ mutationFn: () => api.saveRecipient(walletNumber) });

  if (quote.data) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("transfer.title")} description={t("transfer.description")} />
        <ConfirmSpend
          quote={quote.data}
          onBack={() => {
            quote.reset();
            changed();
          }}
        />
      </div>
    );
  }

  const recipientReady = mode === "saved" ? savedId !== "" : found !== null && found.active;
  const canLookup = mode === "wallet" ? WALLET_NUMBER.test(walletNumber) : EMAIL.test(email);
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    quote.mutate();
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("transfer.title")} description={t("transfer.description")} />
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-5">
          <div role="tablist" aria-label={t("transfer.recipientBy")} className="flex gap-2">
            {(["wallet", "email", "saved"] as const).map((m) => (
              <Button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                size="sm"
                variant={mode === m ? "primary" : "secondary"}
                onClick={() => {
                  setMode(m);
                  setFound(null);
                  changed();
                }}
              >
                {t(`transfer.by.${m}`)}
              </Button>
            ))}
          </div>
          {mode === "saved" ? (
            recipients.isPending ? (
              <Skeleton className="h-11 w-full" />
            ) : (recipients.data ?? []).length === 0 ? (
              <EmptyState title={t("transfer.noSaved")} />
            ) : (
              <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
                {t("transfer.savedLabel")}
                <select
                  value={savedId}
                  onChange={(e) => {
                    setSavedId(e.target.value);
                    changed();
                  }}
                  className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
                >
                  <option value="">{t("transfer.choose")}</option>
                  {(recipients.data ?? []).map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.nickname ? `${r.nickname} · ` : ""}
                      {r.recipient.display_name} · {r.recipient.wallet_number_masked}
                    </option>
                  ))}
                </select>
              </label>
            )
          ) : (
            <div className="flex flex-col gap-2">
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  {mode === "wallet" ? (
                    <TextField
                      label={t("transfer.walletNumber")}
                      inputMode="numeric"
                      autoComplete="off"
                      value={walletNumber}
                      onChange={(e) => {
                        setWalletNumber(e.target.value.replace(/\D/g, "").slice(0, 10));
                        setFound(null);
                        changed();
                      }}
                    />
                  ) : (
                    <TextField
                      label={t("transfer.email")}
                      type="email"
                      autoComplete="off"
                      value={email}
                      onChange={(e) => {
                        setEmail(e.target.value.trim());
                        setFound(null);
                        changed();
                      }}
                    />
                  )}
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  loading={lookup.isPending}
                  disabled={!canLookup}
                  onClick={() => lookup.mutate()}
                >
                  {t("transfer.check")}
                </Button>
              </div>
              <FormError error={lookup.error} />
              {found && (
                <div
                  className="flex items-center justify-between gap-3 rounded-md bg-surface-muted px-3 py-2"
                  aria-live="polite"
                >
                  <span className="flex flex-col">
                    <span className="text-sm font-semibold text-fg">{found.display_name}</span>
                    <span className="text-xs text-fg-muted">{found.wallet_number_masked}</span>
                  </span>
                  {found.active ? (
                    mode === "wallet" && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        loading={save.isPending}
                        disabled={save.isSuccess}
                        onClick={() => save.mutate()}
                      >
                        {save.isSuccess ? t("transfer.saved") : t("transfer.save")}
                      </Button>
                    )
                  ) : (
                    <Badge tone="warning">{t("money.failure.RECIPIENT_UNAVAILABLE")}</Badge>
                  )}
                </div>
              )}
            </div>
          )}
          <AmountField
            value={amount}
            hint={t("transfer.amountHint")}
            onChange={(v) => {
              setAmount(v);
              changed();
            }}
          />
          <TextField
            label={t("transfer.note")}
            maxLength={140}
            value={note}
            onChange={(e) => {
              setNote(e.target.value);
              changed();
            }}
          />
          <FormError error={quote.error} />
          <Button
            type="submit"
            loading={quote.isPending}
            disabled={!recipientReady || amount === ""}
            className="w-fit"
          >
            {t("transfer.quote")}
          </Button>
        </form>
      </Card>
      <div className="flex flex-wrap gap-4 text-sm">
        <Link to="/scan" className="font-medium text-primary">
          {t("scan.title")}
        </Link>
        <Link to="/receive" className="font-medium text-primary">
          {t("receive.title")}
        </Link>
        <Link to="/request" className="font-medium text-primary">
          {t("request.title")}
        </Link>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ result

function spendHeadline(spend: Spend, t: TFunction) {
  const payment = spend.type === "PAYMENT";
  if (spend.status === "COMPLETED")
    return { icon: CheckCircle2, text: t(payment ? "pay.completed" : "transfer.completed") };
  if (spend.next_action === "WAIT_REVIEW") return { icon: Clock, text: t("spend.review_hold") };
  if (spend.status === "EXPIRED") return { icon: XCircle, text: t("spend.expired") };
  if (spend.status === "CANCELLED") return { icon: XCircle, text: t("spend.cancelled") };
  if (spend.status === "CREATED") return { icon: Clock, text: t("spend.notConfirmed") };
  if (["REVERSED", "REFUNDED", "PARTIALLY_REFUNDED"].includes(spend.status))
    return { icon: CheckCircle2, text: t(`spend.status.${spend.status}`) };
  return { icon: XCircle, text: t(payment ? "pay.failed" : "transfer.failed") };
}

function SpendResult({ kind }: { kind: SpendKind }) {
  const { t, i18n } = useTranslation();
  const { reference = "" } = useParams();
  const queryClient = useQueryClient();
  const spend = useSpend(kind, reference);
  const status = spend.data?.status;
  useEffect(() => {
    if (status && status !== "CREATED") {
      void queryClient.invalidateQueries({ queryKey: financeKeys.wallet });
    }
  }, [status, queryClient]);
  if (spend.isPending) return <Skeleton className="h-56 w-full" />;
  if (spend.isError) {
    return <ErrorState title={t("txn.notFound")} onRetry={() => void spend.refetch()} />;
  }
  const data = spend.data;
  const { icon: Icon, text } = spendHeadline(data, t);
  const locale = uiLocale(i18n.language);
  const failure = failureText(data.failure_code, t);
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
            direction="OUT"
            locale={amountLocale(i18n.language)}
          />
          <p className="text-sm text-fg">
            {data.counterparty?.name}{" "}
            <span className="text-fg-muted">{data.counterparty?.wallet_number_masked}</span>
          </p>
          <StatusBadge status={data.status} locale={locale} />
          {failure && <p className="max-w-md text-sm text-fg-muted">{failure}</p>}
          {data.next_action === "WAIT_REVIEW" && (
            <p className="max-w-md text-sm text-fg-muted">{t("spend.reviewBody")}</p>
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
        </div>
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

export function TransferResultPage() {
  return <SpendResult kind="transfers" />;
}

export function PaymentResultPage() {
  return <SpendResult kind="payments" />;
}

// ------------------------------------------------------------------ pay a QR

function Issuer({ request }: { request: PaymentRequest }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3">
      <span className="flex size-11 items-center justify-center rounded-full bg-primary-soft text-primary">
        <QrIcon aria-hidden className="size-5" />
      </span>
      <span className="flex flex-col">
        <span className="inline-flex items-center gap-1.5 font-semibold text-fg">
          {request.issuer.name}
          {request.issuer.kind === "MERCHANT" && (
            <Badge tone="success" icon={<BadgeCheck aria-hidden className="size-3.5" />}>
              {t("pay.verified")}
            </Badge>
          )}
        </span>
        <span className="text-xs text-fg-muted">
          {request.issuer.kind === "MERCHANT" ? t("pay.merchant") : t("pay.person")} ·{" "}
          {request.issuer.wallet_number_masked}
        </span>
      </span>
    </div>
  );
}

export function PayPage() {
  const { t, i18n } = useTranslation();
  const { code = "" } = useParams();
  const api = useSpendApi();
  const request = usePaymentRequest(code);
  const [amount, setAmount] = useState("");
  const [payKey, setPayKey] = useState(newIdempotencyKey);
  const pay = useMutation({
    mutationFn: () => api.pay(code, request.data?.amount ? null : amount, payKey),
  });
  if (request.isPending) return <Skeleton className="h-56 w-full" />;
  if (request.isError) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("pay.title")} description={t("pay.description")} />
        <ErrorState title={t("pay.invalid")} onRetry={() => void request.refetch()} />
      </div>
    );
  }
  if (pay.data) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("pay.title")} description={t("pay.description")} />
        <ConfirmSpend
          quote={pay.data}
          onBack={() => {
            pay.reset();
            setPayKey(newIdempotencyKey());
          }}
        />
      </div>
    );
  }
  const data = request.data;
  const open = data.status === "OPEN";
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("pay.title")} description={t("pay.description")} />
      <Card>
        <div className="flex flex-col gap-5">
          <Issuer request={data} />
          {data.description && <p className="text-sm text-fg">{data.description}</p>}
          {data.amount ? (
            <AmountText money={data.amount} size="display" locale={amountLocale(i18n.language)} />
          ) : (
            open && (
              <AmountField
                value={amount}
                hint={t("pay.openAmount")}
                onChange={(v) => {
                  setAmount(v);
                  setPayKey(newIdempotencyKey());
                }}
              />
            )
          )}
          {!open && <InlineAlert tone="warning" title={t(`pay.closed.${data.status}`)} />}
          {data.is_mine && <InlineAlert tone="info" title={t("pay.yours")} />}
          <FormError error={pay.error} />
          {open && !data.is_mine && (
            <Button
              loading={pay.isPending}
              disabled={!data.amount && amount === ""}
              onClick={() => pay.mutate()}
              className="w-fit"
            >
              {t("pay.continue")}
            </Button>
          )}
          {open && data.addressed_to_me && <DeclineButton code={data.code} />}
        </div>
      </Card>
    </div>
  );
}

function DeclineButton({ code }: { code: string }) {
  const { t } = useTranslation();
  const api = useSpendApi();
  const queryClient = useQueryClient();
  const decline = useMutation({
    mutationFn: () => api.declineRequest(code),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["payment-request"] });
      await queryClient.invalidateQueries({ queryKey: ["payment-requests"] });
    },
  });
  return (
    <Button
      variant="ghost"
      size="sm"
      loading={decline.isPending}
      onClick={() => decline.mutate()}
      className="w-fit"
    >
      {t("request.decline")}
    </Button>
  );
}

// ------------------------------------------------------------------ scan

export function ScanPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const video = useRef<HTMLVideoElement | null>(null);
  const [manual, setManual] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [camera, setCamera] = useState<"idle" | "on" | "unsupported" | "denied">("idle");

  const go = (text: string) => {
    const target = parseScanned(text, window.location.origin);
    if (target) void navigate(pathFor(target));
    else setInvalid(true);
  };

  useEffect(() => {
    if (camera !== "on") return;
    const detector = barcodeDetector();
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    let stopped = false;
    const start = async () => {
      if (!detector || !("mediaDevices" in navigator)) {
        setCamera("unsupported");
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
      } catch {
        setCamera("denied");
        return;
      }
      const element = video.current;
      if (!element || stopped) return;
      element.srcObject = stream;
      await element.play();
      timer = window.setInterval(() => {
        void detector.detect(element).then((codes) => {
          const hit = codes
            .map((c) => parseScanned(c.rawValue, window.location.origin))
            .find((x) => x !== null);
          if (hit) void navigate(pathFor(hit));
        });
      }, 400);
    };
    void start();
    return () => {
      stopped = true;
      if (timer) window.clearInterval(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [camera, navigate]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("scan.title")} description={t("scan.description")} />
      <Card>
        <div className="flex flex-col gap-4">
          {camera === "on" ? (
            <video
              ref={video}
              muted
              playsInline
              aria-label={t("scan.camera")}
              className="aspect-square w-full max-w-sm rounded-md bg-black object-cover"
            />
          ) : (
            <Button variant="secondary" className="w-fit" onClick={() => setCamera("on")}>
              {t("scan.start")}
            </Button>
          )}
          {camera === "unsupported" && <InlineAlert tone="info" title={t("scan.unsupported")} />}
          {camera === "denied" && <InlineAlert tone="warning" title={t("scan.denied")} />}
        </div>
      </Card>
      <Card title={t("scan.manual")}>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            go(manual);
          }}
        >
          <TextField
            label={t("scan.code")}
            hint={t("scan.codeHint")}
            autoComplete="off"
            value={manual}
            onChange={(e) => {
              setManual(e.target.value);
              setInvalid(false);
            }}
          />
          {invalid && <InlineAlert tone="danger" title={t("scan.invalid")} />}
          <Button type="submit" disabled={manual.trim() === ""} className="w-fit">
            {t("scan.open")}
          </Button>
        </form>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ receive (personal QR)

export function ReceivePage() {
  const { t } = useTranslation();
  const qr = useReceiveQr();
  const toast = useToast();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("receive.title")} description={t("receive.description")} />
      <Card>
        {qr.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : qr.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void qr.refetch()} />
        ) : (
          <div className="flex flex-col items-center gap-3 text-center">
            <QrCode value={qr.data.payload} label={t("receive.qrLabel")} />
            <p className="font-semibold text-fg">{qr.data.display_name}</p>
            <p className="font-mono text-lg tracking-wider">{qr.data.wallet_number}</p>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                void navigator.clipboard.writeText(qr.data.wallet_number).catch(() => undefined);
                toast.show({ title: t("wallet.copied"), tone: "success" });
              }}
            >
              {t("wallet.copy")}
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ request money

const REQUEST_TONES = {
  OPEN: "info",
  PAID: "success",
  EXPIRED: "neutral",
  CANCELLED: "neutral",
  DECLINED: "warning",
} as const;

function RequestList({ role, items }: { role: "issued" | "incoming"; items: PaymentRequest[] }) {
  const { t, i18n } = useTranslation();
  const api = useSpendApi();
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: api.cancelRequest,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: spendKeys.requests("issued") }),
  });
  if (items.length === 0) return <EmptyState title={t(`request.empty.${role}`)} />;
  return (
    <ul className="divide-y divide-border">
      {items.map((r) => (
        <li key={r.code} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <span className="flex flex-col">
            <span className="text-sm font-medium text-fg">
              {role === "incoming" ? r.issuer.name : (r.payer_name ?? t("request.anyone"))}
              {r.description ? ` · ${r.description}` : ""}
            </span>
            <span className="text-xs text-fg-muted">
              {t("request.until", { time: formatDateTime(r.expires_at, i18n.language) })}
            </span>
          </span>
          <span className="flex items-center gap-2">
            {r.amount && (
              <AmountText money={r.amount} size="sm" locale={amountLocale(i18n.language)} />
            )}
            <Badge tone={REQUEST_TONES[r.status]}>{t(`request.status.${r.status}`)}</Badge>
            {role === "incoming" && r.status === "OPEN" && (
              <Link to={`/pay/${r.code}`} className="text-sm font-medium text-primary">
                {t("request.pay")}
              </Link>
            )}
            {role === "issued" && r.status === "OPEN" && (
              <Button size="sm" variant="ghost" onClick={() => cancel.mutate(r.code)}>
                {t("request.cancel")}
              </Button>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function RequestMoneyPage() {
  const { t } = useTranslation();
  const api = useSpendApi();
  const queryClient = useQueryClient();
  const issued = usePaymentRequests("issued");
  const incoming = usePaymentRequests("incoming");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [payer, setPayer] = useState("");
  const [requestKey, setRequestKey] = useState(newIdempotencyKey);
  const create = useMutation({
    mutationFn: () =>
      api.createRequest(
        {
          kind: "USER",
          amount,
          description: note,
          single_use: true,
          ...(EMAIL.test(payer) ? { payer_email: payer } : {}),
          ...(WALLET_NUMBER.test(payer) ? { payer_wallet_number: payer } : {}),
        },
        requestKey,
      ),
    onSuccess: async () => {
      setRequestKey(newIdempotencyKey());
      await queryClient.invalidateQueries({ queryKey: spendKeys.requests("issued") });
    },
  });
  const payerValid = payer === "" || EMAIL.test(payer) || WALLET_NUMBER.test(payer);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("request.title")} description={t("request.description")} />
      <Card title={t("request.incoming")}>
        {incoming.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : incoming.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void incoming.refetch()} />
        ) : (
          <RequestList role="incoming" items={incoming.data} />
        )}
      </Card>
      <Card title={t("request.new")}>
        {create.data ? (
          <div className="flex flex-col items-center gap-3 text-center">
            <QrCode value={create.data.url} label={t("request.qrLabel")} />
            <p className="break-all font-mono text-xs text-fg-muted">{create.data.url}</p>
            <Button size="sm" variant="secondary" onClick={() => create.reset()}>
              {t("request.another")}
            </Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <AmountField value={amount} hint={t("transfer.amountHint")} onChange={setAmount} />
            <TextField
              label={t("transfer.note")}
              maxLength={140}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <TextField
              label={t("request.payer")}
              hint={t("request.payerHint")}
              autoComplete="off"
              value={payer}
              error={payerValid ? undefined : t("request.payerInvalid")}
              onChange={(e) => setPayer(e.target.value.trim())}
            />
            <FormError error={create.error} />
            <Button
              type="submit"
              loading={create.isPending}
              disabled={amount === "" || !payerValid}
              className="w-fit"
            >
              {t("request.create")}
            </Button>
          </form>
        )}
      </Card>
      <Card title={t("request.issued")}>
        {issued.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : issued.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void issued.refetch()} />
        ) : (
          <RequestList role="issued" items={issued.data} />
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ merchant

function RefundDialog({ payment, onClose }: { payment: MerchantPayment; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const api = useSpendApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState(payment.refundable.amount);
  const [reason, setReason] = useState("");
  const [refundKey] = useState(newIdempotencyKey);
  const refund = useMutation({
    mutationFn: () => api.refund(payment.reference, amount, reason, refundKey),
    onSuccess: async (result) => {
      toast.show({
        title: result.status === "COMPLETED" ? t("merchant.refunded") : t("merchant.refundFailed"),
        tone: result.status === "COMPLETED" ? "success" : "danger",
      });
      await queryClient.invalidateQueries({ queryKey: spendKeys.merchant });
      await queryClient.invalidateQueries({ queryKey: spendKeys.merchantPayments });
      onClose();
    },
  });
  const tooMuch = Number(amount || "0") > Number(payment.refundable.amount);
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("merchant.refundTitle")}
      description={t("merchant.refundBody", { reference: payment.reference })}
      closeLabel={t("common.close")}
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          refund.mutate();
        }}
      >
        <p className="text-sm text-fg-muted">
          {t("merchant.refundable")}{" "}
          <AmountText money={payment.refundable} size="sm" locale={amountLocale(i18n.language)} />
        </p>
        <TextField
          label={t("money.amount")}
          inputMode="numeric"
          value={amount}
          error={tooMuch ? t("merchant.tooMuch") : undefined}
          onChange={(e) => setAmount(onlyDigits(e.target.value))}
        />
        <TextField
          label={t("merchant.reason")}
          maxLength={140}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <FormError error={refund.error} />
        <Button
          type="submit"
          loading={refund.isPending}
          disabled={amount === "" || tooMuch || reason.trim().length < 3}
          className="w-fit"
        >
          {t("merchant.refund")}
        </Button>
      </form>
    </Dialog>
  );
}

export function MerchantPage() {
  const { t, i18n } = useTranslation();
  const api = useSpendApi();
  const merchant = useMerchant();
  const payments = useMerchantPayments(merchant.isSuccess);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [reusable, setReusable] = useState(false);
  const [requestKey, setRequestKey] = useState(newIdempotencyKey);
  const [refunding, setRefunding] = useState<MerchantPayment | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api.createRequest(
        {
          kind: "MERCHANT",
          amount: amount || null,
          description: note,
          single_use: !reusable,
        },
        requestKey,
      ),
    onSuccess: () => setRequestKey(newIdempotencyKey()),
  });
  if (merchant.isPending) return <Skeleton className="h-56 w-full" />;
  if (merchant.isError) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("merchant.title")} description={t("merchant.description")} />
        <EmptyState title={t("merchant.none")} description={t("merchant.noneBody")} />
      </div>
    );
  }
  const money = amountLocale(i18n.language);
  const locale = uiLocale(i18n.language);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={merchant.data.display_name} description={t("merchant.description")} />
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-fg-muted">
            {t("merchant.balance")} · {merchant.data.wallet_number}
          </span>
          <AmountText money={merchant.data.balance} size="lg" locale={money} />
        </div>
      </Card>
      <Card title={t("merchant.newQr")}>
        {create.data ? (
          <div className="flex flex-col items-center gap-3 text-center">
            <QrCode value={create.data.url} label={t("merchant.qrLabel")} />
            {create.data.amount && (
              <AmountText money={create.data.amount} size="lg" locale={money} />
            )}
            <p className="break-all font-mono text-xs text-fg-muted">{create.data.url}</p>
            <Button size="sm" variant="secondary" onClick={() => create.reset()}>
              {t("request.another")}
            </Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-5"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <AmountField value={amount} hint={t("merchant.amountHint")} onChange={setAmount} />
            <TextField
              label={t("transfer.note")}
              maxLength={140}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <label className="flex items-center gap-2 text-sm text-fg">
              <input
                type="checkbox"
                checked={reusable}
                onChange={(e) => setReusable(e.target.checked)}
              />
              {t("merchant.reusable")}
            </label>
            <FormError error={create.error} />
            <Button type="submit" loading={create.isPending} className="w-fit">
              {t("merchant.createQr")}
            </Button>
          </form>
        )}
      </Card>
      <Card title={t("merchant.payments")}>
        {payments.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : payments.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void payments.refetch()} />
        ) : payments.data.items.length === 0 ? (
          <EmptyState title={t("merchant.noPayments")} />
        ) : (
          <ul className="divide-y divide-border">
            {payments.data.items.map((p) => (
              <li
                key={p.reference}
                className="flex flex-wrap items-center justify-between gap-3 py-3"
              >
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-fg">
                    {t(`txn.types.${p.type}`)} · {p.payer?.name}
                  </span>
                  <span className="font-mono text-xs text-fg-muted">
                    {p.reference}
                    {p.related_reference ? ` ← ${p.related_reference}` : ""}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <AmountText
                    money={p.amount}
                    size="sm"
                    direction={p.type === "REFUND" ? "OUT" : "IN"}
                    locale={money}
                  />
                  <StatusBadge status={p.status} locale={locale} />
                  {p.refundable.amount !== "0" && (
                    <Button size="sm" variant="secondary" onClick={() => setRefunding(p)}>
                      {t("merchant.refund")}
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {refunding && <RefundDialog payment={refunding} onClose={() => setRefunding(null)} />}
    </div>
  );
}
