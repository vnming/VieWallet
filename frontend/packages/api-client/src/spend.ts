/**
 * Transfers, QR payments, payment requests, saved recipients and the merchant screens.
 * Every money-moving call carries an Idempotency-Key chosen once per user intent.
 */
import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type Spend = Schemas["SpendOut"];
export type TransferInput = Schemas["TransferIn"];
export type RecipientLookup = Schemas["LookupOut"];
export type SavedRecipient = Schemas["SavedRecipientOut"];
export type ReceiveQr = Schemas["ReceiveQrOut"];
export type PaymentRequest = Schemas["PaymentRequestOut"];
export type PaymentRequestInput = Schemas["PaymentRequestIn"];
export type Merchant = Schemas["MerchantOut"];
export type MerchantPayment = Schemas["MerchantPaymentOut"];
export type SpendKind = "transfers" | "payments";

export const spendKeys = {
  spend: (reference: string) => ["spend", reference] as const,
  recipients: ["recipients"] as const,
  receiveQr: ["wallet", "qr"] as const,
  request: (code: string) => ["payment-request", code] as const,
  requests: (role: "issued" | "incoming") => ["payment-requests", role] as const,
  merchant: ["merchant"] as const,
  merchantPayments: ["merchant", "payments"] as const,
};

export const spendKindOf = (spend: Pick<Spend, "type">): SpendKind =>
  spend.type === "PAYMENT" ? "payments" : "transfers";

export const spendApi = (client: ApiClient) => ({
  quoteTransfer: async (body: TransferInput, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/transfers", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body,
      }),
    ),
  confirm: async (kind: SpendKind, reference: string, pin: string, idempotencyKey: string) =>
    kind === "transfers"
      ? unwrap(
          await client.POST("/api/v1/transfers/{reference}/confirm", {
            params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
            body: { pin },
          }),
        )
      : unwrap(
          await client.POST("/api/v1/payments/{reference}/confirm", {
            params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
            body: { pin },
          }),
        ),
  cancel: async (kind: SpendKind, reference: string) =>
    kind === "transfers"
      ? unwrap(
          await client.POST("/api/v1/transfers/{reference}/cancel", {
            params: { path: { reference } },
          }),
        )
      : unwrap(
          await client.POST("/api/v1/payments/{reference}/cancel", {
            params: { path: { reference } },
          }),
        ),
  get: async (kind: SpendKind, reference: string) =>
    kind === "transfers"
      ? unwrap(
          await client.GET("/api/v1/transfers/{reference}", { params: { path: { reference } } }),
        )
      : unwrap(
          await client.GET("/api/v1/payments/{reference}", { params: { path: { reference } } }),
        ),
  lookup: async (query: { wallet_number?: string; email?: string }) =>
    unwrap(await client.GET("/api/v1/recipients/lookup", { params: { query } })),
  recipients: async () => unwrap(await client.GET("/api/v1/recipients")),
  saveRecipient: async (walletNumber: string, nickname?: string) =>
    unwrap(
      await client.POST("/api/v1/recipients", {
        body: { wallet_number: walletNumber, nickname: nickname || null },
      }),
    ),
  removeRecipient: async (id: string) =>
    unwrap(
      await client.DELETE("/api/v1/recipients/{recipient_id}", {
        params: { path: { recipient_id: id } },
      }),
    ),
  receiveQr: async () => unwrap(await client.GET("/api/v1/wallet/qr")),
  createRequest: async (body: PaymentRequestInput, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/payment-requests", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body,
      }),
    ),
  request: async (code: string) =>
    unwrap(await client.GET("/api/v1/payment-requests/{code}", { params: { path: { code } } })),
  requests: async (role: "issued" | "incoming") =>
    unwrap(await client.GET("/api/v1/payment-requests", { params: { query: { role } } })),
  cancelRequest: async (code: string) =>
    unwrap(
      await client.POST("/api/v1/payment-requests/{code}/cancel", { params: { path: { code } } }),
    ),
  declineRequest: async (code: string) =>
    unwrap(
      await client.POST("/api/v1/payment-requests/{code}/decline", { params: { path: { code } } }),
    ),
  pay: async (code: string, amount: string | null, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/payment-requests/{code}/pay", {
        params: { path: { code }, header: { "Idempotency-Key": idempotencyKey } },
        body: { amount },
      }),
    ),
  merchant: async () => unwrap(await client.GET("/api/v1/merchant")),
  merchantPayments: async () =>
    unwrap(await client.GET("/api/v1/merchant/payments", { params: { query: { limit: 50 } } })),
  refund: async (reference: string, amount: string, reason: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/merchant/payments/{reference}/refunds", {
        params: { path: { reference }, header: { "Idempotency-Key": idempotencyKey } },
        body: { amount, reason },
      }),
    ),
});

export function useSpendApi() {
  return spendApi(useApiClient());
}

/** A transfer/payment; polls every 5 s while a risk review holds it (at most 2 minutes). */
export function useSpend(kind: SpendKind, reference: string) {
  const api = useSpendApi();
  return useQuery({
    queryKey: spendKeys.spend(reference),
    queryFn: () => api.get(kind, reference),
    refetchInterval: (query) =>
      query.state.data?.next_action === "WAIT_REVIEW" && query.state.dataUpdateCount < 24
        ? 5_000
        : false,
  });
}

export function useRecipients() {
  const api = useSpendApi();
  return useQuery({ queryKey: spendKeys.recipients, queryFn: api.recipients });
}

export function useReceiveQr() {
  const api = useSpendApi();
  return useQuery({ queryKey: spendKeys.receiveQr, queryFn: api.receiveQr, staleTime: Infinity });
}

export function usePaymentRequest(code: string) {
  const api = useSpendApi();
  return useQuery({ queryKey: spendKeys.request(code), queryFn: () => api.request(code) });
}

export function usePaymentRequests(role: "issued" | "incoming") {
  const api = useSpendApi();
  return useQuery({ queryKey: spendKeys.requests(role), queryFn: () => api.requests(role) });
}

/** ``retry: false``: a 404 simply means "not a merchant" and hides the merchant screens. */
export function useMerchant(enabled = true) {
  const api = useSpendApi();
  return useQuery({ queryKey: spendKeys.merchant, queryFn: api.merchant, enabled, retry: false });
}

export function useMerchantPayments(enabled: boolean) {
  const api = useSpendApi();
  return useQuery({
    queryKey: spendKeys.merchantPayments,
    queryFn: api.merchantPayments,
    enabled,
  });
}
