/**
 * Typed calls for identity endpoints (customer and staff). Components use these through
 * TanStack Query; they never build URLs or headers themselves.
 */
import { useQuery } from "@tanstack/react-query";

import { ApiError, type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type Me = Schemas["MeResponse"];
export type StaffMe = Schemas["StaffMeResponse"];
export type SessionInfo = Schemas["SessionResponse"];
export type LoginHistoryItem = Schemas["LoginHistoryItem"];
export type AuditLogPage = Schemas["AuditLogPage"];
export type StaffNextStep = Schemas["StaffLoginResponse"]["next_step"];

export const identityKeys = {
  me: ["identity", "me"] as const,
  staffMe: ["identity", "staff-me"] as const,
  sessions: ["identity", "sessions"] as const,
  loginHistory: ["identity", "login-history"] as const,
  audit: (page: number, action: string) => ["admin", "audit", page, action] as const,
};

/** Returns null when nobody is signed in (401), instead of throwing. */
async function orNullOn401<T>(call: () => Promise<T>): Promise<T | null> {
  try {
    return await call();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export const identityApi = (client: ApiClient) => ({
  // --- customer -------------------------------------------------------------------------
  me: () => orNullOn401(async () => unwrap(await client.GET("/api/v1/me"))),
  register: async (body: Schemas["RegisterRequest"]) =>
    unwrap(await client.POST("/api/v1/auth/register", { body })),
  verifyEmail: async (body: Schemas["VerifyEmailRequest"]) =>
    unwrap(await client.POST("/api/v1/auth/verify-email", { body })),
  resendVerification: async (email: string) =>
    unwrap(await client.POST("/api/v1/auth/verify-email/resend", { body: { email } })),
  login: async (body: Schemas["LoginRequest"]) =>
    unwrap(await client.POST("/api/v1/auth/login", { body })),
  logout: async () => unwrap(await client.POST("/api/v1/auth/logout")),
  logoutAll: async () => unwrap(await client.POST("/api/v1/auth/logout-all")),
  forgotPassword: async (email: string) =>
    unwrap(await client.POST("/api/v1/auth/password/forgot", { body: { email } })),
  resetPassword: async (body: Schemas["ResetPasswordRequest"]) =>
    unwrap(await client.POST("/api/v1/auth/password/reset", { body })),
  updateProfile: async (body: Schemas["UpdateProfileRequest"]) =>
    unwrap(await client.PATCH("/api/v1/me", { body })),
  changePassword: async (body: Schemas["ChangePasswordRequest"]) =>
    unwrap(await client.POST("/api/v1/me/password", { body })),
  sessions: async () => unwrap(await client.GET("/api/v1/me/sessions")),
  revokeSession: async (id: string) =>
    unwrap(
      await client.DELETE("/api/v1/me/sessions/{session_id}", {
        params: { path: { session_id: id } },
      }),
    ),
  loginHistory: async () =>
    unwrap(await client.GET("/api/v1/me/login-history", { params: { query: { limit: 10 } } })),
  setPin: async (body: Schemas["SetPinRequest"]) =>
    unwrap(await client.POST("/api/v1/me/pin", { body })),
  changePin: async (body: Schemas["ChangePinRequest"]) =>
    unwrap(await client.PUT("/api/v1/me/pin", { body })),
  requestPinReset: async (password: string) =>
    unwrap(await client.POST("/api/v1/me/pin/reset/request", { body: { password } })),
  confirmPinReset: async (body: Schemas["ConfirmPinResetRequest"]) =>
    unwrap(await client.POST("/api/v1/me/pin/reset/confirm", { body })),

  // --- staff ----------------------------------------------------------------------------
  staffMe: () => orNullOn401(async () => unwrap(await client.GET("/api/v1/admin/me"))),
  staffLogin: async (body: Schemas["LoginRequest"]) =>
    unwrap(await client.POST("/api/v1/admin/auth/login", { body })),
  mfaEnroll: async () => unwrap(await client.POST("/api/v1/admin/auth/mfa/enroll")),
  mfaConfirm: async (code: string) =>
    unwrap(await client.POST("/api/v1/admin/auth/mfa/confirm", { body: { code } })),
  mfaVerify: async (body: Schemas["MfaVerifyRequest"]) =>
    unwrap(await client.POST("/api/v1/admin/auth/mfa/verify", { body })),
  stepUp: async (code: string) =>
    unwrap(await client.POST("/api/v1/admin/auth/step-up", { body: { code } })),
  staffLogout: async () => unwrap(await client.POST("/api/v1/admin/auth/logout")),
  auditLogs: async (page: number, action: string) =>
    unwrap(
      await client.GET("/api/v1/admin/audit-logs", {
        params: { query: { page, page_size: 25, ...(action ? { action } : {}) } },
      }),
    ),
  verifyAudit: async () => unwrap(await client.GET("/api/v1/admin/audit-logs/verify")),
});

export function useIdentityApi() {
  return identityApi(useApiClient());
}

export function useMe() {
  const api = useIdentityApi();
  return useQuery({ queryKey: identityKeys.me, queryFn: api.me, staleTime: 30_000, retry: false });
}

export function useStaffMe() {
  const api = useIdentityApi();
  return useQuery({
    queryKey: identityKeys.staffMe,
    queryFn: api.staffMe,
    staleTime: 30_000,
    retry: false,
  });
}
