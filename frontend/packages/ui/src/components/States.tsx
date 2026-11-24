import { clsx } from "clsx";
import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "./Button";

export interface EmptyStateProps {
  title: string;
  /** Heading level of the title; use 2 when the state sits directly under the page h1. */
  headingLevel?: 2 | 3;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}

export function EmptyState({
  title,
  headingLevel = 3,
  description,
  action,
  icon,
  className,
}: EmptyStateProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className={clsx("flex flex-col items-center gap-3 px-6 py-10 text-center", className)}>
      <span className="grid size-12 place-items-center rounded-full bg-primary-soft text-primary">
        {icon ?? <Inbox aria-hidden className="size-6" />}
      </span>
      <Heading className="text-base font-semibold text-fg">{title}</Heading>
      {description && <p className="max-w-sm text-sm text-fg-muted">{description}</p>}
      {action}
    </div>
  );
}

export interface ErrorStateProps {
  title: string;
  /** Heading level of the title; use 2 when the state sits directly under the page h1. */
  headingLevel?: 2 | 3;
  description?: ReactNode;
  /** Shown so users can quote it to support. */
  correlationId?: string | null | undefined;
  correlationLabel?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}

export function ErrorState({
  title,
  headingLevel = 3,
  description,
  correlationId,
  correlationLabel = "Mã tham chiếu",
  onRetry,
  retryLabel = "Thử lại",
  className,
}: ErrorStateProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div
      role="alert"
      className={clsx("flex flex-col items-center gap-3 px-6 py-10 text-center", className)}
    >
      <span className="grid size-12 place-items-center rounded-full bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-500">
        <AlertTriangle aria-hidden className="size-6" />
      </span>
      <Heading className="text-base font-semibold text-fg">{title}</Heading>
      {description && <p className="max-w-sm text-sm text-fg-muted">{description}</p>}
      {correlationId && (
        <p className="text-xs text-fg-muted">
          {correlationLabel}: <code className="font-mono">{correlationId}</code>
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
