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
const OTHER = "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e99";

const staff = {
  id: ME,
  email: "finance@example.com",
  full_name: "Nhân Viên Tài Chính",
  locale: "vi",
  roles: ["FINANCE"],
  permissions: [
    "user:read:any",
    "adjustment:propose:any",
    "adjustment:approve:any",
    "reconciliation:manage:any",
    "wallet:freeze:any",
    "wallet:unfreeze:any",
  ],
  mfa_verified_at: "2026-11-21T08:00:00Z",
  session_expires_at: "2026-11-21T08:30:00Z",
};

function approval(overrides: object = {}) {
  return {
    id: "ap1",
    action_type: "ADJUSTMENT",
    target_type: "transaction",
    target_id: "t1",
    payload: {
      transaction_reference: "VW261122ABCDEFGH01",
      wallet_number_masked: "******6782",
      direction: "CREDIT",
      amount: "250000",
      currency: "VND",
      reason_code: "DEMO_FUNDING",
    },
    maker_id: OTHER,
    maker_reason: "Nạp tiền mô phỏng cho demo",
    status: "PENDING",
    checker_id: null,
    checker_note: null,
    failure_code: null,
    created_at: "2026-11-22T03:00:00Z",
    decided_at: null,
    executed_at: null,
    expires_at: "2026-11-25T03:00:00Z",
    can_decide: true,
    ...overrides,
  };
}

function api(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/system/status": json({
      status: "ok",
      version: "0.1.0",
      environment: "development",
      server_time: "2026-11-21T08:00:00Z",
      checks: { database: "ok", migrations: "ok", redis: "ok" },
    }),
    "GET /api/v1/admin/me": json(staff),
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

