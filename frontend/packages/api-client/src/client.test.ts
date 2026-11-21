import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, fetchSystemStatus } from "./index";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const status = {
  status: "ok",
  version: "0.1.0",
  environment: "development",
  server_time: "2026-11-21T08:00:00Z",
  checks: { database: "ok", migrations: "ok", redis: "ok" },
};

describe("api client", () => {
  it("returns typed data and sends Accept-Language", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(status));
    const client = createApiClient({
      baseUrl: "http://api.test",
      getLocale: () => "en",
      fetch: fetchMock,
    });
    await expect(fetchSystemStatus(client)).resolves.toEqual(status);
    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toBe("http://api.test/api/v1/system/status");
    expect(request.headers.get("Accept-Language")).toBe("en");
  });

  it("throws ApiError with the problem code and correlation id", async () => {
    const problem = {
      type: "https://docs.viewallet.local/errors/service-unavailable",
      title: "Dịch vụ tạm thời không khả dụng",
      status: 503,
      code: "SERVICE_UNAVAILABLE",
      correlation_id: "abc123abc123",
    };
    const client = createApiClient({
      baseUrl: "http://api.test",
      fetch: async () => jsonResponse(problem, 503, { "content-type": "application/problem+json" }),
    });
    const error = await fetchSystemStatus(client).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      code: "SERVICE_UNAVAILABLE",
      correlationId: "abc123abc123",
    });
  });

  it("uses the X-Request-ID header when the body is not a problem document", async () => {
    const client = createApiClient({
      baseUrl: "http://api.test",
      fetch: async () =>
        new Response("bad gateway", { status: 502, headers: { "x-request-id": "rid-1" } }),
    });
    const error = (await fetchSystemStatus(client).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("UNKNOWN_ERROR");
    expect(error.correlationId).toBe("rid-1");
  });
});
