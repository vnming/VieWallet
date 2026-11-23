import { createApiClient } from "@viewallet/api-client";
import { json, mockFetch, problem, type MockFetch } from "@viewallet/api-client/testing";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi as vitest } from "vitest";

import { Providers } from "../../app/providers";
import { createQueryClient } from "../../app/queryClient";
import { routes } from "../../app/router";
import { parseScanned } from "../../features/spend/scan";
import { createWebI18n } from "../../i18n";
import vi from "../../i18n/vi.json";

vitest.mock("qrcode", () => ({
  default: { toDataURL: vitest.fn(() => Promise.resolve("data:image/png;base64,AAAA")) },
}));

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

const CODE = "ABCDEFGHJKMNPQRS";
const REF = "VW261123ABCDEFGH01";

function spend(overrides: object = {}) {
  return {
    id: "t1",
    reference: REF,
    type: "TRANSFER",
    status: "CREATED",
    amount: { amount: "250000", currency: "VND" },
    fee: { amount: "0", currency: "VND" },
    total: { amount: "250000", currency: "VND" },
    description: "Tiền cơm",
    counterparty: { kind: "USER", name: "NGUYEN V*** A", wallet_number_masked: "******4821" },
    warnings: ["NEW_RECIPIENT"],
    payment_request_code: null,
    failure_code: null,
    next_action: "CONFIRM",
    quote_expires_at: "2026-11-23T03:10:00Z",
    created_at: "2026-11-23T03:00:00Z",
    completed_at: null,
    events: [
      { from_status: null, to_status: "CREATED", reason_code: null, at: "2026-11-23T03:00:00Z" },
    ],
    ...overrides,
  };
}

function request(overrides: object = {}) {
  return {
    code: CODE,
    url: `http://127.0.0.1:8080/pay/${CODE}`,
    issuer: {
      kind: "MERCHANT",
      name: "Cà phê Mây",
      wallet_number_masked: "******1234",
      mcc: "5814",
    },
    amount: { amount: "150000", currency: "VND" },
    description: "2 cà phê sữa",
    single_use: true,
    status: "OPEN",
    expires_at: "2026-11-23T03:15:00Z",
    created_at: "2026-11-23T03:00:00Z",
    is_mine: false,
    addressed_to_me: false,
    payer_name: null,
    paid_count: 0,
    ...overrides,
  };
}

