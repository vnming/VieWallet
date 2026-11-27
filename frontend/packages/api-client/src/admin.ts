/**
 * Typed calls for the Phase 8 admin console: dashboard, operations queue, system health,
 * simulator panel, customer lookup, staff refunds, fee/limit versions, roles, provider
 * reconciliation and the ticket desk. Every number shown comes from these endpoints.
 */
import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type Dashboard = Schemas["DashboardOut"];
export type OperationsQueue = Schemas["OperationsQueueOut"];
export type SystemHealth = Schemas["SystemHealthOut"];
export type SimulatorPanel = Schemas["SimulatorOut"];
export type SimulatorMode = Schemas["ModeIn"]["mode"];
export type SimulatorAction = Schemas["SimActionIn"]["action"];
export type UserSummary = Schemas["UserSummaryOut"];
export type UserDetail = Schemas["UserDetailOut"];
export type RevealedPii = Schemas["RevealedPiiOut"];
export type FeeRule = Schemas["FeeRuleOut"];
export type LimitRule = Schemas["LimitRuleOut"];
export type FeeVersionInput = Schemas["FeeVersionIn"];
export type LimitVersionInput = Schemas["LimitVersionIn"];
export type RoleCatalog = Schemas["RoleCatalogOut"];
export type StaffMember = Schemas["StaffMemberOut"];
export type RoleChangeInput = Schemas["RoleChangeIn"];
export type ProviderRun = Schemas["ProviderRunOut"];
export type ReconItem = Schemas["ReconItemOut"];
export type ReconItemStatus = Schemas["ItemUpdateIn"]["status"];
export type StaffTicket = Schemas["StaffTicketOut"];
export type StaffTicketDetail = Schemas["StaffTicketDetailOut"];
export type StaffMessageInput = Schemas["StaffMessageIn"];
export type TicketStatus = Schemas["TicketOut"]["status"];

export const adminKeys = {
  dashboard: ["admin", "dashboard"] as const,
  queue: ["admin", "operations-queue"] as const,
  health: ["admin", "system-health"] as const,
  simulator: ["admin", "simulator"] as const,
  users: (q: string) => ["admin", "users", q] as const,
  user: (id: string) => ["admin", "user", id] as const,
  fees: ["admin", "config", "fees"] as const,
  limits: ["admin", "config", "limits"] as const,
  roles: ["admin", "roles"] as const,
  staff: ["admin", "staff"] as const,
  reconItems: ["admin", "reconciliation", "items"] as const,
  tickets: (status: string) => ["admin", "tickets", status] as const,
  allTickets: ["admin", "tickets"] as const,
  ticket: (reference: string) => ["admin", "ticket", reference] as const,
};

export const adminApi = (client: ApiClient) => ({
  dashboard: async () => unwrap(await client.GET("/api/v1/admin/dashboard")),
  operationsQueue: async () => unwrap(await client.GET("/api/v1/admin/operations-queue")),
  systemHealth: async () => unwrap(await client.GET("/api/v1/admin/system-health")),

  simulator: async () => unwrap(await client.GET("/api/v1/admin/simulator")),
  setSimulatorMode: async (mode: SimulatorMode, reason: string) =>
    unwrap(await client.PUT("/api/v1/admin/simulator/mode", { body: { mode, reason } })),
  simulatorAction: async (operationKey: string, action: SimulatorAction, reason: string) =>
    unwrap(
      await client.POST("/api/v1/admin/simulator/operations/{operation_key}/actions", {
        params: { path: { operation_key: operationKey } },
        body: { action, reason },
      }),
    ),

  searchUsers: async (q: string) =>
    unwrap(await client.GET("/api/v1/admin/users", { params: { query: { q } } })),
  user: async (id: string) =>
    unwrap(
      await client.GET("/api/v1/admin/users/{user_id}", { params: { path: { user_id: id } } }),
    ),
  revealPii: async (id: string) =>
    unwrap(
      await client.POST("/api/v1/admin/users/{user_id}/pii-reveals", {
        params: { path: { user_id: id } },
      }),
    ),

  proposeRefund: async (
    reference: string,
    amount: string,
    reason: string,
    idempotencyKey: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/admin/transactions/{reference}/refund-requests", {
        params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
        body: { amount, reason },
      }),
    ),
  proposeReversal: async (reference: string, reason: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/admin/transactions/{reference}/reversals", {
        params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
        body: { reason },
      }),
    ),

  fees: async () => unwrap(await client.GET("/api/v1/admin/config/fees")),
  limits: async () => unwrap(await client.GET("/api/v1/admin/config/limits")),
  proposeFee: async (code: string, body: FeeVersionInput) =>
    unwrap(
      await client.POST("/api/v1/admin/config/fees/{code}/versions", {
        params: { path: { code } },
        body,
      }),
    ),
  proposeLimit: async (code: string, body: LimitVersionInput) =>
    unwrap(
      await client.POST("/api/v1/admin/config/limits/{code}/versions", {
        params: { path: { code } },
        body,
      }),
    ),

  roles: async () => unwrap(await client.GET("/api/v1/admin/roles")),
  staff: async () => unwrap(await client.GET("/api/v1/admin/staff")),
  proposeRoleChange: async (userId: string, body: RoleChangeInput) =>
    unwrap(
      await client.POST("/api/v1/admin/staff/{user_id}/role-changes", {
        params: { path: { user_id: userId } },
        body,
      }),
    ),

  runProviderReconciliation: async (businessDate: string) =>
    unwrap(
      await client.POST("/api/v1/admin/reconciliation/provider-runs", {
        body: { business_date: businessDate },
      }),
    ),
  reconItems: async () => unwrap(await client.GET("/api/v1/admin/reconciliation/items")),
  updateReconItem: async (id: string, status: ReconItemStatus, note: string) =>
    unwrap(
      await client.POST("/api/v1/admin/reconciliation/items/{item_id}", {
        params: { path: { item_id: id } },
        body: { status, note },
      }),
    ),

  tickets: async (status: TicketStatus | null) =>
    unwrap(
      await client.GET("/api/v1/admin/tickets", {
        params: { query: { ...(status ? { status } : {}), page: 1, page_size: 50 } },
      }),
    ),
  ticket: async (reference: string) =>
    unwrap(
      await client.GET("/api/v1/admin/tickets/{reference}", {
        params: { path: { reference } },
      }),
    ),
  replyTicket: async (reference: string, body: StaffMessageInput) =>
    unwrap(
      await client.POST("/api/v1/admin/tickets/{reference}/messages", {
        params: { path: { reference } },
        body,
      }),
    ),
});

export function useAdminApi() {
  return adminApi(useApiClient());
}

export function useDashboard() {
  const api = useAdminApi();
  return useQuery({
    queryKey: adminKeys.dashboard,
    queryFn: api.dashboard,
    refetchInterval: 30_000,
  });
}

export function useSystemHealth() {
  const api = useAdminApi();
  return useQuery({
    queryKey: adminKeys.health,
    queryFn: api.systemHealth,
    refetchInterval: 15_000,
  });
}
