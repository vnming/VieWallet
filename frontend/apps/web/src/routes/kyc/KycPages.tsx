import {
  identityKeys,
  type KycDocumentKind,
  type KycDraft,
  kycKeys,
  UPLOAD_TYPES,
  useKyc,
  useKycApi,
  useLimits,
} from "@viewallet/api-client";
import {
  AmountText,
  Badge,
  Button,
  Card,
  ErrorState,
  InlineAlert,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, Upload } from "lucide-react";
import { type SyntheticEvent, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { PageHeader } from "../../features/money/fields";
import { amountLocale, formatDateTime } from "../../features/wallet/format";

const DOCUMENT_TYPES: KycDraft["document_type"][] = ["NATIONAL_ID", "PASSPORT", "DRIVER_LICENSE"];
const MAX_BYTES = 5 * 1024 * 1024;

function UploadSlot({ kind, uploaded }: { kind: KycDocumentKind; uploaded: boolean }) {
  const { t } = useTranslation();
  const id = useId();
  const api = useKycApi();
  const queryClient = useQueryClient();
  const [localError, setLocalError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => api.upload(kind, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: kycKeys.mine }),
  });
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {t(`kyc.kind.${kind}`)}
        </label>
        {uploaded ? (
          <Badge tone="success" icon={<CheckCircle2 aria-hidden className="size-3.5" />}>
            {t("kyc.uploaded")}
          </Badge>
        ) : (
          <Badge tone="neutral">{t("kyc.missing")}</Badge>
        )}
      </div>
      <input
        id={id}
        type="file"
        accept={UPLOAD_TYPES.join(",")}
        className="text-sm"
        onChange={(e) => {
          const file = e.target.files?.[0];
          setLocalError(null);
          if (!file) return;
          if (file.size > MAX_BYTES) {
            setLocalError(t("kyc.tooLarge"));
            return;
          }
          upload.mutate(file);
        }}
      />
      {upload.isPending && (
        <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
          <Upload aria-hidden className="size-3.5" /> {t("kyc.uploading")}
        </span>
      )}
      {localError && <InlineAlert tone="danger" title={localError} />}
      <FormError error={upload.error} />
    </div>
  );
}

