import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi as vitest } from "vitest";

import { Providers } from "../../app/providers";
import { createQueryClient } from "../../app/queryClient";
import { routes } from "../../app/router";
import { createWebI18n } from "../../i18n";
import vi from "../../i18n/vi.json";
import { seriousViolations } from "../../test/a11y";

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
  kyc_tier: 0,
  phone: null,
  theme: "SYSTEM",
  privacy_mode: false,
};

const REF = "VW261122ABCDEFGH01";

function txn(overrides: object = {}) {
  return {
    id: "t1",
    reference: REF,
    type: "TRANSFER",
    status: "COMPLETED",
    direction: "OUT",
    amount: { amount: "120000", currency: "VND" },
    fee: { amount: "0", currency: "VND" },
    total: { amount: "120000", currency: "VND" },
    description: "Tiền cơm",
    counterparty: { kind: "USER", name: "NGUYEN V*** A" },
    failure_code: null,
    category: { code: "TRANSFER_OUT", name: "Chuyển tiền đi" },
    created_at: "2026-11-22T03:00:00Z",
    completed_at: "2026-11-22T03:00:01Z",
    ...overrides,
  };
}

function ticket(overrides: object = {}) {
  return {
    reference: "TKABCD2345",
    category: "TRANSACTION",
    subject: "Chuyển nhầm người",
    status: "PENDING_CUSTOMER",
    transaction_reference: REF,
    created_at: "2026-11-22T03:00:00Z",
    updated_at: "2026-11-22T04:00:00Z",
    messages: [
      {
        id: "m1",
        author: "CUSTOMER",
        body: "Tôi chuyển nhầm.",
        internal: false,
        created_at: "2026-11-22T03:00:00Z",
      },
      {
        id: "m2",
        author: "STAFF",
        body: "Chúng tôi đang xử lý.",
        internal: false,
        created_at: "2026-11-22T04:00:00Z",
      },
    ],
    ...overrides,
  };
}

