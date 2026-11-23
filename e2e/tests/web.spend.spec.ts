import {
  type APIRequestContext,
  type BrowserContext,
  type Page,
  expect,
  test,
} from "@playwright/test";

import {
  createCustomer,
  createMerchant,
  PASSWORD,
  PIN,
  resetRateLimits,
  uniqueEmail,
} from "./support";

const CSRF = { "X-VW-CSRF": "1" };

/** A verified customer (CLI: sign-up is journey 1 and rate limited); the page shares cookies. */
async function customer(request: APIRequestContext, fullName: string): Promise<string> {
  const email = uniqueEmail("spend");
  createCustomer(email, fullName);
  await signIn(request, email);
  const wallet = (await (await request.get("/api/v1/wallet")).json()) as { wallet_number: string };
  return wallet.wallet_number;
}

async function signIn(request: APIRequestContext, email: string): Promise<void> {
  const login = await request.post("/api/v1/auth/login", {
    headers: CSRF,
    data: { email, password: PASSWORD },
  });
  expect(login.status(), await login.text()).toBe(200);
  const pin = await request.post("/api/v1/me/pin", {
    headers: CSRF,
    data: { password: PASSWORD, pin: PIN },
  });
  expect([204, 409]).toContain(pin.status());
}

/** Fund through SimBank (journey 3 covers this page in detail). */
async function deposit(page: Page, amount: string): Promise<void> {
  await page.goto("/deposit");
  await page.getByLabel("Số tiền (₫)").fill(amount);
  await page.getByRole("button", { name: "Tiếp tục tới SimBank" }).click();
  await page.getByRole("button", { name: "Duyệt thanh toán" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Nạp tiền thành công" })).toBeVisible({
    timeout: 20_000,
  });
}

async function startTransfer(page: Page, walletNumber: string, amount: string): Promise<void> {
  await page.goto(`/transfer?to=${walletNumber}`);
  await page.getByRole("button", { name: "Kiểm tra", exact: true }).click();
  await page.getByLabel("Số tiền (₫)").fill(amount);
}

const WEB_URL = process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080";

async function newContext(browser: {
  newContext: (options: { baseURL: string; locale: string }) => Promise<BrowserContext>;
}) {
  const context = await browser.newContext({ baseURL: WEB_URL, locale: "vi-VN" });
  return { context, page: await context.newPage() };
}

test.describe.configure({ timeout: 120_000 });

// Every journey signs in several people from the same IP; real users do not share one.
// Clearing the counters keeps the per-IP login limit (20 / 15 min) from failing other specs.
test.beforeEach(() => {
  resetRateLimits();
});

test("journey 4: transfer to another user who is notified", async ({ page, browser }) => {
  await customer(page.request, "Phạm Minh Châu");
  const other = await newContext(browser);
  const minhWallet = await customer(other.page.request, "Nguyễn Văn An");
  await deposit(page, "500000");

  await startTransfer(page, minhWallet, "120000");
  await expect(page.getByText("NGUYEN V*** A")).toBeVisible();
  await page.getByLabel("Nội dung (không bắt buộc)").fill("Tiền cơm trưa");
  await page.getByRole("button", { name: "Xem phí và xác nhận" }).click();
  await expect(page.getByText("Lần đầu chuyển cho người này", { exact: false })).toBeVisible();
  await page.getByLabel("PIN hiện tại").fill(PIN);
  await page.getByRole("button", { name: "Xác nhận chuyển" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Chuyển tiền thành công" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/transfers\/VW\d{6}[0-9A-Z]{10}$/);
  const reference = page.url().split("/").pop() ?? "";

  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/380\.000/),
  ).toBeVisible();

  // The receiver: balance, in-app notification, history and the detail page.
  await other.page.goto("/");
  await expect(
    other.page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/120\.000/),
  ).toBeVisible();
  await expect(async () => {
    await other.page.goto("/notifications");
    await expect(other.page.getByText("Bạn nhận được tiền").first()).toBeVisible({
      timeout: 2_000,
    });
  }).toPass({ timeout: 30_000 });
  await other.page.goto(`/history/${reference}`);
  await expect(other.page.getByText("PHAM M*** C")).toBeVisible();
  await expect(other.page.getByText("Tiền cơm trưa")).toBeVisible();
  await other.context.close();
});

