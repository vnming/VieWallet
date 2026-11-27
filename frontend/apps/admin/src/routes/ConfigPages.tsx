import {
  adminKeys,
  type FeeRule,
  financeKeys,
  type LimitRule,
  type StaffMember,
  useAdminApi,
  useStaffMe,
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
import { Link } from "react-router";

import { FormError } from "../features/auth/FormError";
import { needsStepUp, useStepUp } from "../features/auth/StepUp";
import { DataTable, Money, PageHeader, SelectField } from "../features/common";
import { useDateFormat } from "../features/format";

const DIGITS = /^\d{0,15}$/;

function useProposed() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  return async () => {
    toast.show({ title: t("config.proposed"), tone: "success" });
    await queryClient.invalidateQueries({ queryKey: ["admin", "config"] });
    await queryClient.invalidateQueries({ queryKey: financeKeys.allApprovals });
  };
}

function ReasonField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  return (
    <TextField
      label={t("config.reason")}
      hint={t("adjustments.noteHint")}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      maxLength={500}
    />
  );
}

function FeeDialog({ rule, onClose }: { rule: FeeRule; onClose: () => void }) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const done = useProposed();
  const { guard, dialog } = useStepUp();
  const [fixed, setFixed] = useState(rule.fixed);
  const [bps, setBps] = useState(String(rule.bps));
  const [minFee, setMinFee] = useState(rule.min_fee);
  const [maxFee, setMaxFee] = useState(rule.max_fee ?? "");
  const [reason, setReason] = useState("");
  const propose = useMutation({
    mutationFn: () =>
      api.proposeFee(rule.code, {
        fixed,
        bps: Number(bps),
        min_fee: minFee,
        max_fee: maxFee === "" ? null : maxFee,
        reason,
      }),
    onSuccess: async () => {
      await done();
      onClose();
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => propose.mutate());
    },
  });
  const digits = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    if (DIGITS.test(e.target.value)) set(e.target.value);
  };
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => !open && onClose()}
        title={t("config.feeTitle", { code: rule.code })}
        description={t("config.versionBody")}
        closeLabel={t("common.close")}
        footer={
          <Button
            loading={propose.isPending}
            disabled={reason.trim().length < 10 || fixed === "" || bps === "" || minFee === ""}
            onClick={() => propose.mutate()}
          >
            {t("config.propose")}
          </Button>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label={t("config.fixed")}
            inputMode="numeric"
            value={fixed}
            onChange={digits(setFixed)}
          />
          <TextField
            label={t("config.bps")}
            hint={t("config.bpsHint")}
            inputMode="numeric"
            value={bps}
            onChange={digits(setBps)}
          />
          <TextField
            label={t("config.minFee")}
            inputMode="numeric"
            value={minFee}
            onChange={digits(setMinFee)}
          />
          <TextField
            label={t("config.maxFee")}
            hint={t("config.emptyNone")}
            inputMode="numeric"
            value={maxFee}
            onChange={digits(setMaxFee)}
          />
          <div className="sm:col-span-2">
            <ReasonField value={reason} onChange={setReason} />
          </div>
          <div className="sm:col-span-2">
            {!needsStepUp(propose.error) && <FormError error={propose.error} />}
          </div>
        </div>
      </Dialog>
      {dialog}
    </>
  );
}

function LimitDialog({ rule, onClose }: { rule: LimitRule; onClose: () => void }) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const done = useProposed();
  const { guard, dialog } = useStepUp();
  const [maxAmount, setMaxAmount] = useState(rule.max_amount ?? "");
  const [maxCount, setMaxCount] = useState(rule.max_count === null ? "" : String(rule.max_count));
  const [minAmount, setMinAmount] = useState(rule.min_amount ?? "");
  const [reason, setReason] = useState("");
  const propose = useMutation({
    mutationFn: () =>
      api.proposeLimit(rule.code, {
        max_amount: maxAmount === "" ? null : maxAmount,
        max_count: maxCount === "" ? null : Number(maxCount),
        min_amount: minAmount === "" ? null : minAmount,
        reason,
      }),
    onSuccess: async () => {
      await done();
      onClose();
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => propose.mutate());
    },
  });
  const digits = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    if (DIGITS.test(e.target.value)) set(e.target.value);
  };
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => !open && onClose()}
        title={t("config.limitTitle", { code: rule.code })}
        description={t("config.versionBody")}
        closeLabel={t("common.close")}
        footer={
          <Button
            loading={propose.isPending}
            disabled={reason.trim().length < 10}
            onClick={() => propose.mutate()}
          >
            {t("config.propose")}
          </Button>
        }
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label={t("config.maxAmount")}
            hint={t("config.emptyNone")}
            inputMode="numeric"
            value={maxAmount}
            onChange={digits(setMaxAmount)}
          />
          <TextField
            label={t("config.maxCount")}
            hint={t("config.emptyNone")}
            inputMode="numeric"
            value={maxCount}
            onChange={digits(setMaxCount)}
          />
          <TextField
            label={t("config.minAmount")}
            hint={t("config.emptyNone")}
            inputMode="numeric"
            value={minAmount}
            onChange={digits(setMinAmount)}
          />
          <div className="sm:col-span-2">
            <ReasonField value={reason} onChange={setReason} />
          </div>
          <div className="sm:col-span-2">
            {!needsStepUp(propose.error) && <FormError error={propose.error} />}
          </div>
        </div>
      </Dialog>
      {dialog}
    </>
  );
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  return (
    <Badge tone={status === "ACTIVE" ? "success" : "warning"}>
      {t(`config.status.${status}`, { defaultValue: status })}
    </Badge>
  );
}

