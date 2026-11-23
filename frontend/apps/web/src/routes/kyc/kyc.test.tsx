import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi as vi_ } from "vitest";

import { Providers } from "../../app/providers";
import { createQueryClient } from "../../app/queryClient";
import { routes } from "../../app/router";
import { createWebI18n } from "../../i18n";
import vi from "../../i18n/vi.json";

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
};

function draft(documents: string[] = []) {
  return {
    id: "s1",
    status: "DRAFT",
    full_name: "Trần Thị Lan",
    date_of_birth: "1995-04-12",
    document_type: "NATIONAL_ID",
    document_number_masked: "••••••••1234",
    address: "12 Đường Hư Cấu",
    documents: documents.map((kind) => ({
      id: kind,
      kind,
      content_type: "image/png",
      size_bytes: 100,
      uploaded_at: "2026-11-23T03:00:00Z",
    })),
    required_documents: ["BACK", "FRONT", "SELFIE"],
    submitted_at: null,
    decided_at: null,
    reject_reason: null,
  };
}

function status(overrides: object = {}) {
  return {
    status: "NOT_STARTED",
    tier: 0,
    approved_at: null,
    expires_at: null,
    submission: null,
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
}

describe("KYC", () => {
  it("saves the draft, uploads raw files and submits", async () => {
    const mock = api({
      "GET /api/v1/kyc": json(status()),
      "PUT /api/v1/kyc/draft": json(status({ submission: draft() })),
    });
    renderApp(mock, "/kyc");
    expect(await screen.findByText(vi.kyc.simulatedTitle)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.kyc.fullName), "Trần Thị Lan");
    await userEvent.type(screen.getByLabelText(vi.kyc.dateOfBirth), "1995-04-12");
    await userEvent.type(screen.getByLabelText(vi.kyc.documentNumber), "079195001234");
    await userEvent.type(screen.getByLabelText(vi.kyc.address), "12 Đường Hư Cấu");
    mock.on("GET /api/v1/kyc", json(status({ submission: draft() })));
    await userEvent.click(screen.getByRole("button", { name: vi.kyc.save }));
    expect(await screen.findByText(vi.kyc.saved)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.method === "PUT")?.body).toEqual({
      full_name: "Trần Thị Lan",
      date_of_birth: "1995-04-12",
      document_type: "NATIONAL_ID",
      document_number: "079195001234",
      address: "12 Đường Hư Cấu",
    });

    const submit = await screen.findByRole("button", { name: vi.kyc.submit });
    expect(submit).toBeDisabled();
    mock.on("PUT /api/v1/kyc/draft/documents/FRONT", json(draft(["FRONT"]).documents[0], 201));
    const file = new File([new Uint8Array([137, 80, 78, 71])], "front.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText(vi.kyc.kind.FRONT), file);
    await vi_.waitFor(() =>
      expect(mock.calls.some((c) => c.path.endsWith("/documents/FRONT"))).toBe(true),
    );
    const upload = mock.calls.find((c) => c.path.endsWith("/documents/FRONT"));
    expect(upload?.headers.get("Content-Type")).toBe("image/png"); // the raw file, no multipart

    mock.on("GET /api/v1/kyc", json(status({ submission: draft(["FRONT", "BACK", "SELFIE"]) })));
    mock.on(
      "POST /api/v1/kyc/submit",
      json(status({ status: "PENDING", submission: { ...draft(), status: "PENDING" } })),
    );
    mock.on("PUT /api/v1/kyc/draft/documents/SELFIE", json(draft(["SELFIE"]).documents[0], 201));
    await userEvent.upload(
      screen.getByLabelText(vi.kyc.kind.SELFIE),
      new File([new Uint8Array([1])], "s.png", { type: "image/png" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: vi.kyc.submit }));
    expect(await screen.findByText(vi.kyc.submitted)).toBeInTheDocument();
  });

  it("refuses files over 5 MB before uploading", async () => {
    const mock = api({ "GET /api/v1/kyc": json(status({ submission: draft() })) });
    renderApp(mock, "/kyc");
    const big = new File([new Uint8Array(5 * 1024 * 1024 + 1)], "big.jpg", { type: "image/jpeg" });
    await userEvent.upload(await screen.findByLabelText(vi.kyc.kind.BACK), big);
    expect(await screen.findByText(vi.kyc.tooLarge)).toBeInTheDocument();
    expect(mock.calls.some((c) => c.path.includes("/documents/"))).toBe(false);
  });

  it("explains a rejection and shows waiting and approved states", async () => {
    renderApp(
      api({
        "GET /api/v1/kyc": json(
          status({
            status: "REJECTED",
            submission: { ...draft(), status: "REJECTED", reject_reason: "BLURRY_IMAGE" },
          }),
        ),
      }),
      "/kyc",
    );
    expect(await screen.findByText(vi.kyc.rejectedTitle)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(vi.kyc.reason.BLURRY_IMAGE))).toBeInTheDocument();
  });

  it("shows the review in progress", async () => {
    renderApp(api({ "GET /api/v1/kyc": json(status({ status: "IN_REVIEW" })) }), "/kyc");
    expect(await screen.findByText(vi.kyc.status.IN_REVIEW)).toBeInTheDocument();
  });

  it("shows an approved profile", async () => {
    renderApp(
      api({
        "GET /api/v1/kyc": json(
          status({ status: "APPROVED", tier: 1, expires_at: "2028-11-23T03:00:00Z" }),
        ),
      }),
      "/kyc",
    );
    expect(await screen.findByText(vi.kyc.approved)).toBeInTheDocument();
  });
});

describe("limits", () => {
  it("shows used and remaining amounts and what needs KYC", async () => {
    renderApp(
      api({
        "GET /api/v1/wallet/limits": json({
          kyc_tier: 0,
          currency: "VND",
          balance_cap: "10000000",
          balance: "2000000",
          types: [
            {
              type: "TRANSFER",
              allowed: true,
              limits: [
                {
                  period: "DAILY",
                  max_amount: "5000000",
                  used_amount: "1000000",
                  remaining_amount: "4000000",
                  max_count: null,
                  used_count: 1,
                },
              ],
            },
            { type: "WITHDRAWAL", allowed: false, limits: [] },
          ],
        }),
      }),
      "/wallet/limits",
    );
    expect(await screen.findByText(/4\.000\.000/)).toBeInTheDocument();
    expect(screen.getByText(vi.limits.notAllowed)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: vi.limits.upgrade })).toHaveAttribute("href", "/kyc");
  });
});
