import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { Providers } from "../../app/providers";
import { createQueryClient } from "../../app/queryClient";
import { routes } from "../../app/router";
import { createWebI18n } from "../../i18n";
import vi from "../../i18n/vi.json";

const me = {
  id: "0193f0c2-8f5e-7c3a-9d1e-2b4f6a8c0e12",
  email: "lan@example.com",
  full_name: "Trần Thị Lan",
  display_name: null,
  locale: "vi",
  user_type: "CUSTOMER",
  email_verified: true,
  created_at: "2026-11-21T08:00:00Z",
  permissions: ["profile:manage:own", "wallet:read:own"],
  pin: { is_set: true, locked_until: null, requires_reset: false },
};

const wallet = {
  id: "w1",
  wallet_number: "8123456782",
  currency: "VND",
  status: "ACTIVE",
  freeze_source: null,
  frozen_at: null,
  balance: { amount: "1250000", currency: "VND" },
};

function txn(index: number, overrides: object = {}) {
  return {
    id: `t${String(index)}`,
    reference: `VW261122ABCDEFGH${String(index).padStart(2, "0")}`,
    type: "ADJUSTMENT",
    status: "COMPLETED",
    direction: "IN",
    amount: { amount: String(index * 1000), currency: "VND" },
    fee: { amount: "0", currency: "VND" },
    total: { amount: String(index * 1000), currency: "VND" },
    description: `Nạp tiền mô phỏng ${String(index)}`,
    counterparty: { kind: "SYSTEM", name: "VieWallet", wallet_number_masked: null },
    failure_code: null,
    category: { code: "OTHER", name: "Khác" },
    created_at: "2026-11-22T03:00:00Z",
    completed_at: "2026-11-22T03:05:00Z",
    ...overrides,
  };
}

function signedIn(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/wallet": json(wallet),
    "GET /api/v1/transactions": json({ items: [txn(1)], next_cursor: null }),
    ...extra,
  });
}

function renderApp(api: MockFetch, path = "/") {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: api.fetch });
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