function api(extra: Parameters<typeof mockFetch>[0] = {}): MockFetch {
  return mockFetch({
    "GET /api/v1/me": json(me),
    "GET /api/v1/notifications/unread-count": json({ unread: 0 }),
    "GET /api/v1/merchant": problem(404, "MERCHANT_NOT_FOUND"),
    "GET /api/v1/recipients": json([]),
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

describe("transfers", () => {
  it("checks the recipient, quotes, confirms with the PIN and opens the result", async () => {
    const done = spend({ status: "COMPLETED", next_action: "NONE", warnings: [] });
    const mock = api({
      "GET /api/v1/recipients/lookup": json({
        wallet_number_masked: "******4821",
        display_name: "NGUYEN V*** A",
        kind: "USER",
        active: true,
        wallet_number: "1234564821",
      }),
      "POST /api/v1/transfers": json(spend(), 201),
      "POST /api/v1/transfers/VW261123ABCDEFGH01/confirm": problem(
        422,
        "PIN_INVALID",
        "PIN giao dịch không đúng",
        { detail: "remaining_attempts=4" },
      ),
      "GET /api/v1/transfers/VW261123ABCDEFGH01": json(done),
    });
    const { router } = renderApp(mock, "/transfer?to=1234564821");
    const quote = await screen.findByRole("button", { name: vi.transfer.quote });
    expect(quote).toBeDisabled(); // nobody checked yet
    expect(screen.getByLabelText(vi.transfer.walletNumber)).toHaveValue("1234564821");
    await userEvent.click(screen.getByRole("button", { name: vi.transfer.check }));
    expect(await screen.findByText("NGUYEN V*** A")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.money.amount), "250000");
    await userEvent.type(screen.getByLabelText(vi.transfer.note), "Tiền cơm");
    await userEvent.click(quote);

    expect(await screen.findByText(vi.spend.warning.NEW_RECIPIENT)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(vi.pin.current), "111111");
    await userEvent.click(screen.getByRole("button", { name: vi.transfer.confirm }));
    expect(await screen.findByText("Còn 4 lần thử.")).toBeInTheDocument();
    mock.on("POST /api/v1/transfers/VW261123ABCDEFGH01/confirm", json(done));
    await userEvent.click(screen.getByRole("button", { name: vi.transfer.confirm }));
    expect(await screen.findByRole("heading", { name: vi.transfer.completed })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/transfers/${REF}`);

    const confirms = mock.calls.filter((c) => c.path.endsWith("/confirm"));
    expect(confirms[0]?.headers.get("Idempotency-Key")).toBe(
      confirms[1]?.headers.get("Idempotency-Key"),
    );
    expect(mock.calls.find((c) => c.path === "/api/v1/transfers")?.body).toEqual({
      wallet_number: "1234564821",
      amount: "250000",
      note: "Tiền cơm",
    });
  });

  it("explains an unavailable recipient and a failed transfer", async () => {
    const failed = spend({
      status: "FAILED",
      next_action: "NONE",
      failure_code: "INSUFFICIENT_BALANCE",
    });
    renderApp(
      api({ "GET /api/v1/transfers/VW261123ABCDEFGH01": json(failed) }),
      `/transfers/${REF}`,
    );
    expect(await screen.findByRole("heading", { name: vi.transfer.failed })).toBeInTheDocument();
    expect(screen.getByText(vi.money.failure.INSUFFICIENT_BALANCE)).toBeInTheDocument();
  });

  it("shows a risk hold with its explanation", async () => {
    const held = spend({ status: "PENDING", next_action: "WAIT_REVIEW", warnings: [] });
    renderApp(api({ "GET /api/v1/transfers/VW261123ABCDEFGH01": json(held) }), `/transfers/${REF}`);
    expect(await screen.findByRole("heading", { name: vi.spend.review_hold })).toBeInTheDocument();
    expect(screen.getByText(vi.spend.reviewBody)).toBeInTheDocument();
  });

  it("sends to a saved recipient", async () => {
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
      "POST /api/v1/transfers": json(spend({ warnings: [] }), 201),
    });
    renderApp(mock, "/transfer");
    await userEvent.click(await screen.findByRole("tab", { name: vi.transfer.by.saved }));
    await userEvent.selectOptions(await screen.findByLabelText(vi.transfer.savedLabel), "r1");
    await userEvent.type(screen.getByLabelText(vi.money.amount), "1000");
    await userEvent.click(screen.getByRole("button", { name: vi.transfer.quote }));
    expect(await screen.findByText(vi.spend.review)).toBeInTheDocument();
    expect(mock.calls.find((c) => c.path === "/api/v1/transfers")?.body).toEqual({
      recipient_id: "r1",
      amount: "1000",
      note: "",
    });
  });
});

describe("QR payments", () => {
  it("shows the verified merchant and pays", async () => {
    const quoted = spend({
      type: "PAYMENT",
      amount: { amount: "150000", currency: "VND" },
      total: { amount: "150000", currency: "VND" },
      counterparty: { kind: "MERCHANT", name: "Cà phê Mây", wallet_number_masked: "******1234" },
      warnings: [],
      payment_request_code: CODE,
    });
    const paid = { ...quoted, status: "COMPLETED", next_action: "NONE" };
    const mock = api({
      [`GET /api/v1/payment-requests/${CODE}`]: json(request()),
      [`POST /api/v1/payment-requests/${CODE}/pay`]: json(quoted, 201),
      "POST /api/v1/payments/VW261123ABCDEFGH01/confirm": json(paid),
      "GET /api/v1/payments/VW261123ABCDEFGH01": json(paid),
    });
    const { router } = renderApp(mock, `/pay/${CODE}`);
    expect(await screen.findByText("Cà phê Mây")).toBeInTheDocument();
    expect(screen.getByText(vi.pay.verified)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: vi.pay.continue }));
    await userEvent.type(await screen.findByLabelText(vi.pin.current), "123456");
    await userEvent.click(screen.getByRole("button", { name: vi.pay.confirm }));
    expect(await screen.findByRole("heading", { name: vi.pay.completed })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/payments/${REF}`);
    expect(mock.calls.find((c) => c.path.endsWith("/pay"))?.body).toEqual({ amount: null });
  });

  it("refuses closed and invalid codes", async () => {
    renderApp(
      api({ [`GET /api/v1/payment-requests/${CODE}`]: json(request({ status: "PAID" })) }),
      `/pay/${CODE}`,
    );
    expect(await screen.findByText(vi.pay.closed.PAID)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: vi.pay.continue })).not.toBeInTheDocument();
  });

  it("asks for the amount of an open-amount code", async () => {
    const mock = api({
      [`GET /api/v1/payment-requests/${CODE}`]: json(request({ amount: null })),
    });
    renderApp(mock, `/pay/${CODE}`);
    const next = await screen.findByRole("button", { name: vi.pay.continue });
    expect(next).toBeDisabled();
    await userEvent.type(screen.getByLabelText(vi.money.amount), "45000");
    expect(next).toBeEnabled();
  });

  it("opens a pasted code from the scanner and rejects foreign links", async () => {
    const mock = api({ [`GET /api/v1/payment-requests/${CODE}`]: json(request()) });
    const { router } = renderApp(mock, "/scan");
    const field = await screen.findByLabelText(vi.scan.code);
    await userEvent.type(field, "https://evil.example/pay/ABCDEFGHJKMNPQRS");
    await userEvent.click(screen.getByRole("button", { name: vi.scan.open }));
    expect(await screen.findByText(vi.scan.invalid)).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, CODE.toLowerCase());
    await userEvent.click(screen.getByRole("button", { name: vi.scan.open }));
    await vitest.waitFor(() => expect(router.state.location.pathname).toBe(`/pay/${CODE}`));
  });

  it("parses only VieWallet targets", () => {
    const origin = "http://127.0.0.1:8080";
    expect(parseScanned(`${origin}/pay/${CODE}`, origin)).toEqual({ kind: "pay", code: CODE });
    expect(parseScanned(`${origin}/transfer?to=1234564821`, origin)).toEqual({
      kind: "transfer",
      to: "1234564821",
    });
    expect(parseScanned("1234564821", origin)).toEqual({ kind: "transfer", to: "1234564821" });
    expect(parseScanned(`${origin}/transfer?to=12`, origin)).toBeNull();
    expect(parseScanned("javascript:alert(1)", origin)).toBeNull();
    expect(parseScanned(`https://other.test/pay/${CODE}`, origin)).toBeNull();
  });
});