export function ConfigPage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const me = useStaffMe();
  const [fee, setFee] = useState<FeeRule | null>(null);
  const [limit, setLimit] = useState<LimitRule | null>(null);
  const fees = useQuery({ queryKey: adminKeys.fees, queryFn: api.fees });
  const limits = useQuery({ queryKey: adminKeys.limits, queryFn: api.limits });
  const canPropose = me.data?.permissions.includes("config:propose:any") ?? false;
  const hasDraft = (rows: { code: string; status: string }[], code: string) =>
    rows.some((r) => r.code === code && r.status === "DRAFT");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("config.title")} description={t("config.description")} />
      <Card title={t("config.fees")}>
        {fees.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : fees.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("config.error")}
            onRetry={() => void fees.refetch()}
          />
        ) : (
          <DataTable
            caption={t("config.fees")}
            headers={[
              t("config.rule"),
              t("config.fixed"),
              t("config.bps"),
              t("config.minFee"),
              t("config.maxFee"),
              t("config.version"),
              "",
            ]}
          >
            {fees.data.map((r) => (
              <tr key={r.id}>
                <td className="py-2 pr-3">
                  <span className="block font-mono text-xs">{r.code}</span>
                  <span className="text-xs text-fg-muted">
                    {t(`txnType.${r.txn_type}`, { defaultValue: r.txn_type })}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  <Money amount={r.fixed} />
                </td>
                <td className="tabular py-2 pr-3">{r.bps}</td>
                <td className="py-2 pr-3">
                  <Money amount={r.min_fee} />
                </td>
                <td className="py-2 pr-3">{r.max_fee ? <Money amount={r.max_fee} /> : "—"}</td>
                <td className="py-2 pr-3">
                  v{r.version} <StatusBadge status={r.status} />
                </td>
                <td className="py-2 pr-3">
                  {canPropose && r.status === "ACTIVE" && !hasDraft(fees.data, r.code) && (
                    <Button size="sm" variant="secondary" onClick={() => setFee(r)}>
                      {t("config.change")}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Card>
      <Card title={t("config.limits")}>
        {limits.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : limits.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("config.error")}
            onRetry={() => void limits.refetch()}
          />
        ) : (
          <DataTable
            caption={t("config.limits")}
            headers={[
              t("config.rule"),
              t("config.scope"),
              t("config.period"),
              t("config.maxAmount"),
              t("config.maxCount"),
              t("config.version"),
              "",
            ]}
          >
            {limits.data.map((r) => (
              <tr key={r.id}>
                <td className="py-2 pr-3">
                  <span className="block font-mono text-xs">{r.code}</span>
                  <span className="text-xs text-fg-muted">
                    {t(`txnType.${r.txn_type}`, { defaultValue: r.txn_type })} · {r.direction}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  {r.scope}
                  {r.scope_value !== null ? ` ${r.scope_value}` : ""}
                </td>
                <td className="py-2 pr-3">{r.period}</td>
                <td className="py-2 pr-3">
                  {r.max_amount ? <Money amount={r.max_amount} /> : "—"}
                </td>
                <td className="tabular py-2 pr-3">{r.max_count ?? "—"}</td>
                <td className="py-2 pr-3">
                  v{r.version} <StatusBadge status={r.status} />
                </td>
                <td className="py-2 pr-3">
                  {canPropose && r.status === "ACTIVE" && !hasDraft(limits.data, r.code) && (
                    <Button size="sm" variant="secondary" onClick={() => setLimit(r)}>
                      {t("config.change")}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Card>
      {fee && <FeeDialog rule={fee} onClose={() => setFee(null)} />}
      {limit && <LimitDialog rule={limit} onClose={() => setLimit(null)} />}
    </div>
  );
}

// ------------------------------------------------------------------ staff and roles

function RoleDialog({
  member,
  roles,
  onClose,
}: {
  member: StaffMember;
  roles: string[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const api = useAdminApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { guard, dialog } = useStepUp();
  const [op, setOp] = useState<"GRANT" | "REVOKE">("GRANT");
  const options = op === "GRANT" ? roles.filter((r) => !member.roles.includes(r)) : member.roles;
  const [role, setRole] = useState(options[0] ?? "");
  const [reason, setReason] = useState("");
  const propose = useMutation({
    mutationFn: () => api.proposeRoleChange(member.id, { role, op, reason }),
    onSuccess: async () => {
      toast.show({ title: t("staff.proposed"), tone: "success" });
      onClose();
      await queryClient.invalidateQueries({ queryKey: financeKeys.allApprovals });
    },
    onError: (error) => {
      if (needsStepUp(error)) guard(() => propose.mutate());
    },
  });
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => !open && onClose()}
        title={t("staff.changeTitle", { email: member.email })}
        description={t("staff.changeBody")}
        closeLabel={t("common.close")}
        footer={
          <Button
            loading={propose.isPending}
            disabled={!role || reason.trim().length < 10}
            onClick={() => propose.mutate()}
          >
            {t("config.propose")}
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <SelectField
            label={t("staff.op")}
            value={op}
            options={["GRANT", "REVOKE"] as const}
            onChange={(v) => {
              setOp(v);
              const next =
                v === "GRANT" ? roles.filter((r) => !member.roles.includes(r)) : member.roles;
              setRole(next[0] ?? "");
            }}
            labelFor={(v) => t(`staff.ops.${v}`)}
          />
          {options.length > 0 && (
            <SelectField
              label={t("staff.role")}
              value={role}
              options={options}
              onChange={setRole}
              labelFor={(r) => r}
            />
          )}
          <ReasonField value={reason} onChange={setReason} />
          {!needsStepUp(propose.error) && <FormError error={propose.error} />}
        </div>
      </Dialog>
      {dialog}
    </>
  );
}

export function StaffPage() {
  const { t } = useTranslation();
  const api = useAdminApi();
  const me = useStaffMe();
  const date = useDateFormat();
  const [member, setMember] = useState<StaffMember | null>(null);
  const staff = useQuery({ queryKey: adminKeys.staff, queryFn: api.staff });
  const catalog = useQuery({ queryKey: adminKeys.roles, queryFn: api.roles });
  const staffRoles = (catalog.data?.roles ?? []).filter((r) => r.is_staff).map((r) => r.code);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("staff.title")}
        description={t("staff.description")}
        actions={
          <Link
            to="/approvals"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            {t("nav.approvals")}
          </Link>
        }
      />
      <Card title={t("staff.members")}>
        {staff.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : staff.isError ? (
          <ErrorState
            headingLevel={2}
            title={t("staff.error")}
            onRetry={() => void staff.refetch()}
          />
        ) : (
          <DataTable
            caption={t("staff.members")}
            headers={[t("staff.email"), t("staff.roles"), t("staff.lastLogin"), ""]}
          >
            {staff.data.map((m) => (
              <tr key={m.id}>
                <td className="py-2 pr-3">
                  <span className="block font-medium">{m.full_name}</span>
                  <span className="text-xs text-fg-muted">{m.email}</span>
                </td>
                <td className="py-2 pr-3">
                  <span className="flex flex-wrap gap-1">
                    {m.roles.map((r) => (
                      <Badge key={r} tone="info">
                        {r}
                      </Badge>
                    ))}
                  </span>
                </td>
                <td className="py-2 pr-3">{date(m.last_login_at)}</td>
                <td className="py-2 pr-3">
                  {m.id !== me.data?.id && (
                    <Button size="sm" variant="secondary" onClick={() => setMember(m)}>
                      {t("staff.change")}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </Card>
      {catalog.data && (
        <Card title={t("staff.catalog")}>
          <p className="mb-3 text-sm text-fg-muted">{t("staff.catalogHint")}</p>
          <ul className="grid gap-3 md:grid-cols-2">
            {catalog.data.roles
              .filter((r) => r.is_staff)
              .map((r) => (
                <li key={r.code} className="rounded-lg border border-border p-3">
                  <h3 className="text-sm font-semibold text-fg">
                    {r.code} · {r.name}
                  </h3>
                  <p className="mt-1 text-xs text-fg-muted">{r.permissions.join(", ")}</p>
                </li>
              ))}
          </ul>
        </Card>
      )}
      {member && <RoleDialog member={member} roles={staffRoles} onClose={() => setMember(null)} />}
    </div>
  );
}
