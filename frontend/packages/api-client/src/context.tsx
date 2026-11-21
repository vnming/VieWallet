import { createContext, type ReactNode, useContext } from "react";

import type { ApiClient } from "./client";

const ApiClientContext = createContext<ApiClient | null>(null);

/** Provides the API client to the component tree (tests inject a client with a mock fetch). */
export function ApiClientProvider({
  client,
  children,
}: {
  client: ApiClient;
  children: ReactNode;
}) {
  return <ApiClientContext.Provider value={client}>{children}</ApiClientContext.Provider>;
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (!client) throw new Error("useApiClient must be used inside <ApiClientProvider>");
  return client;
}