test("journey 5: transfers blocked by the limit, the PIN lock and a frozen wallet", async ({
  page,
  browser,
}) => {
  await customer(page.request, "Phạm Minh Châu");
  const other = await newContext(browser);
  const minhWallet = await customer(other.page.request, "Nguyễn Văn An");
  await other.context.close();
  await deposit(page, "3000000");

  // Tier 0: at most 2 000 000 per transfer.
  await startTransfer(page, minhWallet, "2500000");
  await page.getByRole("button", { name: "Xem phí và xác nhận" }).click();
  await expect(page.getByText("Vượt hạn mức mỗi giao dịch")).toBeVisible();

  // Five wrong PINs lock the PIN; the right one is then refused too.
  await startTransfer(page, minhWallet, "10000");
  await page.getByRole("button", { name: "Xem phí và xác nhận" }).click();
  for (let attempt = 0; attempt < 5; attempt += 1) {
    await page.getByLabel("PIN hiện tại").fill("135790");
    await page.getByRole("button", { name: "Xác nhận chuyển" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
  }
  await expect(page.getByText("PIN giao dịch đang bị khóa")).toBeVisible();
  await page.getByLabel("PIN hiện tại").fill(PIN);
  await page.getByRole("button", { name: "Xác nhận chuyển" }).click();
  await expect(page.getByText("PIN giao dịch đang bị khóa")).toBeVisible();

  // A frozen wallet sends nothing.
  const frozen = await page.request.post("/api/v1/wallet/freeze", { headers: CSRF, data: {} });
  expect(frozen.status()).toBe(200);
  await startTransfer(page, minhWallet, "10000");
  await page.getByRole("button", { name: "Xem phí và xác nhận" }).click();
  await expect(page.getByText("Ví của bạn đang bị khóa")).toBeVisible();

  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/3\.000\.000/),
  ).toBeVisible();
});

test("journey 6: QR payment to a shop, partial refund, the customer sees it", async ({
  page,
  browser,
}) => {
  const shopEmail = uniqueEmail("shop");
  createMerchant(shopEmail, "Cà phê Mây E2E", "Lê Thu Mây");
  const shop = await newContext(browser);
  await signIn(shop.page.request, shopEmail);
  await shop.page.goto("/merchant");
  await expect(shop.page.getByRole("heading", { level: 1, name: "Cà phê Mây E2E" })).toBeVisible();
  await shop.page.getByLabel("Số tiền (₫)").fill("150000");
  await shop.page.getByLabel("Nội dung (không bắt buộc)").fill("2 cà phê sữa");
  await shop.page.getByRole("button", { name: "Tạo mã QR" }).click();
  await expect(shop.page.getByRole("img", { name: "Mã QR thanh toán của cửa hàng" })).toBeVisible();
  const link = (await shop.page.getByText(/\/pay\/[0-9A-Z]{16}$/).textContent()) ?? "";

  await customer(page.request, "Phạm Minh Châu");
  await deposit(page, "500000");
  // The camera is not available headless: the scanner's manual entry takes the same link.
  await page.goto("/scan");
  await page.getByLabel("Mã thanh toán, số ví hoặc liên kết").fill(link);
  await page.getByRole("button", { name: "Mở", exact: true }).click();
  await expect(page.getByText("Cà phê Mây E2E")).toBeVisible();
  await expect(page.getByText("Đã xác minh")).toBeVisible();
  await page.getByRole("button", { name: "Tiếp tục" }).click();
  await page.getByLabel("PIN hiện tại").fill(PIN);
  await page.getByRole("button", { name: "Xác nhận thanh toán" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Thanh toán thành công" }),
  ).toBeVisible();

  // The shop refunds part of it.
  await shop.page.reload();
  await shop.page.getByRole("button", { name: "Hoàn tiền" }).click();
  const dialog = shop.page.getByRole("dialog");
  await dialog.getByLabel("Số tiền (₫)").fill("50000");
  await dialog.getByLabel("Lý do").fill("Hết món bánh");
  await dialog.getByRole("button", { name: "Hoàn tiền" }).click();
  await expect(shop.page.getByText("Đã hoàn tiền cho khách")).toBeVisible();
  await expect(shop.page.getByText("Hoàn một phần").first()).toBeVisible();
  await shop.context.close();

  await page.goto("/history");
  await expect(page.getByText("Hoàn tiền: Hết món bánh").first()).toBeVisible();
  await page.goto("/");
  // 500 000 - 150 000 + 50 000
  await expect(
    page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/400\.000/),
  ).toBeVisible();
});
