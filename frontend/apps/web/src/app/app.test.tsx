import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createWebI18n } from "../i18n";
import en from "../i18n/en.json";
import vi from "../i18n/vi.json";
import { Providers } from "./providers";
import { createQueryClient } from "./queryClient";
import { routes } from "./router";

const okStatus = {
  status: "ok",
  version: "0.1.0",
  environment: "development",
  server_time: "2026-11-21T08:00:00Z",
  checks: { database: "ok", migrations: "ok", redis: "ok" },
};

const me = {
  id: "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12",
  email: "lan@example.com",
  full_name: "Trần Thị Lan",
  display_name: null,
  locale: "vi",
  user_type: "CUSTOMER",
  email_verified: true,
  created_at: "2026-11-21T08:00:00Z",
  permissions: ["profile:manage:own"],
  pin: { is_set: true, locked_until: null, requires_reset: false },
};

function signedOut(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/system/status": json(okStatus),
    "GET /api/v1/me": problem(401, "AUTH_REQUIRED"),
    ...extra,
  });
}

function renderApp(api: MockFetch, path = "/") {
  const i18n = createWebI18n("vi");
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: api.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers i18n={i18n} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, queryClient };
}

describe("web app shell", () => {
  it("shows the live system status and sign-up calls to action when signed out", async () => {
    renderApp(signedOut());
    expect(screen.getByRole("heading", { level: 1, name: vi.home.title })).toBeInTheDocument();
    expect(await screen.findByText(vi.status.overallOk)).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getAllByText(vi.status.ok)).toHaveLength(3);
    expect(await screen.findByRole("link", { name: vi.auth.register.cta })).toHaveAttribute(
      "href",
      "/register",
    );
  });

  it("shows failing checks", async () => {
    renderApp(
      signedOut({
        "GET /api/v1/system/status": json({
          ...okStatus,
          status: "fail",
          checks: { ...okStatus.checks, redis: "fail" },
        }),
      }),
    );
    expect(await screen.findByText(vi.status.overallFail)).toBeInTheDocument();
  });

  it("shows an error state with retry when the API is unreachable", async () => {
    let calls = 0;
    const api = signedOut({
      "GET /api/v1/system/status": () => {
        calls += 1;
        if (calls === 1) throw new TypeError("Failed to fetch");
        return json(okStatus);
      },
    });
    renderApp(api);
    expect(await screen.findByText(vi.status.errorTitle)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.status.retry }));
    expect(await screen.findByText(vi.status.overallOk)).toBeInTheDocument();
  });

  it("switches language and remembers the choice", async () => {
    renderApp(signedOut());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: vi.language.label }), "en");
    expect(
      await screen.findByRole("heading", { level: 1, name: en.home.title }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("vw.locale")).toBe("en");
  });

  it("has labelled navigation landmarks and a skip link", () => {
    renderApp(signedOut());
    expect(screen.getAllByRole("navigation", { name: vi.nav.label }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: vi.app.skipToContent })).toHaveAttribute(
      "href",
      "#main",
    );
  });

  it("renders a not-found page and the dev style guide", async () => {
    renderApp(signedOut(), "/khong-ton-tai");
    expect(screen.getByText(vi.notFound.title)).toBeInTheDocument();
  });
});