function api(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/notifications/unread-count": json({ unread: 0 }),
    "GET /api/v1/merchant": problem(404, "MERCHANT_NOT_FOUND"),
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

describe("help center", () => {
  it("searches articles (also signed out) and shows an empty state", async () => {
    const mock = api({
      "GET /api/v1/me": problem(401, "SESSION_EXPIRED"),
      "GET /api/v1/help/articles": (request: Request) =>
        new URL(request.url).searchParams.get("q")
          ? json([])
          : json([
              {
                slug: "withdraw",
                category: "money",
                title: "Rút tiền về ngân hàng",
                summary: "Cần KYC.",
              },
            ]),
    });
    renderApp(mock, "/help");
    expect(await screen.findByRole("link", { name: /Rút tiền về ngân hàng/ })).toHaveAttribute(
      "href",
      "/help/withdraw",
    );
    expect(await seriousViolations()).toEqual([]);
    await userEvent.type(screen.getByLabelText(vi.help.search), "zzz");
    expect(await screen.findByText(vi.help.noResults)).toBeInTheDocument();
  });

  it("renders the article Markdown as text, never as HTML", async () => {
    renderApp(
      api({
        "GET /api/v1/help/articles/security": json({
          slug: "security",
          category: "account",
          title: "Bảo mật tài khoản",
          summary: "Khóa ví khi mất máy.",
          body: "## Mất điện thoại?\n\nVào **Bảo mật**.\n\n- Một\n- <img src=x onerror=alert(1)>",
          updated_at: "2026-11-22T03:00:00Z",
        }),
      }),
      "/help/security",
    );
    expect(await screen.findByRole("heading", { level: 2, name: "Mất điện thoại?" })).toBeVisible();
    expect(screen.getByText("Bảo mật", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img[src='x']")).toBeNull();
  });
});

describe("support tickets", () => {
  it("opens a ticket about a transaction from its detail page", async () => {
    const mock = api({
      [`GET /api/v1/transactions/${REF}`]: json({ ...txn(), events: [] }),
      "GET /api/v1/transactions/categories": json([
        { code: "TRANSFER_OUT", name: "Chuyển tiền đi" },
        { code: "FOOD", name: "Ăn uống" },
      ]),
      "GET /api/v1/support/tickets": json([]),
      "POST /api/v1/support/tickets": json(ticket({ status: "OPEN", messages: [] }), 201),
      "GET /api/v1/support/tickets/TKABCD2345": json(ticket()),
    });
    const { router } = renderApp(mock, `/history/${REF}`);
    await userEvent.click(await screen.findByRole("link", { name: vi.txn.report }));
    expect(router.state.location.search).toBe(`?txn=${REF}`);
    await userEvent.type(await screen.findByLabelText(vi.support.subject), "Chuyển nhầm người");
    await userEvent.type(screen.getByLabelText(vi.support.message), "Tôi chuyển nhầm số ví.");
    await userEvent.click(screen.getByRole("button", { name: vi.support.send }));
    await vitest.waitFor(() =>
      expect(router.state.location.pathname).toBe("/support/tickets/TKABCD2345"),
    );
    expect(
      mock.calls.find((c) => c.path === "/api/v1/support/tickets" && c.method === "POST")?.body,
    ).toEqual({
      category: "TRANSACTION",
      subject: "Chuyển nhầm người",
      message: "Tôi chuyển nhầm số ví.",
      transaction_reference: REF,
    });
    expect(await screen.findByText("Chúng tôi đang xử lý.")).toBeInTheDocument();
    expect(await seriousViolations()).toEqual([]);
  });

  it("replies in the conversation; a closed ticket cannot be answered", async () => {
    const mock = api({
      "GET /api/v1/support/tickets/TKABCD2345": json(ticket()),
      "POST /api/v1/support/tickets/TKABCD2345/messages": json(ticket({ status: "OPEN" })),
    });
    renderApp(mock, "/support/tickets/TKABCD2345");
    await userEvent.type(await screen.findByLabelText(vi.support.reply), "Cảm ơn.");
    await userEvent.click(screen.getByRole("button", { name: vi.support.send }));
    expect(await screen.findByText(vi.support.status.OPEN)).toBeInTheDocument();
    mock.on("GET /api/v1/support/tickets/TKXXXX2345", json(ticket({ status: "CLOSED" })));
  });

  it("shows the closed note", async () => {
    renderApp(
      api({ "GET /api/v1/support/tickets/TKABCD2345": json(ticket({ status: "CLOSED" })) }),
      "/support/tickets/TKABCD2345",
    );
    expect(await screen.findByText(vi.support.closedNote)).toBeInTheDocument();
    expect(screen.queryByLabelText(vi.support.reply)).not.toBeInTheDocument();
  });
});

describe("history", () => {
  it("filters, changes a category and exports CSV", async () => {
    const mock = api({
      "GET /api/v1/transactions": json({ items: [txn()], next_cursor: null }),
      "POST /api/v1/transactions/exports": json(
        {
          id: "e1",
          status: "PENDING",
          date_from: "2026-11-01",
          date_to: "2026-11-22",
          row_count: null,
          truncated: false,
          created_at: "2026-11-22T03:00:00Z",
          expires_at: "2026-11-23T03:00:00Z",
          download_url: null,
        },
        202,
      ),
      "GET /api/v1/transactions/exports/e1": json({
        id: "e1",
        status: "READY",
        date_from: "2026-11-01",
        date_to: "2026-11-22",
        row_count: 7,
        truncated: false,
        created_at: "2026-11-22T03:00:00Z",
        expires_at: "2026-11-23T03:00:00Z",
        download_url: "/api/v1/transactions/exports/e1/download",
      }),
    });
    renderApp(mock, "/history");
    expect(await screen.findByText(vi.history.export.title)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(vi.history.filterDirection), "OUT");
    await userEvent.type(screen.getByLabelText(vi.history.search), "cơm");
    await userEvent.type(screen.getByLabelText(vi.history.minAmount), "10.000");
    await vitest.waitFor(() => {
      const last = [...mock.calls].reverse().find((c) => c.path === "/api/v1/transactions");
      expect(last).toBeDefined();
    });
    await userEvent.click(screen.getByRole("button", { name: vi.history.export.create }));
    expect(await screen.findByRole("link", { name: "Tải tệp (7 giao dịch)" })).toHaveAttribute(
      "href",
      "/api/v1/transactions/exports/e1/download",
    );
    expect(await seriousViolations()).toEqual([]);
  });
});
