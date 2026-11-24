import { useMe } from "@viewallet/api-client";
import { WifiOff } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import { applyTheme, storedTheme } from "./theme";

/** Keeps <html data-theme> in step with the signed-in user's setting. */
export function ThemeSync() {
  const me = useMe();
  const theme = me.data?.theme;
  useEffect(() => {
    applyTheme(theme ?? storedTheme());
  }, [theme]);
  return null;
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

/** The offline state of every page: data may be stale and actions will wait. */
export function OfflineBanner() {
  const { t } = useTranslation();
  const online = useSyncExternalStore(
    subscribe,
    () => navigator.onLine,
    () => true,
  );
  if (online) return null;
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 bg-warning-100 px-4 py-2 text-sm text-warning-700 dark:bg-warning-700/20 dark:text-warning-500"
    >
      <WifiOff aria-hidden className="size-4" />
      {t("status.offline")}
    </div>
  );
}
