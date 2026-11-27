/**
 * What a checker approves, per action type: the stored payload shown as a summary, and for
 * configuration changes a before/after table with changed values highlighted.
 */
import type { Approval } from "@viewallet/api-client";
import { Badge } from "@viewallet/ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { Money } from "./common";

type Payload = Record<string, unknown>;

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function DiffTable({ before, after }: { before: Payload; after: Payload }) {
  const { t } = useTranslation();
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  return (
    <table className="mt-2 w-full max-w-xl text-left text-sm">
      <caption className="sr-only">{t("approvals.diff")}</caption>
      <thead className="border-b border-border text-xs uppercase tracking-wide text-fg-muted">
        <tr>
          <th scope="col" className="py-1 pr-3">
            {t("approvals.field")}
          </th>
          <th scope="col" className="py-1 pr-3">
            {t("approvals.before")}
          </th>
          <th scope="col" className="py-1 pr-3">
            {t("approvals.after")}
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {keys.map((key) => {
          const changed = text(before[key]) !== text(after[key]);
          return (
            <tr key={key}>
              <th scope="row" className="py-1 pr-3 font-mono text-xs font-normal">
                {key}
              </th>
              <td className="tabular py-1 pr-3">{text(before[key])}</td>
              <td
                className={`tabular py-1 pr-3 ${changed ? "font-semibold text-fg" : "text-fg-muted"}`}
              >
                {text(after[key])}
                {changed && (
                  <Badge tone="warning" className="ml-2">
                    {t("approvals.changed")}
                  </Badge>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ApprovalDetails({ approval }: { approval: Approval }) {
  const { t } = useTranslation();
  const p: Payload = approval.payload;
  switch (approval.action_type) {
    case "REFUND":
      return (
        <div className="flex flex-col gap-1 text-sm">
          <span className="flex flex-wrap items-center gap-2">
            <Money amount={text(p.amount)} currency={text(p.currency)} />
            <span className="text-fg-muted">
              {t("approvals.refundOf")}{" "}
              <Link
                to={`/transactions/${text(p.original_reference)}`}
                className="font-mono text-primary underline-offset-4 hover:underline"
              >
                {text(p.original_reference)}
              </Link>
              {p.merchant ? ` · ${text(p.merchant)}` : ""}
            </span>
          </span>
          <span className="text-xs text-fg-muted">
            {t("approvals.paymentAmount")}: {text(p.payment_amount)} ·{" "}
            {t("approvals.alreadyRefunded")}: {text(p.already_refunded)}
          </span>
        </div>
      );
    case "REVERSAL":
      return (
        <span className="flex flex-wrap items-center gap-2 text-sm">
          <Money amount={text(p.amount)} currency={text(p.currency)} />
          <span className="text-fg-muted">
            {t("approvals.reversalOf")}{" "}
            <Link
              to={`/transactions/${text(p.original_reference)}`}
              className="font-mono text-primary underline-offset-4 hover:underline"
            >
              {text(p.original_reference)}
            </Link>
          </span>
        </span>
      );
    case "FEE_RULE_CHANGE":
    case "LIMIT_RULE_CHANGE":
      return (
        <div className="text-sm">
          <span className="font-mono text-xs">{text(p.code)}</span>{" "}
          <span className="text-fg-muted">
            v{text(p.from_version)} → v{text(p.to_version)}
          </span>
          <DiffTable before={(p.before ?? {}) as Payload} after={(p.after ?? {}) as Payload} />
        </div>
      );
    case "ROLE_CHANGE":
      return (
        <span className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={p.op === "GRANT" ? "info" : "danger"}>{t(`staff.ops.${text(p.op)}`)}</Badge>
          <span className="font-semibold">{text(p.role)}</span>
          <span className="text-fg-muted">{text(p.email)}</span>
        </span>
      );
    case "RISK_RULE_CHANGE":
      return (
        <div className="text-sm">
          <span className="font-mono text-xs">{text(p.code)}</span>{" "}
          <span className="text-fg-muted">v{text(p.version)}</span>
          <DiffTable
            before={{}}
            after={{ score: p.score, block: p.block, enabled: p.enabled, params: p.params }}
          />
        </div>
      );
    default:
      return <span className="text-sm text-fg">{text(p.wallet_number_masked)}</span>;
  }
}
