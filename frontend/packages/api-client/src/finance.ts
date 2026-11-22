/**
 * Typed calls for wallet, transaction history, approvals and reconciliation endpoints.
 * Amounts stay strings of integer minor units end to end (ADR-0003).
 */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type WalletInfo = Schemas["WalletOut"];
export type AdminWallet = Schemas["AdminWalletOut"];
export type TransactionItem = Schemas["TransactionOut"];
export type TransactionDetail = Schemas["TransactionDetailOut"];
export type TransactionPage = Schemas["TransactionPageOut"];
export type AdminTransaction = Schemas["AdminTransactionOut"];
export type Approval = Schemas["ApprovalOut"];
export type ApprovalPage = Schemas["ApprovalPageOut"];
export type AdjustmentInput = Schemas["AdjustmentIn"];
export type ReconciliationRun = Schemas["ReconciliationRunDetailOut"];
export type ReconciliationRunPage = Schemas["ReconciliationRunPageOut"];
export type ApprovalStatus = Schemas["ApprovalStatus"];
export type TxnType = Schemas["TxnType"];
export type TxnStatus = Schemas["TxnStatus"];

export interface TransactionFilters {
  type?: TxnType | undefined;
  status?: TxnStatus | undefined;
}

export const financeKeys = {
  wallet: ["wallet"] as const,
  transactions: (filters: TransactionFilters) => ["transactions", filters] as const,
  allTransactions: ["transactions"] as const,
  transaction: (reference: string) => ["transaction", reference] as const,
  adminWallet: (number: string) => ["admin", "wallet", number] as const,
  adminTransaction: (reference: string) => ["admin", "transaction", reference] as const,
  approvals: (status: string, page: number) => ["admin", "approvals", status, page] as const,
  allApprovals: ["admin", "approvals"] as const,
  reconciliation: (page: number) => ["admin", "reconciliation", page] as const,
  allReconciliation: ["admin", "reconciliation"] as const,
};

/** A fresh key per user intent; retries of the same intent reuse it (financial-processing §7). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export const financeApi = (client: ApiClient) => ({
  // --- customer -------------------------------------------------------------------------
  wallet: async () => unwrap(await client.GET("/api/v1/wallet")),
  freezeWallet: async (reason: string) =>
    unwrap(await client.POST("/api/v1/wallet/freeze", { body: { reason } })),
  unfreezeWallet: async (pin: string) =>
    unwrap(await client.POST("/api/v1/wallet/unfreeze", { body: { pin } })),
  transactions: async (filters: TransactionFilters, cursor?: string, limit = 20) =>
    unwrap(
      await client.GET("/api/v1/transactions", {
        params: {
          query: {
            limit,
            ...(cursor ? { cursor } : {}),
            ...(filters.type ? { type: filters.type } : {}),
            ...(filters.status ? { status: filters.status } : {}),
          },
        },
      }),
    ),
  transaction: async (reference: string) =>
    unwrap(
      await client.GET("/api/v1/transactions/{reference}", {
        params: { path: { reference } },
      }),
    ),

  // --- staff ----------------------------------------------------------------------------
  adminWallet: async (walletNumber: string) =>
    unwrap(
      await client.GET("/api/v1/admin/wallets/{wallet_number}", {
        params: { path: { wallet_number: walletNumber } },
      }),
    ),
  adminFreeze: async (walletNumber: string, reason: string) =>
    unwrap(
      await client.POST("/api/v1/admin/wallets/{wallet_number}/freeze", {
        params: { path: { wallet_number: walletNumber } },
        body: { reason },
      }),
    ),
  requestUnfreeze: async (walletNumber: string, reason: string) =>
    unwrap(
      await client.POST("/api/v1/admin/wallets/{wallet_number}/unfreeze-requests", {
        params: { path: { wallet_number: walletNumber } },
        body: { reason },
      }),
    ),
  adminTransaction: async (reference: string) =>
    unwrap(
      await client.GET("/api/v1/admin/transactions/{reference}", {
        params: { path: { reference } },
      }),
    ),
  proposeAdjustment: async (body: AdjustmentInput, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/admin/adjustments", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body,
      }),
    ),
  approvals: async (status: ApprovalStatus, page: number) =>
    unwrap(
      await client.GET("/api/v1/admin/approvals", {
        params: { query: { status, page, page_size: 20 } },
      }),
    ),
  approve: async (id: string, note: string) =>
    unwrap(
      await client.POST("/api/v1/admin/approvals/{approval_id}/approve", {
        params: { path: { approval_id: id } },
        body: { note: note || null },
      }),
    ),
  reject: async (id: string, note: string) =>
    unwrap(
      await client.POST("/api/v1/admin/approvals/{approval_id}/reject", {
        params: { path: { approval_id: id } },
        body: { note },
      }),
    ),
  cancelApproval: async (id: string) =>
    unwrap(
      await client.POST("/api/v1/admin/approvals/{approval_id}/cancel", {
        params: { path: { approval_id: id } },
      }),
    ),
  reconciliationRuns: async (page: number) =>
    unwrap(
      await client.GET("/api/v1/admin/reconciliation/runs", {
        params: { query: { page, page_size: 20 } },
      }),
    ),
  runReconciliation: async () => unwrap(await client.POST("/api/v1/admin/reconciliation/runs")),
});

export function useFinanceApi() {
  return financeApi(useApiClient());
}

export function useWallet(enabled = true) {
  const api = useFinanceApi();
  return useQuery({
    queryKey: financeKeys.wallet,
    queryFn: api.wallet,
    enabled,
    staleTime: 10_000,
  });
}

export function useTransactions(filters: TransactionFilters, limit = 20) {
  const api = useFinanceApi();
  return useInfiniteQuery({
    queryKey: [...financeKeys.transactions(filters), limit],
    queryFn: ({ pageParam }) => api.transactions(filters, pageParam, limit),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: TransactionPage) => last.next_cursor ?? undefined,
  });
}

export function useTransaction(reference: string) {
  const api = useFinanceApi();
  return useQuery({
    queryKey: financeKeys.transaction(reference),
    queryFn: () => api.transaction(reference),
  });
}
