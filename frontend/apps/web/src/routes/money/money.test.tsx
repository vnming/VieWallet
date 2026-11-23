import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi as vitest } from "vitest";

import { Providers } from "../../app/providers";
import { createQueryClient } from "../../app/queryClient";
import { routes } from "../../app/router";
import { createWebI18n } from "../../i18n";
import { goToProvider } from "../../features/money/redirect";
import vi from "../../i18n/vi.json";

vitest.mock("../../features/money/redirect", () => ({ goToProvider: vitest.fn() }));

const me = {
  id: "u1",
  email: "lan@example.com",
  full_name: "Trần Thị Lan",
  display_name: null,
  locale: "vi",
  user_type: "CUSTOMER",
  email_verified: true,
  created_at: "2026-11-21T08:00:00Z",
  permissions: [],
  pin: { is_set: true, locked_until: null, requires_reset: false },
  kyc_tier: 1,
};

function flow(overrides: object = {}) {
  return {
    id: "t1",
    reference: "VW261122ABCDEFGH01",
    type: "DEPOSIT",
    status: "PENDING",
    amount: { amount: "250000", currency: "VND" },
    fee: { amount: "0", currency: "VND" },
    total: { amount: "250000", currency: "VND" },
    failure_code: null,
    next_action: "REDIRECT",
    checkout_url: "http://127.0.0.1:8090/checkout/SIMC1",
    quote_expires_at: null,
    destination: null,
    created_at: "2026-11-22T03:00:00Z",
    completed_at: null,
    events: [
      { from_status: null, to_status: "CREATED", reason_code: null, at: "2026-11-22T03:00:00Z" },
    ],
    ...overrides,
  };
}

const destination = {
  id: "d1",
  bank_code: "SENVANG",
  bank_name: "Ngân hàng Mô phỏng Sen Vàng",
  account_number_masked: "••••6789",
  account_name: "TRAN THI LAN",
  created_at: "2026-11-21T08:00:00Z",
  usable_from: "2026-11-21T09:00:00Z",
};

function api(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/notifications/unread-count": json({ unread: 3 }),
    "GET /api/v1/wallet/banks": json([
      { code: "SIMBANK", name: "SimBank (mô phỏng)" },
      { code: "SENVANG", name: "Ngân hàng Mô phỏng Sen Vàng" },
    ]),
    "GET /api/v1/wallet/destinations": json([destination]),
    ...extra,
  });
}

function renderApp(mock: MockFetch, path: string) {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: mock.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers i18n={createWebI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router };
}

afterEach(() => vitest.clearAllMocks());

