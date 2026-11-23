import { type APIRequestContext, type Browser, type Page, expect, test } from "@playwright/test";

import {
  createCustomer,
  createStaff,
  PASSWORD,
  PIN,
  resetRateLimits,
  setKycTier,
  totp,
  uniqueEmail,
} from "./support";

const WEB_URL = process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080";
const CSRF = { "X-VW-CSRF": "1" };

// A 1x1 PNG (fictional "document photo").
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

/** A few random letters: names must be words, and unique across runs on the dev database. */
function randomName(): string {
  const letters = "abcdeghiklmnopqrstuvxy";
  const word = Array.from({ length: 6 }, () => letters[Math.floor(Math.random() * letters.length)]);
  return word.join("").replace(/^./, (c) => c.toUpperCase());
}

test.describe.configure({ timeout: 150_000 });
test.beforeEach(() => {
  resetRateLimits();
});

async function signedInCustomer(browser: Browser, fullName: string) {
  const email = uniqueEmail("kyc");
  createCustomer(email, fullName);
  const context = await browser.newContext({ baseURL: WEB_URL, locale: "vi-VN" });
  const request: APIRequestContext = context.request;
  expect(
    (
      await request.post("/api/v1/auth/login", {
        headers: CSRF,
        data: { email, password: PASSWORD },
      })
    ).status(),
  ).toBe(200);
  await request.post("/api/v1/me/pin", { headers: CSRF, data: { password: PASSWORD, pin: PIN } });
  const wallet = (await (await request.get("/api/v1/wallet")).json()) as { wallet_number: string };
  return { email, context, page: await context.newPage(), walletNumber: wallet.wallet_number };
}

/** Sign in a new staff member and enroll TOTP through the admin UI. */
async function enrolledStaff(page: Page, roles: string[]): Promise<void> {
  const email = uniqueEmail("staff");
  createStaff(email, roles);
  await page.goto("/login");
  await page.getByLabel("Email nhân viên").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Tiếp tục" }).click();
  await expect(page.locator("code").first()).toHaveText(/^[A-Z2-7]{16,}$/);
  const secret = (await page.locator("code").first().textContent())?.trim() ?? "";
  await page.getByLabel("Mã 6 số từ ứng dụng xác thực").fill(totp(secret));
  await page.getByRole("button", { name: "Bật xác thực hai lớp" }).click();
  await page.getByRole("button", { name: "Tôi đã lưu mã, tiếp tục" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tổng quan hệ thống" })).toBeVisible();
}

test("journey 2: a customer submits KYC and a reviewer approves it", async ({ page, browser }) => {
  // Unique per run: the dev database keeps submissions left by earlier (interrupted) runs.
  const fullName = `Phạm Minh ${randomName()}`;
  const lan = await signedInCustomer(browser, fullName);
  const web = lan.page;
  await web.goto("/kyc");
  await expect(web.getByText("Xác minh mô phỏng")).toBeVisible();
  await web.getByLabel("Họ và tên").fill(fullName);
  await web.getByLabel("Ngày sinh").fill("1994-08-20");
  await web.getByLabel("Số giấy tờ").fill(String(Date.now()).padStart(12, "0").slice(-12));
  await web.getByLabel("Địa chỉ").fill("12 Đường Hư Cấu, Quận Mô Phỏng");
  await web.getByRole("button", { name: "Lưu thông tin" }).click();
  for (const label of ["Mặt trước", "Mặt sau", "Ảnh chân dung"]) {
    await web.getByLabel(label, { exact: true }).setInputFiles({
      name: "gia-lap.png",
      mimeType: "image/png",
      buffer: PNG,
    });
  }
  await expect(web.getByText("Đã tải")).toHaveCount(3);
  await web.getByRole("button", { name: "Gửi hồ sơ" }).click();
  await expect(web.getByRole("heading", { name: "Hồ sơ đang chờ duyệt" })).toBeVisible();

  await enrolledStaff(page, ["KYC_REVIEWER"]);
  await page.getByRole("link", { name: "Duyệt KYC" }).click();
  await page.getByRole("button", { name: new RegExp(fullName) }).click();
  await page.getByRole("button", { name: "Nhận hồ sơ" }).click();
  await expect(page.getByRole("img", { name: "Mặt trước" })).toBeVisible();
  await page.getByRole("button", { name: "Duyệt (lên cấp 1)" }).click();
  await expect(page.getByText("Đã duyệt hồ sơ")).toBeVisible();

  await web.goto("/kyc");
  await expect(web.getByRole("heading", { name: "Đã xác minh (cấp 1)" })).toBeVisible();
  await web.goto("/wallet/limits");
  await expect(web.getByText("Cấp xác minh: 1")).toBeVisible();
  await expect(async () => {
    await web.goto("/notifications");
    await expect(web.getByText("Xác minh danh tính thành công").first()).toBeVisible({
      timeout: 2_000,
    });
  }).toPass({ timeout: 30_000 });
  await lan.context.close();
});

test("journey 8: a large transfer is held for review and an analyst approves it", async ({
  page,
  browser,
}) => {
  const lan = await signedInCustomer(browser, "Phạm Minh Châu");
  const minh = await signedInCustomer(browser, "Nguyễn Văn An");
  setKycTier(lan.email, 1);
  const web = lan.page;
  await web.goto("/deposit");
  await web.getByLabel("Số tiền (₫)").fill("7000000");
  await web.getByRole("button", { name: "Tiếp tục tới SimBank" }).click();
  await web.getByRole("button", { name: "Duyệt thanh toán" }).click();
  await expect(web.getByRole("heading", { level: 1, name: "Nạp tiền thành công" })).toBeVisible({
    timeout: 20_000,
  });

  // New account + PIN just set + first transfer to this person, 6 000 000 ₫: REVIEW.
  await web.goto(`/transfer?to=${minh.walletNumber}`);
  await web.getByRole("button", { name: "Kiểm tra", exact: true }).click();
  await web.getByLabel("Số tiền (₫)").fill("6000000");
  await web.getByRole("button", { name: "Xem phí và xác nhận" }).click();
  await web.getByLabel("PIN hiện tại").fill(PIN);
  await web.getByRole("button", { name: "Xác nhận chuyển" }).click();
  await expect(web.getByRole("heading", { level: 1, name: "Đang kiểm tra an toàn" })).toBeVisible();

  await enrolledStaff(page, ["RISK_ANALYST"]);
  await page.getByRole("link", { name: "Rủi ro" }).click();
  await page.getByRole("button", { name: "Duyệt", exact: true }).first().click();
  await page.getByRole("button", { name: "Xác nhận" }).click();
  await expect(page.getByText("Đã duyệt, tiền tiếp tục tới người nhận")).toBeVisible();

  await expect(web.getByRole("heading", { level: 1, name: "Chuyển tiền thành công" })).toBeVisible({
    timeout: 20_000,
  });
  await minh.page.goto("/");
  await expect(
    minh.page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/6\.000\.000/),
  ).toBeVisible();
  await Promise.all([lan.context.close(), minh.context.close()]);
});