describe("requests and the shop", () => {
  it("lists requests to me and creates a request with a QR", async () => {
    const mine = request({
      issuer: { kind: "USER", name: "TRAN T*** L", wallet_number_masked: "******9999", mcc: null },
      is_mine: true,
      payer_name: "NGUYEN V*** A",
    });
    const mock = api({
      "GET /api/v1/payment-requests": (req: Request) =>
        new URL(req.url).searchParams.get("role") === "incoming"
          ? json([request({ addressed_to_me: true })])
          : json([]),
      "POST /api/v1/payment-requests": json(mine, 201),
    });
    renderApp(mock, "/request");
    const incoming = await screen.findByRole("link", { name: vi.request.pay });
    expect(incoming).toHaveAttribute("href", `/pay/${CODE}`);
    await userEvent.type(screen.getByLabelText(vi.money.amount), "120000");
    await userEvent.type(screen.getByLabelText(vi.request.payer), "sai-dinh-dang");
    expect(screen.getByRole("button", { name: vi.request.create })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(vi.request.payer));
    await userEvent.type(screen.getByLabelText(vi.request.payer), "minh@example.com");
    await userEvent.click(screen.getByRole("button", { name: vi.request.create }));
    expect(await screen.findByRole("img", { name: vi.request.qrLabel })).toBeInTheDocument();
    expect(mock.calls.find((c) => c.method === "POST")?.body).toEqual({
      kind: "USER",
      amount: "120000",
      description: "",
      single_use: true,
      payer_email: "minh@example.com",
    });
  });

  it("explains how to get a shop", async () => {
    renderApp(api(), "/merchant");
    expect(await screen.findByText(vi.merchant.none)).toBeInTheDocument();
  });

  it("refunds part of a payment from the shop screen", async () => {
    const payment = {
      reference: REF,
      type: "PAYMENT",
      status: "COMPLETED",
      amount: { amount: "300000", currency: "VND" },
      refunded: { amount: "0", currency: "VND" },
      refundable: { amount: "300000", currency: "VND" },
      payer: { kind: "USER", name: "TRAN T*** L", wallet_number_masked: "******9999" },
      description: "",
      related_reference: null,
      created_at: "2026-11-23T03:00:00Z",
      completed_at: "2026-11-23T03:00:01Z",
    };
    const mock = api({
      "GET /api/v1/merchant": json({
        id: "m1",
        display_name: "Cà phê Mây",
        legal_name: "Công ty Mây (hư cấu)",
        mcc: "5814",
        status: "ACTIVE",
        wallet_number: "5555555555",
        balance: { amount: "300000", currency: "VND" },
      }),
      "GET /api/v1/merchant/payments": json({ items: [payment], next_cursor: null }),
      [`POST /api/v1/merchant/payments/${REF}/refunds`]: json(
        spend({ type: "REFUND", status: "COMPLETED" }),
        201,
      ),
    });
    renderApp(mock, "/merchant");
    expect(await screen.findByRole("heading", { name: "Cà phê Mây" })).toBeInTheDocument();
    expect((await screen.findAllByRole("link", { name: vi.nav.merchant })).length).toBeGreaterThan(
      0,
    );
    await userEvent.click(await screen.findByRole("button", { name: vi.merchant.refund }));
    const dialog = await screen.findByRole("dialog");
    const amount = within(dialog).getByLabelText(vi.money.amount);
    await userEvent.clear(amount);
    await userEvent.type(amount, "400000");
    expect(within(dialog).getByText(vi.merchant.tooMuch)).toBeInTheDocument();
    await userEvent.clear(amount);
    await userEvent.type(amount, "100000");
    await userEvent.type(within(dialog).getByLabelText(vi.merchant.reason), "khách trả món");
    await userEvent.click(within(dialog).getByRole("button", { name: vi.merchant.refund }));
    expect(await screen.findByText(vi.merchant.refunded)).toBeInTheDocument();
    const call = mock.calls.find((c) => c.path.endsWith("/refunds"));
    expect(call?.body).toEqual({ amount: "100000", reason: "khách trả món" });
    expect(call?.headers.get("Idempotency-Key")).toBeTruthy();
  });

  it("shows the personal receive QR", async () => {
    renderApp(
      api({
        "GET /api/v1/wallet/qr": json({
          wallet_number: "1234564821",
          display_name: "TRAN T*** L",
          payload: "http://127.0.0.1:8080/transfer?to=1234564821",
        }),
      }),
      "/receive",
    );
    expect(await screen.findByRole("img", { name: vi.receive.qrLabel })).toBeInTheDocument();
    expect(screen.getByText("1234564821")).toBeInTheDocument();
  });
});
