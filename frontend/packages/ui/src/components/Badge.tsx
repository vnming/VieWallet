import { clsx } from "clsx";
import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-bg-subtle text-fg-muted border-border",
  primary: "bg-primary-soft text-primary border-jade-300/40",
  success:
    "bg-success-100 text-success-700 border-success-500/30 dark:bg-success-700/20 dark:text-success-500",
  warning:
    "bg-warning-100 text-warning-700 border-warning-500/30 dark:bg-warning-700/20 dark:text-warning-500",
  danger:
    "bg-danger-100 text-danger-700 border-danger-500/30 dark:bg-danger-700/20 dark:text-danger-500",
  info: "bg-info-100 text-info-700 border-info-500/30 dark:bg-info-700/20 dark:text-info-500",
};

export interface BadgeProps {
  tone?: BadgeTone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = "neutral", icon, children, className }: BadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}