describe("authentication flows", () => {
  it("redirects signed-out users from protected pages to login with a next parameter", async () => {
    const { router } = renderApp(signedOut(), "/account/security");
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.auth.login.title }),
    ).toBeInTheDocument();
    expect(router.state.location.search).toBe("?next=%2Faccount%2Fsecurity");
  });

  it("signs in, sends the CSRF header and follows a safe next path", async () => {
    const api = signedOut({ "POST /api/v1/auth/login": json(me) });
    api.on("GET /api/v1/me/sessions", json([]));
    api.on("GET /api/v1/me/login-history", json([]));
    const { router } = renderApp(api, "/login?next=%2Faccount%2Fsecurity");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "lan@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "mây-trắng-bay-2026");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.login.submit }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.security.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/account/security");
    const login = api.calls.find((c) => c.path === "/api/v1/auth/login");
    expect(login?.headers.get("X-VW-CSRF")).toBe("1");
    expect(login?.body).toEqual({ email: "lan@example.com", password: "mây-trắng-bay-2026" });
  });

  it("refuses open redirects after login", async () => {
    const api = signedOut({ "POST /api/v1/auth/login": json(me) });
    const { router } = renderApp(api, "/login?next=%2F%2Fevil.example");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "lan@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "x");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.login.submit }));
    await screen.findByRole("heading", { level: 1, name: "Xin chào, Trần Thị Lan" });
    expect(router.state.location.pathname).toBe("/");
  });

  it("sends new users without a PIN to the PIN setup step", async () => {
    const noPin = { ...me, pin: { is_set: false, locked_until: null, requires_reset: false } };
    const { router } = renderApp(signedOut({ "GET /api/v1/me": json(noPin) }), "/account/security");
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.pin.setup.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/onboarding/pin");
  });

  it("shows the localized API error for wrong credentials", async () => {
    const api = signedOut({
      "POST /api/v1/auth/login": problem(
        401,
        "INVALID_CREDENTIALS",
        "Email hoặc mật khẩu không đúng",
      ),
    });
    renderApp(api, "/login");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "lan@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "sai");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.login.submit }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email hoặc mật khẩu không đúng");
  });

  it("offers e-mail verification when the account is not verified", async () => {
    const api = signedOut({ "POST /api/v1/auth/login": problem(403, "EMAIL_NOT_VERIFIED") });
    renderApp(api, "/login");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "lan@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "x");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.login.submit }));
    expect(await screen.findByRole("link", { name: vi.auth.login.verifyNow })).toHaveAttribute(
      "href",
      "/verify-email?email=lan%40example.com",
    );
  });

  it("registers and moves to verification; field errors show on the password", async () => {
    const api = signedOut({
      "POST /api/v1/auth/register": problem(422, "PASSWORD_POLICY", "Mật khẩu chưa đủ an toàn", {
        errors: [{ field: "password", code: "TOO_COMMON", message: "too common" }],
      }),
    });
    const { router } = renderApp(api, "/register");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.fullName), "Trần Thị Lan");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "lan@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "password123");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.register.submit }));
    expect(await screen.findByText(vi.auth.fields.passwordWeak)).toBeInTheDocument();

    api.on("POST /api/v1/auth/register", json({ status: "accepted" }, 202));
    await userEvent.click(screen.getByRole("button", { name: vi.auth.register.submit }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.auth.verify.title }),
    ).toBeInTheDocument();
    expect(router.state.location.search).toBe("?email=lan%40example.com");
  });

  it("verifies the e-mail code and returns to login", async () => {
    const api = signedOut({ "POST /api/v1/auth/verify-email": json(null, 204) });
    const { router } = renderApp(api, "/verify-email?email=lan%40example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.verify.code), "12a3456");
    expect(screen.getByLabelText(vi.auth.verify.code)).toHaveValue("123456"); // digits only
    await userEvent.click(screen.getByRole("button", { name: vi.auth.verify.submit }));
    expect(await screen.findByText(vi.auth.verify.done)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("forgot password always shows the same confirmation", async () => {
    const api = signedOut({
      "POST /api/v1/auth/password/forgot": json({ status: "accepted" }, 202),
    });
    renderApp(api, "/forgot-password");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "ghost@example.com");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.forgot.submit }));
    expect(await screen.findByText(vi.auth.forgot.sent)).toBeInTheDocument();
  });

  it("sets the PIN only when both entries match", async () => {
    const noPin = { ...me, pin: { is_set: false, locked_until: null, requires_reset: false } };
    const api = signedOut({
      "GET /api/v1/me": json(noPin),
      "POST /api/v1/me/pin": json(null, 204),
    });
    renderApp(api, "/onboarding/pin");
    const submit = await screen.findByRole("button", { name: vi.pin.setup.submit });
    await userEvent.type(screen.getByLabelText(vi.pin.new), "270419");
    await userEvent.type(screen.getByLabelText(vi.pin.confirm), "270418");
    expect(screen.getByText(vi.pin.mismatch)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText(vi.pin.confirm));
    await userEvent.type(screen.getByLabelText(vi.pin.confirm), "270419");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "mây-trắng-bay-2026");
    api.on("GET /api/v1/me", json(me));
    await userEvent.click(submit);
    await screen.findByRole("heading", { level: 1, name: "Xin chào, Trần Thị Lan" });
    expect(api.calls.find((c) => c.path === "/api/v1/me/pin")?.body).toEqual({
      password: "mây-trắng-bay-2026",
      pin: "270419",
    });
  });

  it("lists sessions on the security page and signs out other devices", async () => {
    const api = signedOut({
      "GET /api/v1/me": json(me),
      "GET /api/v1/me/sessions": json([
        {
          id: "s1",
          created_at: okStatus.server_time,
          last_seen_at: okStatus.server_time,
          ip: "127.0.0.1",
          user_agent: "Firefox",
          current: true,
        },
        {
          id: "s2",
          created_at: okStatus.server_time,
          last_seen_at: okStatus.server_time,
          ip: "10.0.0.2",
          user_agent: "Phone",
          current: false,
        },
      ]),
      "GET /api/v1/me/login-history": json([
        { at: okStatus.server_time, result: "SUCCESS", ip: "127.0.0.1", user_agent: "Firefox" },
      ]),
      "DELETE /api/v1/me/sessions/s2": json(null, 204),
    });
    renderApp(api, "/account/security");
    expect(await screen.findByText("Phone")).toBeInTheDocument();
    expect(screen.getByText(vi.security.sessions.current)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.security.sessions.revoke }));
    expect(
      api.calls.some((c) => c.method === "DELETE" && c.path === "/api/v1/me/sessions/s2"),
    ).toBe(true);
    expect(await screen.findByText(vi.security.history.results.SUCCESS)).toBeInTheDocument();
  });

  it("logs out, clears cached data and returns to login", async () => {
    const api = signedOut({ "GET /api/v1/me": json(me) });
    // The real API answers 401 once the session is revoked.
    api.on("POST /api/v1/auth/logout", () => {
      api.on("GET /api/v1/me", problem(401, "AUTH_REQUIRED"));
      return json(null, 204);
    });
    const { router, queryClient } = renderApp(api);
    await userEvent.click(await screen.findByRole("button", { name: vi.auth.logout }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.auth.login.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(queryClient.getQueryData(["identity", "me"]) ?? null).toBeNull();
    expect(screen.queryByText(me.full_name)).not.toBeInTheDocument();
  });
});

describe("i18n resources", () => {
  const keys = (obj: object, prefix = ""): string[] =>
    Object.entries(obj).flatMap(([k, v]) =>
      typeof v === "object" && v !== null ? keys(v as object, `${prefix}${k}.`) : [`${prefix}${k}`],
    );

  it("vi and en define the same keys", () => {
    expect(keys(en).sort()).toEqual(keys(vi).sort());
  });
});