export function KycPage() {
  const { t, i18n } = useTranslation();
  const kyc = useKyc();
  const api = useKycApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const draft = kyc.data?.submission?.status === "DRAFT" ? kyc.data.submission : null;
  const [form, setForm] = useState<KycDraft | null>(null);
  const values: KycDraft = form ?? {
    full_name: draft?.full_name ?? "",
    date_of_birth: draft?.date_of_birth ?? "",
    document_type: draft?.document_type ?? "NATIONAL_ID",
    document_number: "",
    address: draft?.address ?? "",
  };
  const set = (patch: Partial<KycDraft>) => setForm({ ...values, ...patch });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: kycKeys.mine });
    await queryClient.invalidateQueries({ queryKey: kycKeys.limits });
    await queryClient.invalidateQueries({ queryKey: identityKeys.me });
  };
  const save = useMutation({
    mutationFn: () => api.saveDraft(values),
    onSuccess: async () => {
      toast.show({ title: t("kyc.saved"), tone: "success" });
      setForm(null);
      await refresh();
    },
  });
  const submit = useMutation({
    mutationFn: api.submit,
    onSuccess: async () => {
      toast.show({ title: t("kyc.submitted"), tone: "success" });
      await refresh();
    },
  });

  if (kyc.isPending || kyc.isError) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("kyc.title")} description={t("kyc.description")} />
        {kyc.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <ErrorState title={t("errors.generic")} onRetry={() => void kyc.refetch()} />
        )}
      </div>
    );
  }
  const data = kyc.data;
  const submission = data.submission;
  const header = <PageHeader title={t("kyc.title")} description={t("kyc.description")} />;
  const disclaimer = (
    <InlineAlert tone="warning" title={t("kyc.simulatedTitle")}>
      {t("kyc.simulatedBody")}
    </InlineAlert>
  );

  if (data.status === "APPROVED") {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Card>
          <div className="flex flex-col items-center gap-3 text-center">
            <CheckCircle2 aria-hidden className="size-12 text-primary" />
            <h2 className="text-xl font-bold text-fg">{t("kyc.approved")}</h2>
            <p className="text-sm text-fg-muted">
              {t("kyc.validUntil", {
                time: data.expires_at ? formatDateTime(data.expires_at, i18n.language) : "—",
              })}
            </p>
            <Link to="/wallet/limits" className="text-sm font-medium text-primary">
              {t("limits.title")}
            </Link>
          </div>
        </Card>
      </div>
    );
  }
  if (data.status === "PENDING" || data.status === "IN_REVIEW") {
    return (
      <div className="flex flex-col gap-6">
        {header}
        <Card>
          <div className="flex flex-col items-center gap-3 text-center" aria-live="polite">
            <Clock aria-hidden className="size-12 text-fg-muted" />
            <h2 className="text-xl font-bold text-fg">{t(`kyc.status.${data.status}`)}</h2>
            <p className="max-w-md text-sm text-fg-muted">{t("kyc.waitingBody")}</p>
            {submission?.submitted_at && (
              <p className="text-xs text-fg-muted">
                {t("kyc.submittedAt", {
                  time: formatDateTime(submission.submitted_at, i18n.language),
                })}
              </p>
            )}
          </div>
        </Card>
      </div>
    );
  }

  const uploaded = new Set(draft?.documents.map((d) => d.kind) ?? []);
  const ready = draft !== null && draft.required_documents.every((kind) => uploaded.has(kind));
  const onSave = (event: SyntheticEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <div className="flex flex-col gap-6">
      {header}
      {disclaimer}
      {data.status === "REJECTED" && submission?.reject_reason && (
        <InlineAlert tone="danger" title={t("kyc.rejectedTitle")}>
          {t(`kyc.reason.${submission.reject_reason}`)} {t("kyc.tryAgain")}
        </InlineAlert>
      )}
      {data.status === "EXPIRED" && <InlineAlert tone="warning" title={t("kyc.expired")} />}
      <Card title={t("kyc.step1")}>
        <form onSubmit={onSave} className="grid gap-4 md:grid-cols-2">
          <TextField
            label={t("kyc.fullName")}
            autoComplete="off"
            value={values.full_name}
            onChange={(e) => set({ full_name: e.target.value })}
          />
          <TextField
            label={t("kyc.dateOfBirth")}
            type="date"
            value={values.date_of_birth}
            onChange={(e) => set({ date_of_birth: e.target.value })}
          />
          <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
            {t("kyc.documentType")}
            <select
              value={values.document_type}
              onChange={(e) => set({ document_type: e.target.value as KycDraft["document_type"] })}
              className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
            >
              {DOCUMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`kyc.type.${type}`)}
                </option>
              ))}
            </select>
          </label>
          <TextField
            label={t("kyc.documentNumber")}
            hint={
              draft ? t("kyc.numberSaved", { masked: draft.document_number_masked }) : undefined
            }
            autoComplete="off"
            value={values.document_number}
            onChange={(e) => set({ document_number: e.target.value.toUpperCase().trim() })}
          />
          <div className="md:col-span-2">
            <TextField
              label={t("kyc.address")}
              autoComplete="off"
              value={values.address}
              onChange={(e) => set({ address: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-3 md:col-span-2">
            <FormError error={save.error} />
            <Button
              type="submit"
              loading={save.isPending}
              disabled={
                values.full_name.trim() === "" ||
                values.date_of_birth === "" ||
                values.document_number.length < 8 ||
                values.address.trim().length < 5
              }
              className="w-fit"
            >
              {t("kyc.save")}
            </Button>
          </div>
        </form>
      </Card>
      {draft && (
        <Card title={t("kyc.step2")}>
          <div className="grid gap-3 md:grid-cols-3">
            {draft.required_documents.map((kind) => (
              <UploadSlot key={kind} kind={kind} uploaded={uploaded.has(kind)} />
            ))}
          </div>
          <p className="mt-3 text-xs text-fg-muted">{t("kyc.fileHint")}</p>
        </Card>
      )}
      {draft && (
        <Card>
          <div className="flex flex-col gap-3">
            <FormError error={submit.error} />
            <Button
              loading={submit.isPending}
              disabled={!ready}
              onClick={() => submit.mutate()}
              className="w-fit"
            >
              {t("kyc.submit")}
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

export function LimitsPage() {
  const { t, i18n } = useTranslation();
  const limits = useLimits();
  if (limits.isPending || limits.isError) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title={t("limits.title")} description={t("limits.description")} />
        {limits.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <ErrorState title={t("errors.generic")} onRetry={() => void limits.refetch()} />
        )}
      </div>
    );
  }
  const data = limits.data;
  const money = (amount: string) => ({ amount, currency: data.currency });
  const locale = amountLocale(i18n.language);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("limits.title")} description={t("limits.description")} />
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-fg">
            {t("limits.tier", { tier: data.kyc_tier })}
            {data.kyc_tier === 0 && (
              <>
                {" · "}
                <Link to="/kyc" className="font-medium text-primary">
                  {t("limits.upgrade")}
                </Link>
              </>
            )}
          </span>
          {data.balance_cap && (
            <span className="text-sm text-fg-muted">
              {t("limits.balanceCap")}{" "}
              <AmountText money={money(data.balance_cap)} size="sm" locale={locale} />
            </span>
          )}
        </div>
      </Card>
      {data.types.map((type) => (
        <Card key={type.type} title={t(`txn.types.${type.type}`)}>
          {!type.allowed ? (
            <InlineAlert tone="info" title={t("limits.notAllowed")} />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-fg-muted">
                  <th className="py-1 font-medium">{t("limits.period")}</th>
                  <th className="py-1 font-medium">{t("limits.max")}</th>
                  <th className="py-1 font-medium">{t("limits.used")}</th>
                  <th className="py-1 font-medium">{t("limits.remaining")}</th>
                </tr>
              </thead>
              <tbody>
                {type.limits.map((limit) => (
                  <tr key={limit.period} className="border-t border-border">
                    <td className="py-2">{t(`limits.periods.${limit.period}`)}</td>
                    <td className="py-2">
                      {limit.max_amount ? (
                        <AmountText money={money(limit.max_amount)} size="sm" locale={locale} />
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-2">
                      {limit.period === "PER_TXN" ? (
                        "—"
                      ) : (
                        <AmountText money={money(limit.used_amount)} size="sm" locale={locale} />
                      )}
                    </td>
                    <td className="py-2">
                      {limit.remaining_amount && limit.period !== "PER_TXN" ? (
                        <AmountText
                          money={money(limit.remaining_amount)}
                          size="sm"
                          locale={locale}
                        />
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      ))}
    </div>
  );
}
