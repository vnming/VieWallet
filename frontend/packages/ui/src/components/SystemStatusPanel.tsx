import { clsx } from "clsx";
import { CheckCircle2, XCircle } from "lucide-react";

import { Skeleton } from "./Skeleton";
import { ErrorState } from "./States";

export type CheckState = "ok" | "fail";

export interface SystemStatusData {
  status: CheckState;
  version: string;
  environment: string;
  server_time: string;
  checks: Record<string, CheckState>;
}

export interface SystemStatusLabels {
  heading: string;
  loading: string;
  overallOk: string;
  overallFail: string;
  version: string;
  environment: string;
  serverTime: string;
  checks: Record<string, string>;
  ok: string;
  fail: string;
  errorTitle: string;
  errorDescription: string;
  retry: string;
  correlation: string;
}

export interface SystemStatusPanelProps {
  labels: SystemStatusLabels;
  data?: SystemStatusData | undefined;
  isLoading: boolean;
  error?: { correlationId?: string | null } | null | undefined;
  onRetry?: () => void;
  formatTime: (iso: string) => string;
}

/** Presentational panel for GET /api/v1/system/status; apps provide data and labels. */
export function SystemStatusPanel({
  labels,
  data,
  isLoading,
  error,
  onRetry,
  formatTime,
}: SystemStatusPanelProps) {
  if (isLoading) {
    return (
      <div role="status" aria-label={labels.loading} className="flex flex-col gap-3">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <ErrorState
        title={labels.errorTitle}
        description={labels.errorDescription}
        correlationId={error?.correlationId}
        correlationLabel={labels.correlation}
        retryLabel={labels.retry}
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  const healthy = data.status === "ok";
  return (
    <div className="flex flex-col gap-5">
      <p
        className={clsx(
          "flex items-center gap-2 text-base font-semibold",
          healthy
            ? "text-success-700 dark:text-success-500"
            : "text-danger-700 dark:text-danger-500",
        )}
      >
        {healthy ? (
          <CheckCircle2 aria-hidden className="size-5" />
        ) : (
          <XCircle aria-hidden className="size-5" />
        )}
        {healthy ? labels.overallOk : labels.overallFail}
      </p>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {Object.entries(data.checks).map(([name, state]) => (
          <li key={name} className="flex items-center justify-between px-4 py-3 text-sm">
            <span className="text-fg">{labels.checks[name] ?? name}</span>
            <span
              className={clsx(
                "inline-flex items-center gap-1.5 font-medium",
                state === "ok"
                  ? "text-success-700 dark:text-success-500"
                  : "text-danger-700 dark:text-danger-500",
              )}
            >
              {state === "ok" ? (
                <CheckCircle2 aria-hidden className="size-4" />
              ) : (
                <XCircle aria-hidden className="size-4" />
              )}
              {state === "ok" ? labels.ok : labels.fail}
            </span>
          </li>
        ))}
      </ul>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-fg-muted">{labels.version}</dt>
          <dd className="font-mono text-fg">{data.version}</dd>
        </div>
        <div>
          <dt className="text-fg-muted">{labels.environment}</dt>
          <dd className="font-mono text-fg">{data.environment}</dd>
        </div>
        <div>
          <dt className="text-fg-muted">{labels.serverTime}</dt>
          <dd className="tabular text-fg">{formatTime(data.server_time)}</dd>
        </div>
      </dl>
    </div>
  );
}