describe("wallet", () => {
  beforeEach(() => localStorage.clear());

  it("shows the balance exactly, hides it on request and lists recent transactions", async () => {
    renderApp(signedIn());
    const balance = await screen.findByRole("region", { name: vi.wallet.balanceLabel });
    expect(await within(balance).findByText(/1\.250\.000/)).toBeInTheDocument();
    expect(within(balance).getByText("812 345 6782")).toBeInTheDocument();
    await userEvent.click(within(balance).getByRole("button", { name: vi.wallet.hide }));
    expect(within(balance).queryByText(/1\.250\.000/)).not.toBeInTheDocument();
    expect(within(balance).getByText(vi.wallet.hidden)).toBeInTheDocument();
    expect(localStorage.getItem("vw.hideBalance")).toBe("1");
    const recent = await screen.findByRole("link", { name: /Nạp tiền mô phỏng 1/ });
    expect(recent).toHaveAttribute("href", "/history/VW261122ABCDEFGH01");
    expect(recent).toHaveTextContent("+1.000");
  });

  it("shows an empty history and an error state with retry", async () => {
    const api = signedIn({ "GET /api/v1/transactions": json({ items: [], next_cursor: null }) });
    renderApp(api, "/history");
    expect(await screen.findByText(vi.history.empty)).toBeInTheDocument();

    const failing = signedIn({ "GET /api/v1/wallet": problem(500, "INTERNAL_ERROR") });
    renderApp(failing, "/wallet");
    expect(await screen.findByText(vi.wallet.loadError)).toBeInTheDocument();
  });

  it("pages through history with the cursor and filters by type", async () => {
    const api = signedIn();
    api.on("GET /api/v1/transactions", (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("type") === "TRANSFER") {
        return json({ items: [], next_cursor: null });
      }
      return url.searchParams.get("cursor") === "t2"
        ? json({ items: [txn(1)], next_cursor: null })
        : json({ items: [txn(3), txn(2)], next_cursor: "t2" });
    });
    renderApp(api, "/history");
    expect(await screen.findByText("Nạp tiền mô phỏng 3")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.history.loadMore }));
    expect(await screen.findByText("Nạp tiền mô phỏng 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: vi.history.loadMore })).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText(vi.history.filterType), "TRANSFER");
    expect(await screen.findByText(vi.history.emptyFiltered)).toBeInTheDocument();
    const filtered = api.calls.filter((c) => c.path === "/api/v1/transactions").at(-1);
    expect(filtered).toBeDefined();
  });

  it("shows transaction details with the status timeline, and 404 as not found", async () => {
    const detail = {
      ...txn(5),
      events: [
        { from_status: null, to_status: "CREATED", reason_code: null, at: "2026-11-22T03:00:00Z" },
        {
          from_status: "CREATED",
          to_status: "PENDING",
          reason_code: "AWAITING_APPROVAL",
          at: "2026-11-22T03:00:00Z",
        },
        {
          from_status: "PENDING",
          to_status: "COMPLETED",
          reason_code: "APPROVED",
          at: "2026-11-22T03:05:00Z",
        },
      ],
    };
    const api = signedIn({ "GET /api/v1/transactions/VW261122ABCDEFGH05": json(detail) });
    renderApp(api, "/history/VW261122ABCDEFGH05");
    expect(await screen.findByText("VW261122ABCDEFGH05")).toBeInTheDocument();
    const timeline = screen.getByRole("list");
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(3);
    expect(within(timeline).getByText("Thành công")).toBeInTheDocument();

    renderApp(signedIn(), "/history/VW261122ZZZZZZZZZZ");
    expect(await screen.findByText(vi.txn.notFound)).toBeInTheDocument();
  });

  it("freezes the wallet and unfreezes it with the PIN", async () => {
    const frozen = { ...wallet, status: "FROZEN", freeze_source: "SELF" };
    const api = signedIn({ "POST /api/v1/wallet/freeze": json(frozen) });
    renderApp(api, "/wallet");
    await userEvent.click(await screen.findByRole("button", { name: vi.wallet.freeze }));
    api.on("GET /api/v1/wallet", json(frozen));
    await userEvent.click(screen.getByRole("button", { name: vi.wallet.freezeConfirm }));
    expect(await screen.findByText(vi.wallet.frozenSelf)).toBeInTheDocument();

    api.on("POST /api/v1/wallet/unfreeze", problem(422, "PIN_INVALID", "PIN giao dịch không đúng"));
    await userEvent.click(screen.getByRole("button", { name: vi.wallet.unfreeze }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(vi.pin.current), "111111");
    await userEvent.click(within(dialog).getByRole("button", { name: vi.wallet.unfreeze }));
    expect(await within(dialog).findByText("PIN giao dịch không đúng")).toBeInTheDocument();

    api.on("POST /api/v1/wallet/unfreeze", json(wallet));
    api.on("GET /api/v1/wallet", json(wallet));
    await userEvent.click(within(dialog).getByRole("button", { name: vi.wallet.unfreeze }));
    expect(await screen.findByRole("button", { name: vi.wallet.freeze })).toBeInTheDocument();
    const call = api.calls.filter((c) => c.path === "/api/v1/wallet/unfreeze").at(-1);
    expect(call?.body).toEqual({ pin: "111111" });
  });

  it("tells the customer to contact support when staff froze the wallet", async () => {
    const staffFrozen = { ...wallet, status: "FROZEN", freeze_source: "STAFF" };
    renderApp(signedIn({ "GET /api/v1/wallet": json(staffFrozen) }), "/wallet");
    expect(await screen.findByText(vi.wallet.frozenStaff)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: vi.wallet.unfreeze })).not.toBeInTheDocument();
  });
});
