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
  const view = render(
    <Providers i18n={createWebI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, view };
}

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
});

describe("account hub", () => {
  it("links every account area and has no serious accessibility issues", async () => {
    renderApp(api(), "/account");
    const nav = await screen.findByRole("navigation", { name: vi.account.title });
    for (const key of [
      "profile",
      "settings",
      "security",
      "kyc",
      "recipients",
      "support",
    ] as const) {
      expect(
        within(nav).getByRole("link", { name: new RegExp(vi.account.hub[key]) }),
      ).toBeVisible();
    }
    expect(await screen.findByText("lan@example.com")).toBeInTheDocument();
    expect(await seriousViolations()).toEqual([]);
  });

  it("saves the profile with a simulated phone and changes the e-mail with a code", async () => {
    const mock = api({
      "PATCH /api/v1/me": json({ ...me, phone: "+84912345678" }),
      "POST /api/v1/me/email": json({ status: "accepted" }, 202),
      "POST /api/v1/me/email/confirm": json({ ...me, email: "lan.moi@example.com" }),
    });
    renderApp(mock, "/account/profile");
    const save = await screen.findByRole("button", { name: vi.profile.save });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText(vi.profile.phone), "0912345678");
    await userEvent.click(save);
    expect(await screen.findByText(vi.profile.saved)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.method === "PATCH")?.body).toEqual({
      full_name: "Trần Thị Lan",
      display_name: "",
      phone: "0912345678",
    });

    await userEvent.type(screen.getByLabelText(vi.profile.newEmail), "lan.moi@example.com");
    await userEvent.type(screen.getByLabelText(vi.profile.password), "mật-khẩu-đủ-dài");
    await userEvent.click(screen.getByRole("button", { name: vi.profile.sendCode }));
    const code = await screen.findByLabelText(vi.profile.code);
    await userEvent.type(code, "12a3456");
    expect(code).toHaveValue("123456");
    await userEvent.click(screen.getByRole("button", { name: vi.profile.confirmEmail }));
    expect(await screen.findByText(vi.profile.emailChanged)).toBeInTheDocument();
  });

  it("locks the verified name", async () => {
    renderApp(api({ "GET /api/v1/me": json({ ...me, kyc_tier: 1 }) }), "/account/profile");
    expect(await screen.findByLabelText(vi.profile.fullName)).toBeDisabled();
    expect(screen.getByText(vi.profile.nameLocked)).toBeInTheDocument();
  });
});

describe("settings", () => {
  it("applies the theme at once and saves it for every device", async () => {
    const mock = api({ "PATCH /api/v1/me": json({ ...me, theme: "DARK" }) });
    renderApp(mock, "/settings");
    await userEvent.click(await screen.findByRole("radio", { name: vi.settings.themes.DARK }));
    await vitest.waitFor(() =>
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark"),
    );
    expect(mock.calls.find((c) => c.method === "PATCH")?.body).toEqual({ theme: "DARK" });
    expect(screen.getByRole("radio", { name: vi.settings.themes.DARK })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("privacy mode hides the balance and the amounts", async () => {
    renderApp(
      api({
        "GET /api/v1/me": json({ ...me, privacy_mode: true }),
        "GET /api/v1/wallet": json({
          id: "w1",
          wallet_number: "1234567897",
          currency: "VND",
          status: "ACTIVE",
          freeze_source: null,
          frozen_at: null,
          balance: { amount: "1250000", currency: "VND" },
        }),
        "GET /api/v1/transactions": json({
          items: [
            {
              id: "t1",
              reference: "VW261122ABCDEFGH01",
              type: "TRANSFER",
              status: "COMPLETED",
              direction: "OUT",
              amount: { amount: "50000", currency: "VND" },
              fee: { amount: "0", currency: "VND" },
              total: { amount: "50000", currency: "VND" },
              description: "",
              counterparty: { kind: "USER", name: "NGUYEN V*** A" },
              failure_code: null,
              category: { code: "TRANSFER_OUT", name: "Chuyển tiền đi" },
              created_at: "2026-11-22T03:00:00Z",
              completed_at: "2026-11-22T03:00:01Z",
            },
          ],
          next_cursor: null,
        }),
      }),
      "/",
    );
    expect(await screen.findByLabelText(vi.settings.amountHidden)).toBeInTheDocument();
    expect(screen.queryByText(/1\.250\.000/)).not.toBeInTheDocument();
    expect(screen.queryByText(/50\.000/)).not.toBeInTheDocument();
  });
});

describe("saved recipients", () => {
  it("lists and removes recipients", async () => {
    const mock = api({
      "GET /api/v1/recipients": json([
        {
          id: "r1",
          nickname: "Anh Minh",
          last_used_at: null,
          recipient: {
            wallet_number_masked: "******4821",
            display_name: "NGUYEN V*** A",
            kind: "USER",
            active: true,
          },
        },
      ]),
      "DELETE /api/v1/recipients/r1": json(null, 204),
    });
    renderApp(mock, "/account/recipients");
    await userEvent.click(await screen.findByRole("button", { name: "Xóa Anh Minh" }));
    expect(await screen.findByText(vi.recipients.removed)).toBeInTheDocument();
  });
});
