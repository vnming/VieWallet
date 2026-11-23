import {
  type KycRejectReason,
  type KycSubmissionDetail,
  kycKeys,
  type RiskHold,
  useKycApi,
  useKycQueue,
  useKycSubmission,
  useRiskAlerts,
  useRiskHolds,
} from "@viewallet/api-client";
import { intlLocale } from "@viewallet/shared";
import {
  AmountText,
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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { FormError } from "../features/auth/FormError";
import { needsStepUp, useStepUp } from "../features/auth/StepUp";

const REASONS: KycRejectReason[] = [
  "BLURRY_IMAGE",
  "MISMATCH",
  "EXPIRED_DOCUMENT",
  "SUSPECTED_FRAUD",
  "OTHER",
];

function useDateFormat() {
  const { i18n } = useTranslation();
  const format = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  return (iso: string | null | undefined) => (iso ? format.format(new Date(iso)) : "—");
}

function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="text-[1.75rem] font-bold text-fg">{title}</h1>
      <p className="max-w-3xl text-fg-muted">{description}</p>
    </header>
  );
}

// ------------------------------------------------------------------ KYC review

function SubmissionPanel({ detail }: { detail: KycSubmissionDetail }) {
  const { t } = useTranslation();
  const api = useKycApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const date = useDateFormat();
  const [reason, setReason] = useState<KycRejectReason>("BLURRY_IMAGE");
  const [note, setNote] = useState("");
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: kycKeys.queue });
    await queryClient.invalidateQueries({ queryKey: kycKeys.submission(detail.id) });
  };
  const claim = useMutation({ mutationFn: () => api.claim(detail.id), onSuccess: refresh });
  const decide = useMutation({
    mutationFn: (decision: "APPROVE" | "REJECT") =>
      api.decide(detail.id, decision, decision === "REJECT" ? reason : null, note),
    onSuccess: async (result) => {
      toast.show({
        title: result.status === "APPROVED" ? t("kyc.approvedDone") : t("kyc.rejectedDone"),
        tone: "success",
      });
      setNote("");
      await refresh();
    },
  });
  const open = detail.status === "PENDING" || detail.status === "IN_REVIEW";
  return (
    <Card title={detail.full_name}>
      <div className="flex flex-col gap-4">
        <dl className="grid gap-2 text-sm md:grid-cols-2">
          <div>
            <dt className="text-fg-muted">{t("kyc.dob")}</dt>
            <dd>{detail.date_of_birth}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">{t("kyc.document")}</dt>
            <dd>
              {t(`kyc.type.${detail.document_type}`)} · {detail.document_number_masked}
            </dd>
          </div>
          <div className="md:col-span-2">
            <dt className="text-fg-muted">{t("kyc.address")}</dt>
            <dd>{detail.address}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">{t("kyc.submittedAt")}</dt>
            <dd>{date(detail.submitted_at)}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">{t("kyc.statusLabel")}</dt>
            <dd>
              <Badge tone={open ? "warning" : detail.status === "APPROVED" ? "success" : "neutral"}>
                {t(`kyc.status.${detail.status}`)}
              </Badge>
            </dd>
          </div>
        </dl>
        {detail.duplicate_document_accounts > 0 && (
          <InlineAlert tone="danger" title={t("kyc.duplicate")}>
            {t("kyc.duplicateBody", { count: detail.duplicate_document_accounts })}
          </InlineAlert>
        )}
        <InlineAlert tone="info" title={t("kyc.simulated")} />
        <div className="grid gap-3 md:grid-cols-3">
          {detail.documents.map((d) =>
            d.content_type === "application/pdf" ? (
              <a key={d.id} href={d.url} className="text-sm font-medium text-primary underline">
                {t(`kyc.kind.${d.kind}`)} (PDF)
              </a>
            ) : (
              <figure key={d.id} className="flex flex-col gap-1">
                <img
                  src={d.url}
                  alt={t(`kyc.kind.${d.kind}`)}
                  className="max-h-48 rounded-md border border-border object-contain"
                />
                <figcaption className="text-xs text-fg-muted">{t(`kyc.kind.${d.kind}`)}</figcaption>
              </figure>
            ),
          )}
        </div>
        {detail.status === "PENDING" && (
          <Button loading={claim.isPending} onClick={() => claim.mutate()} className="w-fit">
            {t("kyc.claim")}
          </Button>
        )}
        {detail.can_decide && (
          <div className="flex flex-col gap-3 border-t border-border pt-4">
            <TextField
              label={t("kyc.note")}
              value={note}
              maxLength={500}
              onChange={(e) => setNote(e.target.value)}
            />
            <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
              {t("kyc.rejectReason")}
              <select
                value={reason}
                onChange={(e) => setReason(e.target.value as KycRejectReason)}
                className="h-11 max-w-sm rounded-md border border-border-strong bg-surface px-3 text-sm"
              >
                {REASONS.map((r) => (
                  <option key={r} value={r}>
                    {t(`kyc.reason.${r}`)}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex flex-wrap gap-3">
              <Button loading={decide.isPending} onClick={() => decide.mutate("APPROVE")}>
                {t("kyc.approve")}
              </Button>
              <Button
                variant="danger"
                loading={decide.isPending}
                onClick={() => decide.mutate("REJECT")}
              >
                {t("kyc.reject")}
              </Button>
            </div>
          </div>
        )}
        <FormError error={claim.error ?? decide.error} />
        {detail.reviews.length > 0 && (
          <ul className="flex flex-col gap-1 text-xs text-fg-muted">
            {detail.reviews.map((r) => (
              <li key={r.at}>
                {date(r.at)} · {r.decision}
                {r.reason_code ? ` · ${t(`kyc.reason.${r.reason_code}`)}` : ""}
                {r.note ? ` · ${r.note}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

export function KycReviewPage() {
  const { t } = useTranslation();
  const queue = useKycQueue();
  const date = useDateFormat();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useKycSubmission(selected);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("kyc.title")} description={t("kyc.description")} />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Card title={t("kyc.queue")}>
          {queue.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : queue.isError ? (
            <ErrorState title={t("errors.generic")} onRetry={() => void queue.refetch()} />
          ) : queue.data.items.length === 0 ? (
            <EmptyState title={t("kyc.empty")} />
          ) : (
            <ul className="divide-y divide-border">
              {queue.data.items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(item.id)}
                    aria-pressed={selected === item.id}
                    className="flex w-full items-center justify-between gap-3 py-3 text-left hover:bg-surface-muted"
                  >
                    <span className="flex flex-col">
                      <span className="text-sm font-medium text-fg">{item.full_name}</span>
                      <span className="text-xs text-fg-muted">{date(item.submitted_at)}</span>
                    </span>
                    <Badge tone={item.status === "PENDING" ? "warning" : "info"}>
                      {t(`kyc.status.${item.status}`)}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected === null ? (
          <Card>
            <EmptyState title={t("kyc.pick")} />
          </Card>
        ) : detail.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : detail.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void detail.refetch()} />
        ) : (
          <SubmissionPanel detail={detail.data} />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ risk desk

function HoldRow({ hold }: { hold: RiskHold }) {
  const { t, i18n } = useTranslation();
  const api = useKycApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { guard, dialog } = useStepUp();
  const [mode, setMode] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const date = useDateFormat();
  const decide = useMutation({
    mutationFn: () =>
      mode === "approve"
        ? api.approveHold(hold.reference, note)
        : api.rejectHold(hold.reference, note),
    onSuccess: async () => {
      toast.show({
        title: mode === "approve" ? t("risk.approvedDone") : t("risk.rejectedDone"),
        tone: "success",
      });
      setMode(null);
      setNote("");
      await queryClient.invalidateQueries({ queryKey: kycKeys.holds });
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => decide.mutate());
    },
  });
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <span className="flex flex-col">
        <span className="text-sm font-medium text-fg">
          {t(`risk.types.${hold.type}`, { defaultValue: hold.type })} ·{" "}
          <span className="font-mono">{hold.reference}</span>
        </span>
        <span className="text-xs text-fg-muted">
          {typeof hold.counterparty?.name === "string" ? hold.counterparty.name : ""} ·{" "}
          {date(hold.confirmed_at)}
        </span>
      </span>
      <span className="flex items-center gap-2">
        <AmountText
          money={{ amount: hold.held, currency: hold.currency }}
          size="sm"
          locale={intlLocale(i18n.language) === "vi-VN" ? "vi" : "en"}
        />
        <Button size="sm" onClick={() => setMode("approve")}>
          {t("risk.approve")}
        </Button>
        <Button size="sm" variant="danger" onClick={() => setMode("reject")}>
          {t("risk.reject")}
        </Button>
      </span>
      <Dialog
        open={mode !== null}
        onOpenChange={(open) => !open && setMode(null)}
        title={mode === "approve" ? t("risk.approveTitle") : t("risk.rejectTitle")}
        description={t("risk.decisionBody", { reference: hold.reference })}
        closeLabel={t("common.close")}
        footer={
          <Button
            variant={mode === "reject" ? "danger" : "primary"}
            loading={decide.isPending}
            disabled={mode === "reject" && note.trim().length < 5}
            onClick={() => decide.mutate()}
          >
            {t("common.confirm")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={t("risk.note")}
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
          />
          {!needsStepUp(decide.error) && <FormError error={decide.error} />}
        </div>
      </Dialog>
      {dialog}
    </li>
  );
}

export function RiskPage() {
  const { t } = useTranslation();
  const holds = useRiskHolds();
  const alerts = useRiskAlerts("OPEN");
  const date = useDateFormat();
  const holdsState = holds.isPending ? (
    <Skeleton className="h-24 w-full" />
  ) : holds.isError ? (
    <ErrorState title={t("risk.holdsError")} onRetry={() => void holds.refetch()} />
  ) : holds.data.length === 0 ? (
    <EmptyState title={t("risk.noHolds")} />
  ) : (
    <ul className="divide-y divide-border">
      {holds.data.map((h) => (
        <HoldRow key={h.reference} hold={h} />
      ))}
    </ul>
  );
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("risk.title")} description={t("risk.description")} />
      <Card title={t("risk.holds")}>{holdsState}</Card>
      <Card title={t("risk.alerts")}>
        {alerts.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : alerts.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void alerts.refetch()} />
        ) : alerts.data.items.length === 0 ? (
          <EmptyState title={t("risk.noAlerts")} />
        ) : (
          <ul className="divide-y divide-border">
            {alerts.data.items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-fg">
                    {t(`risk.alertTypes.${a.type}`, { defaultValue: a.type })}
                  </span>
                  <span className="text-xs text-fg-muted">
                    {date(a.created_at)} ·{" "}
                    {Object.entries(a.detail)
                      .map(([k, v]) => `${k}=${String(v)}`)
                      .join(", ")}
                  </span>
                </span>
                <Badge tone={a.severity === "HIGH" ? "danger" : "warning"}>{a.severity}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
