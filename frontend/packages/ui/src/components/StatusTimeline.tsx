import { clsx } from "clsx";
import type { ReactNode } from "react";

import { STATUS_STYLES, type TransactionStatus } from "./StatusBadge";

export interface TimelineStep {
  status: TransactionStatus;
  at: string;
  note?: ReactNode;
}

export interface StatusTimelineProps {
  steps: TimelineStep[];
  locale?: "vi" | "en";
  formatTime?: (iso: string) => string;
  className?: string;
}

const toneDot: Record<string, string> = {
  success: "bg-success-100 text-success-700",
  warning: "bg-warning-100 text-warning-700",
  danger: "bg-danger-100 text-danger-700",
  info: "bg-primary-soft text-primary",
  neutral: "bg-bg-subtle text-fg-muted",
};

/** Ordered status history (oldest first). Each step shows icon + label + time. */
export function StatusTimeline({
  steps,
  locale = "vi",
  formatTime = (iso) => new Date(iso).toLocaleString(locale === "en" ? "en-US" : "vi-VN"),
  className,
}: StatusTimelineProps) {
  return (
    <ol className={clsx("flex flex-col", className)}>
      {steps.map((step, index) => {
        const style = STATUS_STYLES[step.status];
        const Icon = style.icon;
        const last = index === steps.length - 1;
        return (
          <li key={`${step.status}-${step.at}-${String(index)}`} className="flex gap-3">
            <span className="flex flex-col items-center">
              <span
                className={clsx(
                  "grid size-7 place-items-center rounded-full",
                  toneDot[style.tone] ?? toneDot.neutral,
                )}
              >
                <Icon aria-hidden className="size-4" />
              </span>
              {!last && <span aria-hidden className="w-px flex-1 bg-border" />}
            </span>
            <span className={clsx("flex flex-col", !last && "pb-4")}>
              <span className="text-sm font-medium text-fg">{style.label[locale]}</span>
              <time dateTime={step.at} className="text-xs text-fg-muted">
                {formatTime(step.at)}
              </time>
              {step.note && <span className="text-xs text-fg-muted">{step.note}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
