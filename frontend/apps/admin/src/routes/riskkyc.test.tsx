import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { Providers } from "../app/providers";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/router";
import { createAdminI18n } from "../i18n";
import vi from "../i18n/vi.json";

const ME = "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12";

function staff(permissions: string[]) {
  return {
    id: ME,
    email: "risk@example.com",
    full_name: "Nhân Viên Rủi Ro",
    locale: "vi",
    roles: ["RISK_ANALYST", "KYC_REVIEWER"],
    permissions,
    mfa_verified_at: "2026-11-21T08:00:00Z",
    session_expires_at: "2026-11-21T08:30:00Z",
  };
}

const submission = {
  id: "s1",
  user_id: "u1",
  status: "PENDING",
  full_name: "Trần Thị Lan",
  date_of_birth: "1995-04-12",
  document_type: "NATIONAL_ID",
  document_number_masked: "••••••••1234",
  address: "12 Đường Hư Cấu",
  submitted_at: "2026-11-23T03:00:00Z",
  claimed_by: null,
  claimed_at: null,
  decided_at: null,
};

function detail(overrides: object = {}) {
  return {
    ...submission,
    documents: [
      {
        id: "d1",
        kind: "FRONT",
        content_type: "image/png",
        size_bytes: 1200,
        uploaded_at: "2026-11-23T03:00:00Z",
        url: "/api/v1/admin/kyc/documents/d1/content?exp=1&sig=abc",
      },
    ],
    reviews: [],
    duplicate_document_accounts: 1,
    can_decide: false,
    ...overrides,
  };
}

function api(permissions: string[], extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/system/status": json({
      status: "ok",
      version: "0.1.0",
      environment: "development",
      server_time: "2026-11-21T08:00:00Z",
      checks: { database: "ok", migrations: "ok", redis: "ok" },
    }),
    "GET /api/v1/admin/me": json(staff(permissions)),
    ...extra,
  });
}

