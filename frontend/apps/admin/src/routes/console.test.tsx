/**
 * Phase 8 admin console: dashboard, approval diffs, customer lookup with PII reveal, fee
 * versions, role changes, simulator panel, ticket desk, and an axe sweep of the new pages.
 */
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
import { seriousViolations } from "../test/a11y";

const ME = "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12";
const ALL = [
  "user:read:any",
  "user:pii_reveal:any",
  "transaction:read:any",
  "refund:propose:any",
  "refund:approve:any",
  "reversal:propose:any",
  "config:propose:any",
  "config:approve:any",
  "role:assign:any",
  "ticket:manage:any",
  "reconciliation:manage:any",
  "simulator:manage:any",
];

function staff(permissions: string[]) {
  return {
    id: ME,
    email: "admin@example.com",
    full_name: "Quản Trị Demo",
    locale: "vi",
    roles: ["SUPER_ADMIN"],
    permissions,
    mfa_verified_at: "2026-11-24T08:00:00Z",
    session_expires_at: "2026-11-24T08:30:00Z",
  };
}

const health = {
  checked_at: "2026-11-24T08:00:00Z",
  environment: "development",
  version: "0.1.0",
  components: {
    database: "UP",
    redis: "UP",
    worker: "UP",
    push: "NOT_CONFIGURED",
    ai: "NOT_CONFIGURED",
  },
  outbox: { pending: 0, failed: 0, oldest_pending_seconds: null },
  provider: {
    code: "SIMULATOR",
    status: "DOWN",
    mode: "OUTAGE",
    unknown_operations: 0,
    recent_unavailable_failures: 2,
  },
  jobs: [
    {
      name: "outbox_relay",
      last_status: "SUCCEEDED",
      last_finished_at: "2026-11-24T07:59:00Z",
      last_error: null,
    },
  ],
};

const dashboard = {
  generated_at: "2026-11-24T08:00:00Z",
  business_date: "2026-11-24",
  transactions: {
    completed_today: [{ type: "DEPOSIT", count: 3, amount: "750000" }],
    failed_today: 1,
    in_progress: 2,
  },
  users: { customers: 10, new_today: 2 },
  queues: {
    approvals: 4,
    risk_holds: null,
    risk_alerts: null,
    kyc_reviews: null,
    tickets: 1,
    reconciliation_items: 0,
    recovery_cases: 0,
  },
};

const feeApproval = {
  id: "ap1",
  action_type: "FEE_RULE_CHANGE",
  target_type: "fee_rule",
  target_id: "FEE_WITHDRAWAL_VND",
  payload: {
    rule_id: "r2",
    code: "FEE_WITHDRAWAL_VND",
    txn_type: "WITHDRAWAL",
    from_version: 1,
    to_version: 2,
    before: { fixed_minor: "1100", bps: "0", min_fee_minor: "0", max_fee_minor: null },
    after: { fixed_minor: "2100", bps: "0", min_fee_minor: "0", max_fee_minor: null },
  },
  maker_id: "someone-else",
  maker_reason: "Điều chỉnh phí rút tiền",
  status: "PENDING",
  checker_id: null,
  checker_note: null,
  failure_code: null,
  created_at: "2026-11-24T07:00:00Z",
  decided_at: null,
  executed_at: null,
  expires_at: "2026-11-27T07:00:00Z",
  can_decide: true,
};

const fee = {
  id: "r1",
  code: "FEE_WITHDRAWAL_VND",
  txn_type: "WITHDRAWAL",
  currency: "VND",
  scope: "DEFAULT",
  scope_value: null,
  fixed: "1100",
  bps: 0,
  min_fee: "0",
  max_fee: null,
  version: 1,
  status: "ACTIVE",
  effective_from: "2026-11-21T00:00:00Z",
};

const userSummary = {
  id: "u1",
  email: "l***@example.com",
  name: "Tran T*** L",
  phone: "+84*****678",
  user_type: "CUSTOMER",
  status: "ACTIVE",
  kyc_tier: 1,
  wallet_number: "******7897",
  created_at: "2026-11-21T08:00:00Z",
};

const ticket = {
  reference: "TK261124ABCD",
  category: "TRANSACTION",
  subject: "Nạp tiền chưa vào ví",
  status: "OPEN",
  transaction_reference: "VW261124AAAA",
  created_at: "2026-11-24T07:00:00Z",
  updated_at: "2026-11-24T07:00:00Z",
  user_id: "u1",
  assigned_to: null,
};

