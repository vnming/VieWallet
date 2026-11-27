/** Provider statement reconciliation (FT-28) and the open items Finance works through. */
import {
  adminKeys,
  financeKeys,
  type ReconItem,
  type ReconItemStatus,
  useAdminApi,
} from "@viewallet/api-client";
import {
  Badge,
  Button,
  Card,
  Dialog,
  ErrorState,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { FormError } from "./auth/FormError";
import { DataTable, SelectField } from "./common";
import { useDateFormat } from "./format";

const NEXT: ReconItemStatus[] = ["INVESTIGATING", "RESOLVED", "WRITTEN_OFF"];

function todayInVietnam(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" }).format(new Date());
}

function ItemDialog({ item, onClose }: { item: ReconItem; onClose: () => void }) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ReconItemStatus>("RESOLVED");
  const [note, setNote] = useState("");
  const update = useMutation({
    mutationFn: () => api.updateReconItem(item.id, status, note),
    onSuccess: async () => {
      toast.show({ title: t("reconciliation.itemUpdated"), tone: "success" });
      onClose();
      await queryClient.invalidateQueries({ queryKey: adminKeys.reconItems });
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("reconciliation.itemTitle", { subject: item.subject })}
      description={t("reconciliation.itemBody")}
      closeLabel={t("common.close")}
      footer={
        <Button
          loading={update.isPending}
          disabled={note.trim().length < 5}
          onClick={() => update.mutate()}
        >
          {t("common.confirm")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <SelectField
          label={t("reconciliation.newStatus")}
          value={status}
          options={NEXT}
          onChange={setStatus}
          labelFor={(s) => t(`reconciliation.itemStatus.${s}`)}
        />
        <TextField
          label={t("reconciliation.note")}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={500}
        />
        <FormError error={update.error} />
      </div>
    </Dialog>
  );
}

export function ProviderReconciliation() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const date = useDateFormat();
  const [day, setDay] = useState(todayInVietnam);
  const [item, setItem] = useState<ReconItem | null>(null);
  const items = useQuery({ queryKey: adminKeys.reconItems, queryFn: api.reconItems });
  const run = useMutation({
    mutationFn: () => api.runProviderReconciliation(day),
    onSuccess: async (result) => {
      toast.show({
        title: t("reconciliation.providerRan", { count: result.violations }),
        tone: result.violations === 0 ? "success" : "danger",
      });
      await queryClient.invalidateQueries({ queryKey: adminKeys.reconItems });
      await queryClient.invalidateQueries({ queryKey: financeKeys.allReconciliation });
    },
  });
  return (
    <>
      <Card title={t("reconciliation.provider")}>
        <p className="mb-3 text-sm text-fg-muted">{t("reconciliation.providerHint")}</p>
        <div className="flex flex-wrap items-end gap-3">
          <TextField
            type="date"
            label={t("reconciliation.businessDate")}
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
          <Button loading={run.isPending} disabled={!day} onClick={() => run.mutate()}>
            {t("reconciliation.runProvider")}
          </Button>
        </div>
        <div className="mt-3">
          <FormError error={run.error} />
        </div>
      </Card>
      <Card title={t("reconciliation.openItems")}>
        {items.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : items.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("status.errorTitle")}
            onRetry={() => void items.refetch()}
          />
        ) : items.data.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("reconciliation.noOpenItems")}</p>
        ) : (
          <DataTable
            caption={t("reconciliation.openItems")}
            headers={[
              t("reconciliation.type"),
              t("reconciliation.subject"),
              t("reconciliation.expected"),
              t("reconciliation.actual"),
              t("reconciliation.status"),
              "",
            ]}
          >
            {items.data.map((i) => (
              <tr key={i.id}>
                <td className="py-2 pr-3">
                  {t(`reconciliation.itemType.${i.type}`, { defaultValue: i.type })}
                  {i.invariant ? ` (${i.invariant})` : ""}
                  <span className="block text-xs text-fg-muted">{date(i.created_at)}</span>
                </td>
                <td className="py-2 pr-3 font-mono text-xs">{i.subject}</td>
                <td className="tabular py-2 pr-3">{i.expected}</td>
                <td className="tabular py-2 pr-3">{i.actual}</td>
                <td className="py-2 pr-3">
                  <Badge tone={i.status === "OPEN" ? "danger" : "warning"}>
                    {t(`reconciliation.itemStatus.${i.status}`)}
                  </Badge>
                </td>
                <td className="py-2 pr-3">
                  <Button size="sm" variant="secondary" onClick={() => setItem(i)}>
                    {t("reconciliation.work")}
                  </Button>
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Card>
      {item && <ItemDialog item={item} onClose={() => setItem(null)} />}
    </>
  );
}
