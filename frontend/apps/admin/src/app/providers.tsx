import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ApiClient, ApiClientProvider } from "@viewallet/api-client";
import { ToastProvider } from "@viewallet/ui";
import type { i18n } from "i18next";
import { type ReactNode, useState } from "react";
import { I18nextProvider } from "react-i18next";

import { createQueryClient } from "./queryClient";

export interface ProvidersProps {
  i18n: i18n;
  apiClient: ApiClient;
  queryClient?: QueryClient;
  children: ReactNode;
}

export function Providers({ i18n, apiClient, queryClient, children }: ProvidersProps) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  return (
    <I18nextProvider i18n={i18n}>
      <ApiClientProvider client={apiClient}>
        <QueryClientProvider client={client}>
          <ToastProvider closeLabel={i18n.t("common.close")}>{children}</ToastProvider>
        </QueryClientProvider>
      </ApiClientProvider>
    </I18nextProvider>
  );
}
