import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createAdminI18n } from "../i18n";
import en from "../i18n/en.json";
import vi from "../i18n/vi.json";
import { Providers } from "./providers";
import { createQueryClient } from "./queryClient";
import { routes } from "./router";

const status = {
  status: "ok",
  version: "0.1.0",
  environment: "development",
  server_time: "2026-11-21T08:00:00Z",
  checks: { database: "ok", migrations: "ok", redis: "ok" },
};

const staff = {
  id: "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12",
  email: "ops@example.com",
  full_name: "Nhân Viên Demo",
  locale: "vi",
  roles: ["SUPER_ADMIN"],
  permissions: ["audit:read:any", "audit:verify:any"],
  mfa_verified_at: "2026-11-21T08:00:00Z",
  session_expires_at: "2026-11-21T08:30:00Z",
};

const auditPage = {
  items: [
    {
      id: "a1",
      seq: 7,
      at: "2026-11-21T08:00:00Z",
      actor_type: "STAFF",
      actor_id: staff.id,
      action: "identity.login",
      target_type: "user",
      target_id: staff.id,
      result: "SUCCESS",
      ip: "127.0.0.1",
      correlation_id: "c1",
      details: null,
    },
  ],
  page: 1,
  page_size: 25,
  total: 1,
};

function api(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/system/status": json(status),
    "GET /api/v1/admin/me": json(staff),
    ...extra,
  });
}

function renderAdmin(mock: MockFetch, path = "/") {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: mock.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers i18n={createAdminI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

describe("admin app", () => {
  it("shows service health, environment badge and permission-driven navigation", async () => {
    renderAdmin(api());
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.overview.title }),
    ).toBeInTheDocument();
    expect(await screen.findByText(vi.status.overallOk)).toBeInTheDocument();
    expect(screen.getByText(`${vi.app.environment}: DEVELOPMENT`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: vi.nav.audit })).toBeInTheDocument();
  });

  it("hides the audit link and page without permission", async () => {
    renderAdmin(
      api({ "GET /api/v1/admin/me": json({ ...staff, roles: ["SUPPORT"], permissions: [] }) }),
      "/audit",
    );
    expect(await screen.findByText(vi.auth.noPermission)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: vi.nav.audit })).not.toBeInTheDocument();
  });

  it("redirects to login without a session", async () => {
    const router = renderAdmin(api({ "GET /api/v1/admin/me": problem(401, "AUTH_REQUIRED") }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.auth.login.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("routes a pending MFA session to the verification step", async () => {
    const router = renderAdmin(api({ "GET /api/v1/admin/me": problem(403, "MFA_REQUIRED") }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.mfa.verify.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/mfa/verify");
  });

  it("login leads to MFA enrollment, shows recovery codes once, then the app", async () => {
    const mock = api({
      "POST /api/v1/admin/auth/login": json({ next_step: "MFA_ENROLLMENT_REQUIRED" }),
      "POST /api/v1/admin/auth/mfa/enroll": json({
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri:
          "otpauth://totp/VieWallet:ops@example.com?secret=JBSWY3DPEHPK3PXP&issuer=VieWallet",
      }),
      "POST /api/v1/admin/auth/mfa/confirm": json({
        recovery_codes: ["AAAAA-BBBBB", "CCCCC-DDDDD"],
      }),
    });
    const router = renderAdmin(mock, "/login");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.email), "ops@example.com");
    await userEvent.type(screen.getByLabelText(vi.auth.fields.password), "mây-trắng-bay-2026");
    await userEvent.click(screen.getByRole("button", { name: vi.auth.login.submit }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.mfa.setup.title }),
    ).toBeInTheDocument();
    expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: vi.mfa.setup.qrAlt })).toHaveAttribute(
      "src",
      expect.stringMatching(/^data:image\/png/),
    );

    await userEvent.type(screen.getByLabelText(vi.mfa.code), "123456");
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.setup.submit }));
    expect(await screen.findByText("AAAAA-BBBBB")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.recovery.done }));
    expect(
      await screen.findByRole("heading", { level: 1, name: vi.overview.title }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
    expect(sessionStorage.getItem("vw.admin.nextStep")).toBeNull();
  });

  it("verifies with a recovery code", async () => {
    const mock = api({ "POST /api/v1/admin/auth/mfa/verify": json(null, 204) });
    renderAdmin(mock, "/mfa/verify");
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.verify.useRecovery }));
    await userEvent.type(screen.getByLabelText(vi.mfa.recoveryCode), "AAAAA-BBBBB");
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.verify.submit }));
    await screen.findByRole("heading", { level: 1, name: vi.overview.title });
    expect(mock.calls.find((c) => c.path === "/api/v1/admin/auth/mfa/verify")?.body).toEqual({
      recovery_code: "AAAAA-BBBBB",
    });
  });

  it("lists audit entries and asks for step-up before verifying the chain", async () => {
    let verified = false;
    const mock = api({
      "GET /api/v1/admin/audit-logs": json(auditPage),
      "GET /api/v1/admin/audit-logs/verify": () =>
        verified
          ? json({ ok: true, checked: 42, first_broken_seq: null })
          : problem(403, "MFA_STEP_UP_REQUIRED"),
      "POST /api/v1/admin/auth/step-up": () => {
        verified = true;
        return json(null, 204);
      },
    });
    renderAdmin(mock, "/audit");
    expect(await screen.findByText("identity.login")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.audit.verify }));
    const dialog = await screen.findByRole("dialog", { name: vi.mfa.stepUp.title });
    expect(dialog).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.mfa.code), "654321");
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.stepUp.submit }));
    expect(
      await screen.findByText(vi.audit.chainOk.replace("{{count}}", "42")),
    ).toBeInTheDocument();
  });

  it("renders not found for unknown routes", async () => {
    renderAdmin(api(), "/x");
    expect(await screen.findByText(vi.notFound.title)).toBeInTheDocument();
  });

  it("vi and en define the same keys", () => {
    const keys = (obj: object, prefix = ""): string[] =>
      Object.entries(obj).flatMap(([k, v]) =>
        typeof v === "object" && v !== null
          ? keys(v as object, `${prefix}${k}.`)
          : [`${prefix}${k}`],
      );
    expect(keys(en).sort()).toEqual(keys(vi).sort());
  });
});