describe("money movement", () => {
  it("starts a deposit with one idempotency key and goes to SimBank", async () => {
    const mock = api({ "POST /api/v1/deposits": problem(503, "SERVICE_UNAVAILABLE", "Lỗi") });
    renderApp(mock, "/deposit");
    const submit = await screen.findByRole("button", { name: vi.deposit.submit });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText(vi.money.amount), "1500");
    expect(submit).toBeDisabled(); // not a multiple of 1 000
    await userEvent.click(screen.getByRole("button", { name: /500\.000/ }));
    await userEvent.click(submit);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    mock.on("POST /api/v1/deposits", json(flow(), 201));
    await userEvent.click(submit);
    await vitest.waitFor(() => expect(goToProvider).toHaveBeenCalledWith(flow().checkout_url));
    const calls = mock.calls.filter((c) => c.path === "/api/v1/deposits");
    expect(calls[0]?.headers.get("Idempotency-Key")).toBe(calls[1]?.headers.get("Idempotency-Key"));
    expect(calls[1]?.body).toEqual({ amount: "500000", method: "SIMBANK" });
  });

  it("shows the deposit result with the timeline", async () => {
    const done = flow({
      status: "COMPLETED",
      next_action: "NONE",
      checkout_url: null,
      events: [
        { from_status: null, to_status: "CREATED", reason_code: null, at: "2026-11-22T03:00:00Z" },
        {
          from_status: "CREATED",
          to_status: "PENDING",
          reason_code: null,
          at: "2026-11-22T03:00:00Z",
        },
        {
          from_status: "PENDING",
          to_status: "COMPLETED",
          reason_code: null,
          at: "2026-11-22T03:01:00Z",
        },
      ],
    });
    renderApp(
      api({ "GET /api/v1/deposits/VW261122ABCDEFGH01": json(done) }),
      "/deposits/VW261122ABCDEFGH01",
    );
    expect(await screen.findByRole("heading", { name: vi.deposit.completed })).toBeInTheDocument();
    expect(screen.getByText("+250.000 ₫")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(3);
  });

  it("explains a failed withdrawal and the refund", async () => {
    const failed = flow({
      type: "WITHDRAWAL",
      status: "FAILED",
      failure_code: "ACCOUNT_CLOSED",
      next_action: "NONE",
      checkout_url: null,
      total: { amount: "1001100", currency: "VND" },
    });
    renderApp(
      api({ "GET /api/v1/withdrawals/VW261122ABCDEFGH01": json(failed) }),
      "/withdrawals/VW261122ABCDEFGH01",
    );
    expect(await screen.findByRole("heading", { name: vi.withdraw.failed })).toBeInTheDocument();
    expect(screen.getByText(vi.money.failure.ACCOUNT_CLOSED)).toBeInTheDocument();
    expect(screen.getByText(vi.withdraw.refunded)).toBeInTheDocument();
  });

  it("requires KYC tier 1 to withdraw", async () => {
    renderApp(api({ "GET /api/v1/me": json({ ...me, kyc_tier: 0 }) }), "/withdraw");
    expect(await screen.findByText(vi.withdraw.kycRequired)).toBeInTheDocument();
  });

  it("quotes, confirms with the PIN and opens the result", async () => {
    const quoted = flow({
      type: "WITHDRAWAL",
      status: "CREATED",
      next_action: "NONE",
      checkout_url: null,
      amount: { amount: "1000000", currency: "VND" },
      fee: { amount: "1100", currency: "VND" },
      total: { amount: "1001100", currency: "VND" },
      quote_expires_at: "2026-11-22T03:10:00Z",
      destination: {
        bank_code: "SENVANG",
        name: destination.bank_name,
        account_masked: "••••6789",
        account_name: "TRAN THI LAN",
      },
    });
    const held = { ...quoted, status: "PENDING", next_action: "WAIT_PROVIDER" };
    const mock = api({
      "POST /api/v1/withdrawals": json(quoted, 201),
      "POST /api/v1/withdrawals/VW261122ABCDEFGH01/confirm": problem(
        422,
        "PIN_INVALID",
        "PIN giao dịch không đúng",
        { detail: "remaining_attempts=4" },
      ),
      "GET /api/v1/withdrawals/VW261122ABCDEFGH01": json(held),
    });
    const { router } = renderApp(mock, "/withdraw");
    await userEvent.type(await screen.findByLabelText(vi.money.amount), "1000000");
    await userEvent.click(screen.getByRole("button", { name: vi.withdraw.quote }));
    expect(await screen.findByText("1.001.100 ₫")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.pin.current), "111111");
    await userEvent.click(screen.getByRole("button", { name: vi.withdraw.confirm }));
    expect(await screen.findByText("Còn 4 lần thử.")).toBeInTheDocument();
    mock.on("POST /api/v1/withdrawals/VW261122ABCDEFGH01/confirm", json(held));
    await userEvent.click(screen.getByRole("button", { name: vi.withdraw.confirm }));
    expect(await screen.findByRole("heading", { name: vi.withdraw.pending })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/withdrawals/VW261122ABCDEFGH01");
    const confirms = mock.calls.filter((c) => c.path.endsWith("/confirm"));
    expect(confirms[0]?.headers.get("Idempotency-Key")).toBe(
      confirms[1]?.headers.get("Idempotency-Key"),
    );
    expect(mock.calls.find((c) => c.path === "/api/v1/withdrawals")?.body).toEqual({
      destination_id: "d1",
      amount: "1000000",
    });
  });

  it("asks for a bank account first", async () => {
    renderApp(api({ "GET /api/v1/wallet/destinations": json([]) }), "/withdraw");
    expect(await screen.findByText(vi.withdraw.noDestination)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: vi.withdraw.addDestination })).toHaveAttribute(
      "href",
      "/wallet/destinations",
    );
  });

  it("adds and removes bank accounts", async () => {
    const mock = api({
      "POST /api/v1/wallet/destinations": json(destination, 201),
      "DELETE /api/v1/wallet/destinations/d1": json(null, 204),
    });
    renderApp(mock, "/wallet/destinations");
    expect(await screen.findByText(/••••6789/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.destinations.accountNumber), "01a23456789");
    expect(screen.getByLabelText(vi.destinations.accountNumber)).toHaveValue("0123456789");
    await userEvent.type(screen.getByLabelText(vi.destinations.accountName), "Tran Thi Lan");
    await userEvent.click(screen.getByRole("button", { name: vi.destinations.add }));
    expect(await screen.findByText(vi.destinations.added)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.method === "POST")?.body).toEqual({
      bank_code: "SIMBANK",
      account_number: "0123456789",
      account_name: "Tran Thi Lan",
    });
    await userEvent.click(screen.getByRole("button", { name: vi.destinations.remove }));
    expect(await screen.findByText(vi.destinations.removed)).toBeInTheDocument();
  });

  it("lists notifications, marks them read and follows the link", async () => {
    const item = {
      id: "n1",
      category: "TRANSACTION",
      type: "transaction.deposit.completed",
      title: "Nạp tiền thành công",
      body: "250.000 ₫ đã vào ví của bạn.",
      data: { link: "/history/VW261122ABCDEFGH01" },
      priority: "NORMAL",
      read_at: null,
      created_at: "2026-11-22T03:00:00Z",
    };
    const mock = api({
      "GET /api/v1/notifications": json({ items: [item], next_cursor: null }),
      "POST /api/v1/notifications/n1/read": json(null, 204),
      "GET /api/v1/transactions/VW261122ABCDEFGH01": problem(404, "TXN_NOT_FOUND"),
    });
    const { router } = renderApp(mock, "/notifications");
    expect(await screen.findByRole("link", { name: "3 thông báo chưa đọc" })).toBeInTheDocument();
    const list = await screen.findByRole("list");
    await userEvent.click(within(list).getByRole("button", { name: /Nạp tiền thành công/ }));
    await vitest.waitFor(() =>
      expect(router.state.location.pathname).toBe("/history/VW261122ABCDEFGH01"),
    );
    expect(mock.calls.some((c) => c.path === "/api/v1/notifications/n1/read")).toBe(true);
  });
});
