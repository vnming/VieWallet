import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type SystemStatus = Schemas["SystemStatus"];

/** RFC 9457 Problem Details returned by the API for every error (docs/api/api-spec.md §2). */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  correlation_id?: string | null;
  errors?: { field: string; code: string; message: string }[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly problem: ProblemDetails | null;

  constructor(status: number, problem: ProblemDetails | null, correlationId: string | null) {
    super(problem?.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem?.code ?? (status === 0 ? "NETWORK_ERROR" : "UNKNOWN_ERROR");
    this.correlationId = problem?.correlation_id ?? correlationId;
    this.problem = problem;
  }
}

function isProblem(value: unknown): value is ProblemDetails {
  return typeof value === "object" && value !== null && "code" in value && "status" in value;
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface ApiClientOptions {
  /** Empty string = same origin (the dev server and nginx proxy /api to the backend). */
  baseUrl?: string;
  /** Returns the current UI locale for Accept-Language (localized error titles). */
  getLocale?: () => string;
  fetch?: typeof globalThis.fetch;
}

export function createApiClient({ baseUrl = "", getLocale, fetch }: ApiClientOptions = {}) {
  const client = createClient<paths>({
    baseUrl,
    ...(fetch ? { fetch } : {}),
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  const middleware: Middleware = {
    onRequest({ request }) {
      if (getLocale) request.headers.set("Accept-Language", getLocale());
      // Cookie sessions need CSRF protection: the API rejects state-changing requests
      // without this header (ADR-0012). Cross-site pages cannot add it.
      if (UNSAFE_METHODS.has(request.method)) request.headers.set("X-VW-CSRF", "1");
      return request;
    },
  };
  client.use(middleware);
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Unwraps an openapi-fetch result: returns data or throws ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const problem = isProblem(result.error) ? result.error : null;
    throw new ApiError(
      result.response.status,
      problem,
      result.response.headers.get("x-request-id"),
    );
  }
  return result.data as T;
}

/** Field-level validation messages keyed by field name (first message per field). */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || !error.problem?.errors) return {};
  const result: Record<string, string> = {};
  for (const item of error.problem.errors) {
    result[item.field] ??= item.message;
  }
  return result;
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code);
}