function api(permissions: string[] = ALL, extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/system/status": json({
      status: "ok",
      version: "0.1.0",
      environment: "development",
      server_time: "2026-11-24T08:00:00Z",
      checks: { database: "ok", migrations: "ok", redis: "ok" },
    }),
    "GET /api/v1/admin/me": json(staff(permissions)),
    "GET /api/v1/admin/dashboard": json(dashboard),
    "GET /api/v1/admin/system-health": json(health),
    "GET /api/v1/admin/approvals": json({ items: [feeApproval], page: 1, page_size: 20, total: 1 }),
    "GET /api/v1/admin/users": json([userSummary]),
    "GET /api/v1/admin/users/u1": json({
      ...userSummary,
      roles: ["CUSTOMER"],
      email_verified: true,
      last_login_at: null,
      kyc_status: "APPROVED",
      wallet: {
        wallet_number: "******7897",
        status: "ACTIVE",
        freeze_source: null,
        balance: "1250000",
        currency: "VND",
      },
      risk_flags: [],
      recent_transactions: [],
    }),
    "POST /api/v1/admin/users/u1/pii-reveals": json({
      email: "lan@example.com",
      full_name: "Trần Thị Lan",
      phone: "+84912345678",
    }),
    "GET /api/v1/admin/config/fees": json([fee]),
    "GET /api/v1/admin/config/limits": json([]),
    "POST /api/v1/admin/config/fees/FEE_WITHDRAWAL_VND/versions": json(feeApproval, 201),
    "GET /api/v1/admin/roles": json({
      roles: [
        { code: "SUPPORT", name: "Hỗ trợ", is_staff: true, permissions: ["ticket:manage:any"] },
        {
          code: "KYC_REVIEWER",
          name: "Duyệt KYC",
          is_staff: true,
          permissions: ["kyc:review:any"],
        },
      ],
      permissions: [],
    }),
    "GET /api/v1/admin/staff": json([
      {
        id: "s2",
        email: "ops@example.com",
        full_name: "Vận Hành",
        status: "ACTIVE",
        roles: ["SUPPORT"],
        last_login_at: null,
      },
    ]),
    "POST /api/v1/admin/staff/s2/role-changes": json(
      { ...feeApproval, action_type: "ROLE_CHANGE" },
      201,
    ),
    "GET /api/v1/admin/simulator": json({ mode: "NORMAL", operations: [] }),
    "PUT /api/v1/admin/simulator/mode": json({ mode: "OUTAGE", operations: [] }),
    "GET /api/v1/admin/operations-queue": json({
      stuck_transactions: [],
      unknown_provider_operations: [],
      recovery_cases: [],
      pending_money_approvals: [],
    }),
    "GET /api/v1/admin/tickets": json({ items: [ticket], page: 1, page_size: 50, total: 1 }),
    "GET /api/v1/admin/tickets/TK261124ABCD": json({
      ...ticket,
      messages: [
        {
          id: "m1",
          author: "CUSTOMER",
          body: "Tôi đã nạp nhưng chưa thấy tiền",
          internal: false,
          created_at: "2026-11-24T07:00:00Z",
        },
      ],
    }),
    "POST /api/v1/admin/tickets/TK261124ABCD/messages": json({
      ...ticket,
      status: "PENDING_CUSTOMER",
      messages: [],
    }),
    "GET /api/v1/admin/reconciliation/runs": json({ items: [], page: 1, page_size: 20, total: 0 }),
    "GET /api/v1/admin/reconciliation/items": json([]),
    ...extra,
  });
}

