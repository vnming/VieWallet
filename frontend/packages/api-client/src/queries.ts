import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type SystemStatus, unwrap } from "./client";
import { useApiClient } from "./context";

export const queryKeys = {
  systemStatus: ["system", "status"] as const,
};

export async function fetchSystemStatus(client: ApiClient): Promise<SystemStatus> {
  return unwrap(await client.GET("/api/v1/system/status"));
}

export function useSystemStatus() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.systemStatus,
    queryFn: () => fetchSystemStatus(client),
    refetchInterval: 30_000,
  });
}
