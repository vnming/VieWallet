import { ApiError, useStaffMe } from "@viewallet/api-client";
import { ErrorState, InlineAlert, Skeleton } from "@viewallet/ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router";

import { rememberedNextStep } from "./session";

/** Guard for staff pages. The API enforces MFA and permissions; this only routes the UI. */
export function RequireStaff() {
  const { t } = useTranslation();
  const location = useLocation();
  const me = useStaffMe();

  if (me.isPending) {
    return (
      <div role="status" aria-label={t("auth.checkingSession")} className="flex flex-col gap-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (me.error instanceof ApiError && me.error.code === "MFA_REQUIRED") {
    const step = rememberedNextStep();
    return (
      <Navigate to={step === "MFA_ENROLLMENT_REQUIRED" ? "/mfa/setup" : "/mfa/verify"} replace />
    );
  }
  if (me.isError) {
    return (
      <ErrorState
        title={t("status.errorTitle")}
        description={t("status.errorDescription")}
        retryLabel={t("status.retry")}
        onRetry={() => void me.refetch()}
      />
    );
  }
  if (me.data === null) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return <Outlet />;
}

export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const me = useStaffMe();
  if (!me.data?.permissions.includes(permission)) {
    return <InlineAlert tone="warning" title={t("auth.noPermission")} />;
  }
  return <>{children}</>;
}