function renderAdmin(mock: MockFetch, path: string) {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: mock.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers i18n={createAdminI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
}

describe("KYC review", () => {
  it("claims, shows documents and approves", async () => {
    const mock = api(["kyc:review:any"], {
      "GET /api/v1/admin/kyc/submissions": json({
        items: [submission],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      "GET /api/v1/admin/kyc/submissions/s1": json(detail()),
      "POST /api/v1/admin/kyc/submissions/s1/claim": json(
        detail({ status: "IN_REVIEW", can_decide: true }),
      ),
      "POST /api/v1/admin/kyc/submissions/s1/decision": json(
        detail({ status: "APPROVED", can_decide: false }),
      ),
    });
    renderAdmin(mock, "/kyc");
    await userEvent.click(await screen.findByRole("button", { name: /Trần Thị Lan/ }));
    expect(await screen.findByText(vi.kyc.duplicate)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: vi.kyc.kind.FRONT })).toHaveAttribute(
      "src",
      "/api/v1/admin/kyc/documents/d1/content?exp=1&sig=abc",
    );
    expect(screen.queryByRole("button", { name: vi.kyc.approve })).not.toBeInTheDocument();
    mock.on(
      "GET /api/v1/admin/kyc/submissions/s1",
      json(detail({ status: "IN_REVIEW", can_decide: true })),
    );
    await userEvent.click(screen.getByRole("button", { name: vi.kyc.claim }));
    await userEvent.click(await screen.findByRole("button", { name: vi.kyc.approve }));
    expect(await screen.findByText(vi.kyc.approvedDone)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({
      decision: "APPROVE",
      reason_code: null,
      note: null,
    });
  });

  it("rejects with a reason", async () => {
    const mock = api(["kyc:review:any"], {
      "GET /api/v1/admin/kyc/submissions": json({
        items: [{ ...submission, status: "IN_REVIEW" }],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      "GET /api/v1/admin/kyc/submissions/s1": json(
        detail({ status: "IN_REVIEW", can_decide: true }),
      ),
      "POST /api/v1/admin/kyc/submissions/s1/decision": json(detail({ status: "REJECTED" })),
    });
    renderAdmin(mock, "/kyc");
    await userEvent.click(await screen.findByRole("button", { name: /Trần Thị Lan/ }));
    await userEvent.selectOptions(await screen.findByLabelText(vi.kyc.rejectReason), "MISMATCH");
    await userEvent.type(screen.getByLabelText(vi.kyc.note), "tên không khớp");
    await userEvent.click(screen.getByRole("button", { name: vi.kyc.reject }));
    expect(await screen.findByText(vi.kyc.rejectedDone)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({
      decision: "REJECT",
      reason_code: "MISMATCH",
      note: "tên không khớp",
    });
  });

  it("is hidden without the permission", async () => {
    renderAdmin(api(["user:read:any"]), "/kyc");
    const nav = (await screen.findAllByRole("navigation", { name: vi.nav.label }))[0]!;
    expect(within(nav).queryByRole("link", { name: vi.nav.kyc })).not.toBeInTheDocument();
  });
});

describe("risk desk", () => {
  const hold = {
    reference: "VW261123ABCDEFGH01",
    type: "TRANSFER",
    status: "PENDING",
    amount: "6000000",
    fee: "0",
    held: "6000000",
    currency: "VND",
    source_wallet_id: "w1",
    destination_wallet_id: "w2",
    counterparty: { kind: "USER", name: "NGUYEN V*** A" },
    confirmed_at: "2026-11-23T03:00:00Z",
    created_at: "2026-11-23T03:00:00Z",
  };

  it("approves a hold after a TOTP step-up and lists alerts", async () => {
    const mock = api(["risk_hold:decide:any", "risk_alert:manage:any"], {
      "GET /api/v1/admin/risk/holds": json([hold]),
      "GET /api/v1/admin/risk/alerts": json({
        items: [
          {
            id: "a1",
            user_id: "u2",
            transaction_id: null,
            type: "FAN_IN",
            severity: "MEDIUM",
            detail: { distinct_senders_1h: "5" },
            status: "OPEN",
            assigned_to: null,
            resolution_note: null,
            created_at: "2026-11-23T03:00:00Z",
            resolved_at: null,
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      "POST /api/v1/admin/risk/holds/VW261123ABCDEFGH01/approve": problem(
        403,
        "MFA_STEP_UP_REQUIRED",
      ),
      "POST /api/v1/admin/auth/step-up": json(null, 204),
    });
    renderAdmin(mock, "/risk");
    expect(await screen.findByText(vi.risk.alertTypes.FAN_IN)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: vi.risk.approve }));
    await userEvent.click(screen.getByRole("button", { name: vi.common.confirm }));
    const code = await screen.findByLabelText(vi.mfa.code); // 403 step-up: TOTP dialog
    mock.on(
      "POST /api/v1/admin/risk/holds/VW261123ABCDEFGH01/approve",
      json({ ...hold, status: "COMPLETED" }),
    );
    await userEvent.type(code, "123456");
    await userEvent.click(screen.getByRole("button", { name: vi.mfa.stepUp.submit }));
    expect(await screen.findByText(vi.risk.approvedDone)).toBeInTheDocument();
  });

  it("needs a note to reject", async () => {
    renderAdmin(
      api(["risk_hold:decide:any", "risk_alert:manage:any"], {
        "GET /api/v1/admin/risk/holds": json([hold]),
        "GET /api/v1/admin/risk/alerts": json({ items: [], page: 1, page_size: 20, total: 0 }),
      }),
      "/risk",
    );
    expect(await screen.findByText(vi.risk.noAlerts)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: vi.risk.reject }));
    expect(screen.getByRole("button", { name: vi.common.confirm })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(vi.risk.note), "nghi gian lận");
    expect(screen.getByRole("button", { name: vi.common.confirm })).toBeEnabled();
  });
});
