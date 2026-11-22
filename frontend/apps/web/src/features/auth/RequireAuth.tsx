import { useMe } from "@viewallet/api-client";
import { ErrorState, Skeleton } from "@viewallet/ui";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router";

/**
 * Route guard. The backend is the authority on every request; this only keeps signed-out
 * users away from pages that cannot work and sends new users to set their PIN first.
 */
export function RequireAuth() {
  const { t } = useTranslation();
  const location = useLocation();
  const me = useMe();

  if (me.isPending) {
    return (
      <div role="status" aria-label={t("auth.checkingSession")} className="flex flex-col gap-3">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-32 w-full" />
      </div>
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
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (!me.data.pin.is_set && location.pathname !== "/onboarding/pin") {
    return <Navigate to="/onboarding/pin" replace />;
  }
  return <Outlet />;
}
