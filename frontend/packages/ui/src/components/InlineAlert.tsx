import { clsx } from "clsx";
import { CheckCircle2, Info, TriangleAlert, XCircle } from "lucide-react";
import type { ReactNode } from "react";

export type AlertTone = "info" | "success" | "warning" | "danger";

const styles: Record<AlertTone, string> = {
  info: "border-info-500/30 bg-info-100 text-info-700 dark:bg-info-700/15 dark:text-info-500",
  success:
    "border-success-500/30 bg-success-100 text-success-700 dark:bg-success-700/15 dark:text-success-500",
  warning:
    "border-warning-500/30 bg-warning-100 text-warning-700 dark:bg-warning-700/15 dark:text-warning-500",
  danger:
    "border-danger-500/30 bg-danger-100 text-danger-700 dark:bg-danger-700/15 dark:text-danger-500",
};
const icons = { info: Info, success: CheckCircle2, warning: TriangleAlert, danger: XCircle };

export interface InlineAlertProps {
  tone?: AlertTone;
  title: ReactNode;
  children?: ReactNode;
  className?: string;
}

/** In-flow message (form errors, confirmations). Danger alerts are announced immediately. */
export function InlineAlert({ tone = "info", title, children, className }: InlineAlertProps) {
  const Icon = icons[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={clsx("flex gap-3 rounded-lg border p-3 text-sm", styles[tone], className)}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-0.5 opacity-90">{children}</div>}
      </div>
    </div>
  );
}
