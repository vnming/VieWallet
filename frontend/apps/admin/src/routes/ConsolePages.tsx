import {
  adminKeys,
  type Dashboard,
  type SimulatorAction,
  type SimulatorMode,
  useAdminApi,
  useDashboard,
  useStaffMe,
  useSystemHealth,
} from "@viewallet/api-client";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { FormError } from "../features/auth/FormError";
import { DataTable, Money, PageHeader, SelectField } from "../features/common";
import { HEALTH_TONE, useDateFormat } from "../features/format";
import { ServiceStatusCard } from "./ServiceStatusCard";

// ------------------------------------------------------------------ dashboard

const QUEUE_LINKS: Record<keyof Dashboard["queues"], string> = {
  approvals: "/approvals",
  risk_holds: "/risk",
  risk_alerts: "/risk",
  kyc_reviews: "/kyc",
  tickets: "/tickets",
  reconciliation_items: "/reconciliation",
  recovery_cases: "/operations",
};

function Kpi({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-4">
      <span className="text-xs text-fg-muted">{label}</span>
      <span className="tabular text-2xl font-semibold text-fg">{value}</span>
    </div>
  );
}

export function DashboardPage() {
  const { t } = useTranslation();
  const dashboard = useDashboard();
  const health = useSystemHealth();
  const date = useDateFormat();
  const queues = dashboard.data
    ? (
        Object.entries(dashboard.data.queues) as [keyof Dashboard["queues"], number | null][]
      ).filter(([, count]) => count !== null)
    : [];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("overview.title")} description={t("dashboard.description")} />
      {dashboard.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : dashboard.isError ? (
        <ErrorState
          headingLevel={2}
          title={t("dashboard.error")}
          onRetry={() => void dashboard.refetch()}
        />
      ) : (
        <>
          <Card title={t("dashboard.queues")}>
            {queues.length === 0 ? (
              <EmptyState headingLevel={2} title={t("dashboard.noQueues")} />
            ) : (
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {queues.map(([name, count]) => (
                  <li key={name}>
                    <Link
                      to={QUEUE_LINKS[name]}
                      className="flex items-center justify-between rounded-lg border border-border p-4 hover:bg-bg-subtle"
                    >
                      <span className="text-sm text-fg">{t(`dashboard.queue.${name}`)}</span>
                      <Badge tone={count ? "warning" : "success"}>{count}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {dashboard.data.transactions && (
            <Card title={t("dashboard.today", { date: dashboard.data.business_date })}>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Kpi
                  label={t("dashboard.inProgress")}
                  value={dashboard.data.transactions.in_progress}
                />
                <Kpi
                  label={t("dashboard.failedToday")}
                  value={dashboard.data.transactions.failed_today}
                />
                {dashboard.data.users && (
                  <Kpi
                    label={t("dashboard.customers")}
                    value={`${dashboard.data.users.customers} (+${dashboard.data.users.new_today})`}
                  />
                )}
              </div>
              {dashboard.data.transactions.completed_today.length === 0 ? (
                <p className="text-sm text-fg-muted">{t("dashboard.noTransactions")}</p>
              ) : (
                <DataTable
                  caption={t("dashboard.byType")}
                  headers={[t("dashboard.type"), t("dashboard.count"), t("dashboard.volume")]}
                >
                  {dashboard.data.transactions.completed_today.map((row) => (
                    <tr key={row.type}>
                      <td className="py-2 pr-3">
                        {t(`txnType.${row.type}`, { defaultValue: row.type })}
                      </td>
                      <td className="tabular py-2 pr-3">{row.count}</td>
                      <td className="py-2 pr-3">
                        <Money amount={row.amount} />
                      </td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </Card>
          )}
          <p className="text-xs text-fg-muted">
            {t("dashboard.generated", { time: date(dashboard.data.generated_at) })}
          </p>
        </>
      )}
      <ServiceStatusCard />
      {health.data && (
        <Card
          title={t("health.title")}
          actions={
            <Link
              to="/system"
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              {t("common.details")}
            </Link>
          }
        >
          <ul className="flex flex-wrap gap-2">
            {Object.entries(health.data.components).map(([name, value]) => (
              <li key={name}>
                <Badge tone={HEALTH_TONE[value] ?? "neutral"}>
                  {t(`health.component.${name}`)}: {t(`health.status.${value}`)}
                </Badge>
              </li>
            ))}
            <li>
              <Badge tone={HEALTH_TONE[health.data.provider.status] ?? "neutral"}>
                {t("health.provider")}: {t(`health.status.${health.data.provider.status}`)}
              </Badge>
            </li>
          </ul>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ operations queue

export function OperationsQueuePage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const date = useDateFormat();
  const queue = useQuery({
    queryKey: adminKeys.queue,
    queryFn: api.operationsQueue,
    refetchInterval: 30_000,
  });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("queue.title")} description={t("queue.description")} />
      {queue.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : queue.isError ? (
        <ErrorState
          headingLevel={2}
          title={t("queue.error")}
          onRetry={() => void queue.refetch()}
        />
      ) : (
        <>
          <Card title={t("queue.stuck")}>
            {queue.data.stuck_transactions.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("queue.none")}</p>
            ) : (
              <DataTable
                caption={t("queue.stuck")}
                headers={[
                  t("queue.reference"),
                  t("queue.type"),
                  t("queue.status"),
                  t("queue.amount"),
                  t("queue.age"),
                ]}
              >
                {queue.data.stuck_transactions.map((x) => (
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
                      {t(`txnType.${x.type}`, { defaultValue: x.type })}
                    </td>
                    <td className="py-2 pr-3">{x.status}</td>
                    <td className="py-2 pr-3">
                      <Money amount={x.amount} currency={x.currency} />
                    </td>
                    <td className="tabular py-2 pr-3">
                      {t("queue.minutes", { count: x.age_minutes })}
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
          <Card title={t("queue.unknown")}>
            {queue.data.unknown_provider_operations.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("queue.none")}</p>
            ) : (
              <DataTable
                caption={t("queue.unknown")}
                headers={[
                  t("queue.operation"),
                  t("queue.status"),
                  t("queue.amount"),
                  t("queue.inquiries"),
                  t("queue.nextInquiry"),
                ]}
              >
                {queue.data.unknown_provider_operations.map((o) => (
                  <tr key={o.id}>
                    <td className="py-2 pr-3 font-mono text-xs">
                      {o.operation} · {o.id.slice(0, 8)}…
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone="warning">{o.status}</Badge>
                    </td>
                    <td className="py-2 pr-3">
                      <Money amount={o.amount} />
                    </td>
                    <td className="tabular py-2 pr-3">{o.inquiry_count}</td>
                    <td className="py-2 pr-3">{date(o.next_inquiry_at)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
          <Card title={t("queue.recovery")}>
            {queue.data.recovery_cases.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("queue.none")}</p>
            ) : (
              <ul className="divide-y divide-border">
                {queue.data.recovery_cases.map((c) => (
                  <li key={c.id} className="flex flex-col gap-1 py-3">
                    <span className="text-sm font-medium text-fg">{c.reason}</span>
                    <span className="text-sm text-fg-muted">{c.detail}</span>
                    <span className="text-xs text-fg-muted">{date(c.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={t("queue.approvals")}>
            {queue.data.pending_money_approvals.length === 0 ? (
              <p className="text-sm text-fg-muted">{t("queue.none")}</p>
            ) : (
              <ul className="divide-y divide-border">
                {queue.data.pending_money_approvals.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                    <span className="text-sm text-fg">
                      {t(`finance.actions.${a.action_type}`)} · {a.maker_reason}
                    </span>
                    <span className="text-xs text-fg-muted">
                      {t("queue.expires", { time: date(a.expires_at) })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ system health

export function SystemHealthPage() {
  const { t } = useTranslation();
  const health = useSystemHealth();
  const date = useDateFormat();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("health.title")} description={t("health.description")} />
      {health.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : health.isError ? (
        <ErrorState
          headingLevel={2}
          title={t("health.error")}
          onRetry={() => void health.refetch()}
        />
      ) : (
        <>
          <Card title={t("health.components")}>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(health.data.components).map(([name, value]) => (
                <div
                  key={name}
                  className="flex items-center justify-between rounded-lg border border-border p-3"
                >
                  <dt className="text-sm text-fg">{t(`health.component.${name}`)}</dt>
                  <dd>
                    <Badge tone={HEALTH_TONE[value] ?? "neutral"}>
                      {t(`health.status.${value}`)}
                    </Badge>
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
          <Card title={t("health.provider")}>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-xs text-fg-muted">{t("health.providerStatus")}</dt>
                <dd>
                  <Badge tone={HEALTH_TONE[health.data.provider.status] ?? "neutral"}>
                    {t(`health.status.${health.data.provider.status}`)}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">{t("health.mode")}</dt>
                <dd className="text-sm font-medium">{health.data.provider.mode ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">{t("health.unknownOps")}</dt>
                <dd className="tabular text-sm font-medium">
                  {health.data.provider.unknown_operations}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">{t("health.unavailable")}</dt>
                <dd className="tabular text-sm font-medium">
                  {health.data.provider.recent_unavailable_failures}
                </dd>
              </div>
            </dl>
          </Card>
          <Card title={t("health.outbox")}>
            <dl className="grid gap-3 sm:grid-cols-3">
              <div>
                <dt className="text-xs text-fg-muted">{t("health.pending")}</dt>
                <dd className="tabular text-sm font-medium">{health.data.outbox.pending}</dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">{t("health.failed")}</dt>
                <dd className="tabular text-sm font-medium">{health.data.outbox.failed}</dd>
              </div>
              <div>
                <dt className="text-xs text-fg-muted">{t("health.lag")}</dt>
                <dd className="tabular text-sm font-medium">
                  {health.data.outbox.oldest_pending_seconds === null
                    ? "—"
                    : t("health.seconds", { count: health.data.outbox.oldest_pending_seconds })}
                </dd>
              </div>
            </dl>
          </Card>
          <Card title={t("health.jobs")}>
            <DataTable
              caption={t("health.jobs")}
              headers={[
                t("health.job"),
                t("health.lastStatus"),
                t("health.lastFinished"),
                t("health.lastError"),
              ]}
            >
              {health.data.jobs.map((j) => (
                <tr key={j.name}>
                  <td className="py-2 pr-3 font-mono text-xs">{j.name}</td>
                  <td className="py-2 pr-3">
                    <Badge
                      tone={
                        j.last_status === "FAILED"
                          ? "danger"
                          : j.last_status
                            ? "success"
                            : "neutral"
                      }
                    >
                      {j.last_status ?? "—"}
                    </Badge>
                  </td>
                  <td className="py-2 pr-3">{date(j.last_finished_at)}</td>
                  <td className="max-w-xs truncate py-2 pr-3 text-xs text-fg-muted">
                    {j.last_error ?? ""}
                  </td>
                </tr>
              ))}
            </DataTable>
          </Card>
          <p className="text-xs text-fg-muted">
            {t("health.checked", {
              time: date(health.data.checked_at),
              version: health.data.version,
            })}
          </p>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ simulator panel

const MODES: SimulatorMode[] = ["NORMAL", "DEGRADED", "OUTAGE"];
const ACTIONS: SimulatorAction[] = ["SUCCEED", "FAIL", "EXPIRE", "RETURN", "RESEND_CALLBACK"];

interface SimOperation {
  operation_key: string;
  provider_reference: string | null;
  kind: string;
  status: string;
  amount: string;
  reference: string;
}

export function SimulatorPage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const me = useStaffMe();
  const [mode, setMode] = useState<SimulatorMode>("NORMAL");
  const [pending, setPending] = useState<
    { kind: "mode" } | { kind: "action"; key: string; action: SimulatorAction } | null
  >(null);
  const [reason, setReason] = useState("");
  const panel = useQuery({ queryKey: adminKeys.simulator, queryFn: api.simulator });
  const close = () => {
    setPending(null);
    setReason("");
  };
  const run = useMutation({
    mutationFn: async () => {
      if (pending?.kind === "mode") await api.setSimulatorMode(mode, reason);
      else if (pending?.kind === "action")
        await api.simulatorAction(pending.key, pending.action, reason);
    },
    onSuccess: async () => {
      toast.show({ title: t("simulator.done"), tone: "success" });
      close();
      await queryClient.invalidateQueries({ queryKey: adminKeys.simulator });
      await queryClient.invalidateQueries({ queryKey: adminKeys.health });
    },
  });
  const operations = (panel.data?.operations ?? []) as unknown as SimOperation[];
  if (!me.data?.permissions.includes("simulator:manage:any")) return null;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("simulator.title")} description={t("simulator.description")} />
      {panel.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : panel.isError ? (
        <ErrorState
          headingLevel={2}
          title={t("simulator.error")}
          onRetry={() => void panel.refetch()}
        />
      ) : (
        <>
          <Card title={t("simulator.mode")}>
            <div className="flex flex-wrap items-end gap-3">
              <p className="text-sm text-fg">
                {t("simulator.current")}{" "}
                <Badge
                  tone={
                    panel.data.mode === "NORMAL"
                      ? "success"
                      : panel.data.mode === "OUTAGE"
                        ? "danger"
                        : "warning"
                  }
                >
                  {panel.data.mode}
                </Badge>
              </p>
              <SelectField
                label={t("simulator.newMode")}
                value={mode}
                options={MODES}
                onChange={setMode}
                labelFor={(m) => t(`simulator.modes.${m}`)}
                className="w-56"
              />
              <Button
                disabled={mode === panel.data.mode}
                onClick={() => setPending({ kind: "mode" })}
              >
                {t("simulator.apply")}
              </Button>
            </div>
          </Card>
          <Card title={t("simulator.operations")}>
            {operations.length === 0 ? (
              <EmptyState headingLevel={2} title={t("simulator.noOperations")} />
            ) : (
              <DataTable
                caption={t("simulator.operations")}
                headers={[
                  t("simulator.reference"),
                  t("simulator.kind"),
                  t("simulator.status"),
                  t("queue.amount"),
                  t("simulator.actions"),
                ]}
              >
                {operations.map((o) => (
                  <tr key={o.operation_key}>
                    <td className="py-2 pr-3 font-mono text-xs">{o.reference}</td>
                    <td className="py-2 pr-3">{o.kind}</td>
                    <td className="py-2 pr-3">{o.status}</td>
                    <td className="py-2 pr-3">
                      <Money amount={o.amount} />
                    </td>
                    <td className="py-2 pr-3">
                      <label className="sr-only" htmlFor={`act-${o.operation_key}`}>
                        {t("simulator.actionFor", { reference: o.reference })}
                      </label>
                      <select
                        id={`act-${o.operation_key}`}
                        defaultValue=""
                        onChange={(e) => {
                          const action = e.target.value as SimulatorAction | "";
                          if (action !== "")
                            setPending({ kind: "action", key: o.operation_key, action });
                          e.target.value = "";
                        }}
                        className="h-9 rounded-md border border-border-strong bg-surface px-2 text-sm"
                      >
                        <option value="">{t("simulator.choose")}</option>
                        {ACTIONS.map((a) => (
                          <option key={a} value={a}>
                            {t(`simulator.action.${a}`)}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
        </>
      )}
      <Dialog
        open={pending !== null}
        onOpenChange={(open) => !open && close()}
        title={t("simulator.confirmTitle")}
        description={
          pending?.kind === "mode"
            ? t("simulator.confirmMode", { mode })
            : pending
              ? t("simulator.confirmAction", { action: t(`simulator.action.${pending.action}`) })
              : ""
        }
        closeLabel={t("common.close")}
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              {t("common.cancel")}
            </Button>
            <Button
              loading={run.isPending}
              disabled={reason.trim().length < 5}
              onClick={() => run.mutate()}
            >
              {t("common.confirm")}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={t("simulator.reason")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={300}
          />
          <FormError error={run.error} />
        </div>
      </Dialog>
    </div>
  );
}
