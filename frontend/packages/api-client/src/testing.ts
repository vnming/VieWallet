/**
 * Test helper: a fetch implementation that routes "METHOD /path" to canned responses.
 * Unknown routes answer 404 Problem Details so missing mocks fail loudly in assertions.
 */
export type MockHandler = (request: Request) => Response | Promise<Response>;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      "content-type": status >= 400 ? "application/problem+json" : "application/json",
      ...headers,
    },
  });
}

export function problem(status: number, code: string, title = code, extra: object = {}): Response {
  return json(
    { type: "about:blank", title, status, code, correlation_id: "test-corr-id", ...extra },
    status,
  );
}

export interface MockFetch {
  fetch: typeof fetch;
  calls: { method: string; path: string; body: unknown; headers: Headers }[];
  on: (route: string, handler: MockHandler | Response) => void;
}

/** JSON bodies are parsed; anything else (file uploads) is kept as text. */
function parseBody(text: string, contentType: string | null): unknown {
  return contentType?.includes("json") ? (JSON.parse(text) as unknown) : text;
}

export function mockFetch(routes: Record<string, MockHandler | Response> = {}): MockFetch {
  const table = new Map(Object.entries(routes));
  const calls: MockFetch["calls"] = [];
  const impl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    const text = request.method === "GET" ? "" : await request.clone().text();
    calls.push({
      method: request.method,
      path: url.pathname,
      body: text ? parseBody(text, request.headers.get("content-type")) : undefined,
      headers: request.headers,
    });
    const handler = table.get(`${request.method} ${url.pathname}`);
    if (!handler) return problem(404, "NOT_FOUND", `no mock for ${request.method} ${url.pathname}`);
    return handler instanceof Response ? handler.clone() : handler(request);
  };
  return {
    fetch: impl,
    calls,
    on: (route, handler) => table.set(route, handler),
  };
}
