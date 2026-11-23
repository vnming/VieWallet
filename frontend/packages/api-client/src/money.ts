/**
 * Deposits, withdrawals, bank accounts and the notification inbox.
 * Every money-moving call carries an Idempotency-Key chosen once per user intent.
 */
import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type MoneyFlow = Schemas["MoneyFlowOut"];
export type Bank = Schemas["BankOut"];
export type BankAccount = Schemas["DestinationOut"];
export type BankAccountInput = Schemas["DestinationIn"];
export type NotificationItem = Schemas["NotificationOut"];
export type NotificationPage = Schemas["NotificationPageOut"];

export const moneyKeys = {
  deposit: (reference: string) => ["deposit", reference] as const,
  withdrawal: (reference: string) => ["withdrawal", reference] as const,
  banks: ["banks"] as const,
  destinations: ["destinations"] as const,
  notifications: ["notifications"] as const,
  unread: ["notifications", "unread"] as const,
};

/** Final states: polling stops once one of these is reached. */
export const FINAL_STATUSES = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "EXPIRED",
  "REVERSED",
  "REFUNDED",
  "PARTIALLY_REFUNDED",
]);

export const moneyApi = (client: ApiClient) => ({
  createDeposit: async (amount: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/deposits", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body: { amount, method: "SIMBANK" },
      }),
    ),
  deposit: async (reference: string) =>
    unwrap(await client.GET("/api/v1/deposits/{reference}", { params: { path: { reference } } })),
  quoteWithdrawal: async (destinationId: string, amount: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/withdrawals", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body: { destination_id: destinationId, amount },
      }),
    ),
  confirmWithdrawal: async (reference: string, pin: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/withdrawals/{reference}/confirm", {
        params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
        body: { pin },
      }),
    ),
  cancelWithdrawal: async (reference: string) =>
    unwrap(
      await client.POST("/api/v1/withdrawals/{reference}/cancel", {
        params: { path: { reference } },
      }),
    ),
  withdrawal: async (reference: string) =>
    unwrap(
      await client.GET("/api/v1/withdrawals/{reference}", { params: { path: { reference } } }),
    ),
  banks: async () => unwrap(await client.GET("/api/v1/wallet/banks")),
  destinations: async () => unwrap(await client.GET("/api/v1/wallet/destinations")),
  addDestination: async (body: BankAccountInput) =>
    unwrap(await client.POST("/api/v1/wallet/destinations", { body })),
  removeDestination: async (id: string) =>
    unwrap(
      await client.DELETE("/api/v1/wallet/destinations/{destination_id}", {
        params: { path: { destination_id: id } },
      }),
    ),
  notifications: async (cursor?: string) =>
    unwrap(
      await client.GET("/api/v1/notifications", {
        params: { query: { limit: 20, ...(cursor ? { cursor } : {}) } },
      }),
    ),
  unreadCount: async () => unwrap(await client.GET("/api/v1/notifications/unread-count")),
  markRead: async (id: string) =>
    unwrap(
      await client.POST("/api/v1/notifications/{notification_id}/read", {
        params: { path: { notification_id: id } },
      }),
    ),
  markAllRead: async () => unwrap(await client.POST("/api/v1/notifications/read-all")),
});

export function useMoneyApi() {
  return moneyApi(useApiClient());
}

/** Polls every 2 s while the transaction is not final, for at most ``maxMs`` (§2 user flow). */
export function useMoneyFlow(kind: "deposit" | "withdrawal", reference: string, maxMs = 60_000) {
  const api = useMoneyApi();
  return useQuery({
    queryKey: kind === "deposit" ? moneyKeys.deposit(reference) : moneyKeys.withdrawal(reference),
    queryFn: () => (kind === "deposit" ? api.deposit(reference) : api.withdrawal(reference)),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && FINAL_STATUSES.has(status)) return false;
      return query.state.dataUpdateCount * 2_000 < maxMs ? 2_000 : false;
    },
  });
}

export function useDestinations(enabled = true) {
  const api = useMoneyApi();
  return useQuery({ queryKey: moneyKeys.destinations, queryFn: api.destinations, enabled });
}

export function useBanks() {
  const api = useMoneyApi();
  return useQuery({ queryKey: moneyKeys.banks, queryFn: api.banks, staleTime: Infinity });
}

export function useUnreadCount(enabled: boolean) {
  const api = useMoneyApi();
  return useQuery({
    queryKey: moneyKeys.unread,
    queryFn: api.unreadCount,
    enabled,
    refetchInterval: 30_000,
  });
}

export function useNotifications() {
  const api = useMoneyApi();
  return useQuery({ queryKey: moneyKeys.notifications, queryFn: () => api.notifications() });
}
