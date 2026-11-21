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
  });
  const locale: Middleware = {
    onRequest({ request }) {
      if (getLocale) request.headers.set("Accept-Language", getLocale());
      return request;
    },
  };
  client.use(locale);
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
