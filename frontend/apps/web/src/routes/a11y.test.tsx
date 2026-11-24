/**
 * Phase 7 acceptance: no serious/critical axe violation on any customer page, in the data
 * state and in the error state (every API call failing).
 */
import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { Providers } from "../app/providers";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/router";
import { createWebI18n } from "../i18n";
import { seriousViolations } from "../test/a11y";

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
  phone: null,
  theme: "SYSTEM",
  privacy_mode: false,
};
const wallet = {
  id: "w1",
  wallet_number: "1234567897",
  currency: "VND",
  status: "ACTIVE",
  freeze_source: null,
  frozen_at: null,
  balance: { amount: "1250000", currency: "VND" },
};
const empty = { items: [], next_cursor: null };

function dataApi(): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/wallet": json(wallet),
    "GET /api/v1/transactions": json(empty),
    "GET /api/v1/notifications": json(empty),
    "GET /api/v1/notifications/unread-count": json({ unread: 0 }),
    "GET /api/v1/merchant": problem(404, "MERCHANT_NOT_FOUND"),
    "GET /api/v1/recipients": json([]),
    "GET /api/v1/wallet/destinations": json([]),
    "GET /api/v1/wallet/banks": json([{ code: "SIMBANK", name: "SimBank" }]),
    "GET /api/v1/payment-requests": json([]),
    "GET /api/v1/support/tickets": json([]),
    "GET /api/v1/help/articles": json([]),
    "GET /api/v1/me/sessions": json([]),
    "GET /api/v1/me/login-history": json([]),
    "GET /api/v1/kyc": json({
      status: "NOT_STARTED",
      tier: 0,
      approved_at: null,
      expires_at: null,
      submission: null,
    }),
    "GET /api/v1/wallet/limits": json({
      kyc_tier: 1,
      currency: "VND",
      balance_cap: "100000000",
      balance: "1250000",
      types: [],
    }),
    "GET /api/v1/wallet/qr": json({
      wallet_number: "1234567897",
      display_name: "TRAN T*** L",
      payload: "http://127.0.0.1:8080/transfer?to=1234567897",
    }),
  });
}

function failingApi(): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/notifications/unread-count": json({ unread: 0 }),
    // Every other call answers 404/500 through the mock's default: the error states render.
  });
}

const PAGES = [
  "/",
  "/wallet",
  "/history",
  "/transfer",
  "/scan",
  "/receive",
  "/request",
  "/deposit",
  "/withdraw",
  "/wallet/destinations",
  "/wallet/limits",
  "/notifications",
  "/kyc",
  "/account",
  "/account/profile",
  "/account/security",
  "/account/recipients",
  "/settings",
  "/support/tickets",
  "/help",
  "/merchant",
];

function renderAt(mock: MockFetch, path: string) {
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: mock.fetch });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <Providers i18n={createWebI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
}

async function settled(): Promise<void> {
  await screen.findByRole("heading", { level: 1 });
  // Let pending queries resolve so loading skeletons are replaced by content or errors.
  await new Promise((resolve) => setTimeout(resolve, 60));
}

describe("accessibility of customer pages", () => {
  it.each(PAGES)("%s has no serious violations with data", async (path) => {
    const view = renderAt(dataApi(), path);
    await settled();
    expect(await seriousViolations(view.container)).toEqual([]);
    view.unmount();
  });

  it.each(PAGES)("%s has no serious violations when the API fails", async (path) => {
    const view = renderAt(failingApi(), path);
    await settled();
    expect(await seriousViolations(view.container)).toEqual([]);
    view.unmount();
  });
});