function renderAdmin(mock: MockFetch, path: string) {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: mock.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <Providers i18n={createAdminI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
}

describe("admin console", () => {
  it("shows backend KPIs, queues the staff member can work and a red provider", async () => {
    renderAdmin(api(), "/");
    expect(await screen.findByText(vi.dashboard.queue.approvals)).toBeInTheDocument();
    expect(screen.queryByText(vi.dashboard.queue.risk_holds)).not.toBeInTheDocument();
    expect(screen.getByText(vi.dashboard.queue.tickets).closest("a")).toHaveAttribute(
      "href",
      "/tickets",
    );
    expect(screen.getByText("10 (+2)")).toBeInTheDocument();
    expect(
      await screen.findByText(`${vi.health.provider}: ${vi.health.status.DOWN}`),
    ).toBeInTheDocument();
  });

  it("shows a before/after diff for a fee change", async () => {
    renderAdmin(api(), "/approvals");
    const table = await screen.findByRole("table", { name: vi.approvals.diff });
    const row = within(table).getByRole("row", { name: /fixed_minor/ });
    expect(row).toHaveTextContent("1100");
    expect(row).toHaveTextContent("2100");
    expect(within(row).getByText(vi.approvals.changed)).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /^bps/ })).not.toHaveTextContent(
      vi.approvals.changed,
    );
  });

  it("finds a masked customer and reveals personal data on demand", async () => {
    const mock = api();
    renderAdmin(mock, "/users?q=lan@");
    await userEvent.click(await screen.findByRole("link", { name: "Tran T*** L" }));
    expect(await screen.findByText("l***@example.com")).toBeInTheDocument();
    expect(screen.queryByText("lan@example.com")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.users.reveal }));
    expect(await screen.findByText("lan@example.com")).toBeInTheDocument();
    expect(mock.calls.filter((c) => c.path.endsWith("/pii-reveals"))).toHaveLength(1);
  });

  it("hides the reveal button without the permission", async () => {
    renderAdmin(api(["user:read:any"]), "/users/u1");
    expect(await screen.findByText("l***@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: vi.users.reveal })).not.toBeInTheDocument();
  });

  it("proposes a new fee version", async () => {
    const mock = api();
    renderAdmin(mock, "/config");
    await userEvent.click(await screen.findByRole("button", { name: vi.config.change }));
    const fixed = screen.getByLabelText(vi.config.fixed);
    await userEvent.clear(fixed);
    await userEvent.type(fixed, "2100");
    await userEvent.type(screen.getByLabelText(vi.config.reason), "Điều chỉnh phí rút tiền");
    await userEvent.click(screen.getByRole("button", { name: vi.config.propose }));
    expect(await screen.findByText(vi.config.proposed)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/versions"))?.body).toEqual({
      fixed: "2100",
      bps: 0,
      min_fee: "0",
      max_fee: null,
      reason: "Điều chỉnh phí rút tiền",
    });
  });

  it("proposes a role grant for another staff member", async () => {
    const mock = api();
    renderAdmin(mock, "/staff");
    await userEvent.click(await screen.findByRole("button", { name: vi.staff.change }));
    await userEvent.type(screen.getByLabelText(vi.config.reason), "Hỗ trợ hàng đợi KYC tuần này");
    await userEvent.click(screen.getByRole("button", { name: vi.config.propose }));
    expect(await screen.findByText(vi.staff.proposed)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/role-changes"))?.body).toEqual({
      role: "KYC_REVIEWER",
      op: "GRANT",
      reason: "Hỗ trợ hàng đợi KYC tuần này",
    });
  });

  it("switches the simulator mode with an audited reason", async () => {
    const mock = api();
    renderAdmin(mock, "/simulator");
    await userEvent.selectOptions(await screen.findByLabelText(vi.simulator.newMode), "OUTAGE");
    await userEvent.click(screen.getByRole("button", { name: vi.simulator.apply }));
    await userEvent.type(screen.getByLabelText(vi.simulator.reason), "Diễn tập sự cố");
    await userEvent.click(screen.getByRole("button", { name: vi.common.confirm }));
    expect(await screen.findByText(vi.simulator.done)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.method === "PUT")?.body).toEqual({
      mode: "OUTAGE",
      reason: "Diễn tập sự cố",
    });
  });

  it("answers a ticket with an internal note", async () => {
    const mock = api();
    renderAdmin(mock, "/tickets/TK261124ABCD");
    expect(await screen.findByText("Tôi đã nạp nhưng chưa thấy tiền")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.tickets.message), "Đã kiểm tra với provider");
    await userEvent.click(screen.getByLabelText(vi.tickets.markInternal));
    await userEvent.click(screen.getByRole("button", { name: vi.tickets.send }));
    expect(await screen.findByText(vi.tickets.sent)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path.endsWith("/messages"))?.body).toEqual({
      body: "Đã kiểm tra với provider",
      internal: true,
      status: null,
    });
  });

  it("refuses pages without the permission", async () => {
    renderAdmin(api(["user:read:any"]), "/staff");
    expect(await screen.findByText(vi.auth.noPermission)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: vi.nav.staff })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: vi.nav.users })).toBeInTheDocument();
  });
});

const PAGES = [
  "/",
  "/users?q=lan@",
  "/users/u1",
  "/operations",
  "/tickets",
  "/tickets/TK261124ABCD",
  "/config",
  "/staff",
  "/simulator",
  "/system",
  "/approvals",
  "/reconciliation",
];

async function settled(): Promise<void> {
  await screen.findByRole("heading", { level: 1 });
  await new Promise((resolve) => setTimeout(resolve, 60));
}

describe("accessibility of the new admin pages", () => {
  it.each(PAGES)("%s has no serious violations with data", async (path) => {
    const view = renderAdmin(api(), path);
    await settled();
    expect(await seriousViolations(view.container)).toEqual([]);
    view.unmount();
  });

  it.each(PAGES)("%s has no serious violations when the API fails", async (path) => {
    const failing = mockFetch({
      "GET /api/v1/admin/me": json(staff(ALL)),
      "GET /api/v1/admin/users/u1": problem(500, "INTERNAL"),
    });
    const view = renderAdmin(failing, path);
    await settled();
    expect(await seriousViolations(view.container)).toEqual([]);
    view.unmount();
  });
});