describe("finance operations", () => {
  it("shows finance navigation only for held permissions", async () => {
    renderAdmin(
      api({ "GET /api/v1/admin/approvals": json({ items: [], page: 1, page_size: 20, total: 0 }) }),
      "/approvals",
    );
    expect(await screen.findByText(vi.approvals.empty)).toBeInTheDocument();
    const nav = screen.getAllByRole("navigation", { name: vi.nav.label })[0]!;
    for (const key of ["wallets", "adjustments", "approvals", "reconciliation"] as const) {
      expect(within(nav).getByRole("link", { name: vi.nav[key] })).toBeInTheDocument();
    }
    expect(within(nav).queryByRole("link", { name: vi.nav.audit })).not.toBeInTheDocument();
  });

  it("proposes an adjustment with one idempotency key per intent", async () => {
    const mock = api({
      "POST /api/v1/admin/adjustments": problem(503, "SERVICE_UNAVAILABLE"),
    });
    renderAdmin(mock, "/adjustments?wallet=8123456782");
    const submit = await screen.findByRole("button", { name: vi.adjustments.submit });
    expect(screen.getByLabelText(vi.finance.walletNumber)).toHaveValue("8123456782");
    await userEvent.type(screen.getByLabelText(vi.finance.amount), "25a0000");
    expect(screen.getByLabelText(vi.finance.amount)).toHaveValue("250000"); // digits only
    expect(submit).toBeDisabled(); // the note is still missing
    await userEvent.type(screen.getByLabelText(vi.adjustments.note), "Nạp tiền mô phỏng cho demo");
    await userEvent.click(submit);
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    mock.on(
      "POST /api/v1/admin/adjustments",
      json(
        {
          approval: approval({ maker_id: ME, can_decide: false }),
          transaction_reference: "VW261122ABCDEFGH01",
        },
        201,
      ),
    );
    await userEvent.click(submit);
    expect(await screen.findByText(/VW261122ABCDEFGH01/)).toBeInTheDocument();
    const calls = mock.calls.filter((c) => c.path === "/api/v1/admin/adjustments");
    expect(calls).toHaveLength(2);
    const [first, second] = calls;
    expect(first?.headers.get("Idempotency-Key")).toBeTruthy();
    expect(second?.headers.get("Idempotency-Key")).toBe(first?.headers.get("Idempotency-Key"));
    expect(second?.body).toEqual({
      wallet_number: "8123456782",
      direction: "CREDIT",
      amount: "250000",
      reason_code: "DEMO_FUNDING",
      note: "Nạp tiền mô phỏng cho demo",
      description: "",
      ticket_ref: null,
    });
  });

  it("approves after a step-up and hides decisions on own requests", async () => {
    const mock = api({
      "GET /api/v1/admin/approvals": json({
        items: [approval(), approval({ id: "ap2", maker_id: ME, can_decide: false })],
        page: 1,
        page_size: 20,
        total: 2,
      }),
      "POST /api/v1/admin/approvals/ap1/approve": problem(403, "MFA_STEP_UP_REQUIRED"),
      "POST /api/v1/admin/auth/step-up": json(null, 204),
    });
    renderAdmin(mock, "/approvals");
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(2);
    const [other, mine] = cards as [HTMLElement, HTMLElement];
    expect(
      within(mine).queryByRole("button", { name: vi.approvals.approve }),
    ).not.toBeInTheDocument();
    expect(within(mine).getByRole("button", { name: vi.approvals.cancel })).toBeInTheDocument();
    expect(within(other).getByText(/250\.000/)).toBeInTheDocument();

    await userEvent.click(within(other).getByRole("button", { name: vi.approvals.approve }));
    const dialog = await screen.findByRole("dialog");
    mock.on("POST /api/v1/admin/approvals/ap1/approve", json(approval({ status: "EXECUTED" })));
    await userEvent.type(within(dialog).getByLabelText(vi.mfa.code), "123456");
    await userEvent.click(within(dialog).getByRole("button", { name: vi.mfa.stepUp.submit }));
    expect(await screen.findByText(vi.approvals.approved)).toBeInTheDocument();
    const approvals = mock.calls.filter((c) => c.path === "/api/v1/admin/approvals/ap1/approve");
    expect(approvals).toHaveLength(2); // retried after the fresh TOTP
  });

  it("rejects with a reason", async () => {
    const mock = api({
      "GET /api/v1/admin/approvals": json({
        items: [approval()],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      "POST /api/v1/admin/approvals/ap1/reject": json(approval({ status: "REJECTED" })),
    });
    renderAdmin(mock, "/approvals");
    await userEvent.click(await screen.findByRole("button", { name: vi.approvals.reject }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: vi.approvals.reject });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(vi.approvals.rejectNote), "không có ticket");
    await userEvent.click(confirm);
    expect(await screen.findByText(vi.approvals.rejected)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/reject"))?.body).toEqual({
      note: "không có ticket",
    });
  });

  it("looks up a wallet and freezes it with a reason", async () => {
    const wallet = {
      id: "w1",
      wallet_number: "8123456782",
      currency: "VND",
      status: "ACTIVE",
      freeze_source: null,
      frozen_at: null,
      balance: { amount: "1250000", currency: "VND" },
      owner_user_id: OTHER,
      status_reason: null,
      created_at: "2026-11-22T03:00:00Z",
    };
    const mock = api({
      "GET /api/v1/admin/wallets/8123456782": json(wallet),
      "GET /api/v1/admin/wallets/1111111111": problem(404, "WALLET_NOT_FOUND"),
      "POST /api/v1/admin/wallets/8123456782/freeze": json({ ...wallet, status: "FROZEN" }),
    });
    renderAdmin(mock, "/wallets");
    const input = await screen.findByLabelText(vi.finance.walletNumber);
    await userEvent.type(input, "1111111111");
    await userEvent.click(screen.getByRole("button", { name: vi.common.search }));
    expect(await screen.findByText(vi.wallets.notFound)).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "812 345 6782");
    await userEvent.click(screen.getByRole("button", { name: vi.common.search }));
    expect(await screen.findByText(/1\.250\.000/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.wallets.freeze }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(vi.wallets.reason), "nghi ngờ gian lận");
    await userEvent.click(within(dialog).getByRole("button", { name: vi.common.confirm }));
    expect(await screen.findByText(vi.wallets.frozenDone)).toBeInTheDocument();
  });

  it("runs reconciliation and lists runs", async () => {
    const run = {
      id: "r1",
      kind: "INTERNAL",
      status: "SUCCEEDED",
      violations: 0,
      stats: {},
      triggered_by: ME,
      started_at: "2026-11-22T03:00:00Z",
      finished_at: "2026-11-22T03:00:01Z",
    };
    const mock = api({
      "GET /api/v1/admin/reconciliation/runs": json({
        items: [],
        page: 1,
        page_size: 20,
        total: 0,
      }),
      "POST /api/v1/admin/reconciliation/runs": json({ ...run, items: [] }, 201),
    });
    renderAdmin(mock, "/reconciliation");
    expect(await screen.findByText(vi.reconciliation.empty)).toBeInTheDocument();
    mock.on(
      "GET /api/v1/admin/reconciliation/runs",
      json({ items: [run], page: 1, page_size: 20, total: 1 }),
    );
    await userEvent.click(screen.getByRole("button", { name: vi.reconciliation.run }));
    expect(await screen.findByText("Đối soát xong: 0 vi phạm")).toBeInTheDocument();
    expect(await screen.findByText(vi.reconciliation.clean)).toBeInTheDocument();
  });
});
