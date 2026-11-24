import type { ApiMoney } from "@viewallet/shared";
import { clsx } from "clsx";
import { ArrowDownLeft, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";

import { AmountText } from "./AmountText";
import { StatusBadge, type TransactionStatus } from "./StatusBadge";

export interface TransactionRowProps {
  title: string;
  subtitle?: ReactNode;
  money: ApiMoney;
  direction: "IN" | "OUT";
  status: TransactionStatus;
  locale?: "vi" | "en";
  /** Hide the badge for the common "completed" case to keep lists calm. */
  hideCompletedBadge?: boolean;
  /** Privacy mode: the amount is replaced by dots (and its accessible name says so). */
  maskedLabel?: string;
  className?: string;
}

/** One line of transaction history: direction icon, title, time, signed amount, status. */
export function TransactionRow({
  title,
  subtitle,
  money,
  direction,
  status,
  locale = "vi",
  hideCompletedBadge = true,
  maskedLabel,
  className,
}: TransactionRowProps) {
  const Icon = direction === "IN" ? ArrowDownLeft : ArrowUpRight;
  const settled = status === "COMPLETED";
  return (
    <div className={clsx("flex items-center gap-3 py-3", className)}>
      <span
        className={clsx(
          "grid size-10 shrink-0 place-items-center rounded-full",
          direction === "IN" ? "bg-primary-soft text-primary" : "bg-bg-subtle text-fg-muted",
        )}
      >
        <Icon aria-hidden className="size-5" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium text-fg">{title}</span>
        {subtitle && <span className="truncate text-xs text-fg-muted">{subtitle}</span>}
      </span>
      <span className="flex flex-col items-end gap-1">
        {maskedLabel ? (
          <span className="font-semibold tabular-nums text-fg-muted" aria-label={maskedLabel}>
            ••••••
          </span>
        ) : (
          <AmountText
            money={money}
            direction={direction}
            locale={locale === "en" ? "en-US" : "vi-VN"}
            className={clsx(!settled && "text-fg-muted")}
          />
        )}
        {(!settled || !hideCompletedBadge) && <StatusBadge status={status} locale={locale} />}
      </span>
    </div>
  );
}
