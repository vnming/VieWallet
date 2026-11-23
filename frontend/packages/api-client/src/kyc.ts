/**
 * KYC (customer flow and reviewer queue), remaining limits, and the risk desk
 * (holds, alerts, flags, rule versions).
 */
import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type KycStatus = Schemas["KycStatusOut"];
export type KycDraft = Schemas["DraftIn"];
export type KycDocumentKind = Schemas["DocumentKind"];
export type KycSubmissionAdmin = Schemas["AdminSubmissionOut"];
export type KycSubmissionDetail = Schemas["AdminSubmissionDetailOut"];
export type KycRejectReason = Schemas["RejectReason"];
export type Limits = Schemas["LimitsOut"];
export type RiskHold = Schemas["HoldOut"];
export type RiskAlert = Schemas["AlertOut"];
export type RiskRule = Schemas["RuleOut"];

export const kycKeys = {
  mine: ["kyc"] as const,
  limits: ["wallet", "limits"] as const,
  queue: ["admin", "kyc", "queue"] as const,
  submission: (id: string) => ["admin", "kyc", id] as const,
  holds: ["admin", "risk", "holds"] as const,
  alerts: (status: string) => ["admin", "risk", "alerts", status] as const,
  rules: ["admin", "risk", "rules"] as const,
};

export const UPLOAD_TYPES = ["image/jpeg", "image/png", "application/pdf"] as const;
type UploadType = (typeof UPLOAD_TYPES)[number];

export const kycApi = (client: ApiClient) => ({
  status: async () => unwrap(await client.GET("/api/v1/kyc")),
  saveDraft: async (body: KycDraft) => unwrap(await client.PUT("/api/v1/kyc/draft", { body })),
  upload: async (kind: KycDocumentKind, file: Blob) =>
    unwrap(
      await client.PUT("/api/v1/kyc/draft/documents/{kind}", {
        params: { path: { kind } },
        // The raw file is the body (no multipart): the browser streams the Blob as is.
        body: file as unknown as string,
        bodySerializer: (body: unknown) => body as BodyInit,
        headers: { "Content-Type": (file.type || "application/octet-stream") as UploadType },
      }),
    ),
  submit: async () => unwrap(await client.POST("/api/v1/kyc/submit")),
  limits: async () => unwrap(await client.GET("/api/v1/wallet/limits")),
  // ---- reviewer
  queue: async () => unwrap(await client.GET("/api/v1/admin/kyc/submissions")),
  submission: async (id: string) =>
    unwrap(
      await client.GET("/api/v1/admin/kyc/submissions/{submission_id}", {
        params: { path: { submission_id: id } },
      }),
    ),
  claim: async (id: string) =>
    unwrap(
      await client.POST("/api/v1/admin/kyc/submissions/{submission_id}/claim", {
        params: { path: { submission_id: id } },
      }),
    ),
  decide: async (
    id: string,
    decision: "APPROVE" | "REJECT",
    reasonCode: KycRejectReason | null,
    note: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/admin/kyc/submissions/{submission_id}/decision", {
        params: { path: { submission_id: id } },
        body: { decision, reason_code: reasonCode, note: note || null },
      }),
    ),
  // ---- risk desk
  holds: async () => unwrap(await client.GET("/api/v1/admin/risk/holds")),
  approveHold: async (reference: string, note: string) =>
    unwrap(
      await client.POST("/api/v1/admin/risk/holds/{reference}/approve", {
        params: { path: { reference } },
        body: { note: note || null },
      }),
    ),
  rejectHold: async (reference: string, note: string) =>
    unwrap(
      await client.POST("/api/v1/admin/risk/holds/{reference}/reject", {
        params: { path: { reference } },
        body: { note },
      }),
    ),
  alerts: async (status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED" | "FALSE_POSITIVE") =>
    unwrap(await client.GET("/api/v1/admin/risk/alerts", { params: { query: { status } } })),
  resolveAlert: async (
    id: string,
    outcome: "ACKNOWLEDGED" | "RESOLVED" | "FALSE_POSITIVE",
    note: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/admin/risk/alerts/{alert_id}/resolve", {
        params: { path: { alert_id: id } },
        body: { outcome, note },
      }),
    ),
  rules: async () => unwrap(await client.GET("/api/v1/admin/risk/rules")),
});

export function useKycApi() {
  return kycApi(useApiClient());
}

export function useKyc() {
  const api = useKycApi();
  return useQuery({ queryKey: kycKeys.mine, queryFn: api.status });
}

export function useLimits() {
  const api = useKycApi();
  return useQuery({ queryKey: kycKeys.limits, queryFn: api.limits });
}

export function useKycQueue() {
  const api = useKycApi();
  return useQuery({ queryKey: kycKeys.queue, queryFn: api.queue });
}

export function useKycSubmission(id: string | null) {
  const api = useKycApi();
  return useQuery({
    queryKey: kycKeys.submission(id ?? ""),
    queryFn: () => api.submission(id ?? ""),
    enabled: id !== null,
  });
}

export function useRiskHolds() {
  const api = useKycApi();
  return useQuery({ queryKey: kycKeys.holds, queryFn: api.holds });
}

export function useRiskAlerts(status: "OPEN" | "ACKNOWLEDGED" | "RESOLVED" | "FALSE_POSITIVE") {
  const api = useKycApi();
  return useQuery({ queryKey: kycKeys.alerts(status), queryFn: () => api.alerts(status) });
}
