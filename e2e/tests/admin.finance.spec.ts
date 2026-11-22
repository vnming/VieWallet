import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

import { createStaff, PASSWORD, PIN, sixDigitCode, totp, uniqueEmail, waitForEmail } from "./support";

const WEB_URL = process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080";
const CSRF = { "X-VW-CSRF": "1" };

/** Customer onboarding through the API (the UI flow is covered by web.auth.spec.ts). */
async function customerWithWallet(request: APIRequestContext, email: string): Promise<string> {
  const ok = (status: number) => {
    expect([200, 202, 204]).toContain(status);
  };
  ok(
    (
      await request.post("/api/v1/auth/register", {
        headers: CSRF,
        data: { email, password: PASSWORD, full_name: "Lê Văn Bình" },
      })
    ).status(),
  );
  const code = sixDigitCode(await waitForEmail(email, /Mã xác minh/));
  ok(
    (await request.post("/api/v1/auth/verify-email", { headers: CSRF, data: { email, code } }))
      .status(),
  );
  ok(
    (await request.post("/api/v1/auth/login", { headers: CSRF, data: { email, password: PASSWORD } }))
      .status(),
  );
  ok(
    (await request.post("/api/v1/me/pin", { headers: CSRF, data: { password: PASSWORD, pin: PIN } }))
      .status(),
  );
  const response = await request.get("/api/v1/wallet");
  expect(response.status(), await response.text()).toBe(200);
  const wallet = (await response.json()) as { wallet_number: string };
  return wallet.wallet_number;
}

/** Sign in a new staff member and enroll TOTP through the admin UI. */
async function enrolledStaff(page: Page, roles: string[]): Promise<void> {
  const email = uniqueEmail("finance");
  createStaff(email, roles);
  await page.goto("/login");
  await page.getByLabel("Email nhân viên").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Tiếp tục" }).click();
  const secret = (await page.locator("code").first().textContent())?.trim() ?? "";
  await page.getByLabel("Mã 6 số từ ứng dụng xác thực").fill(totp(secret));
  await page.getByRole("button", { name: "Bật xác thực hai lớp" }).click();
  await page.getByRole("button", { name: "Tôi đã lưu mã, tiếp tục" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tổng quan hệ thống" })).toBeVisible();
}

test("maker proposes a simulated top-up, a second staff member approves, the customer sees it", async ({
  browser,
}) => {
  const customer = await browser.newContext({ baseURL: WEB_URL, locale: "vi-VN" });
  const walletNumber = await customerWithWallet(customer.request, uniqueEmail("binh"));

  const makerContext = await browser.newContext();
  const maker = await makerContext.newPage();
  await enrolledStaff(maker, ["FINANCE"]);
  await maker.getByRole("link", { name: "Điều chỉnh số dư" }).click();
  await maker.getByLabel("Số ví").fill(walletNumber);
  await maker.getByLabel("Số tiền (VND)").fill("250000");
  await maker.getByLabel("Ghi chú nội bộ").fill("Nạp tiền mô phỏng cho demo E2E");
  await maker.getByRole("button", { name: "Gửi đề xuất" }).click();
  await expect(maker.getByText(/Đã tạo đề xuất VW\d{6}[0-9A-Z]{10}/)).toBeVisible();

  // The maker sees the request but cannot approve it.
  await maker.getByRole("link", { name: "Phê duyệt" }).first().click();
  const own = maker.getByRole("article").filter({ hasText: "Nạp tiền mô phỏng cho demo E2E" });
  await expect(own.getByRole("button", { name: "Hủy yêu cầu" })).toBeVisible();
  await expect(own.getByRole("button", { name: "Duyệt và thực hiện" })).toHaveCount(0);

  const checkerContext = await browser.newContext();
  const checker = await checkerContext.newPage();
  await enrolledStaff(checker, ["FINANCE"]);
  await checker.goto("/approvals");
  const card = checker.getByRole("article").filter({ hasText: "Nạp tiền mô phỏng cho demo E2E" });
  await expect(card.getByText("+250.000")).toBeVisible();
  await card.getByRole("button", { name: "Duyệt và thực hiện" }).click();
  await expect(checker.getByText("Đã duyệt và thực hiện")).toBeVisible();

  await checker.getByRole("link", { name: "Đối soát" }).click();
  await checker.getByRole("button", { name: "Chạy đối soát ngay" }).click();
  await expect(checker.getByText("Đối soát xong: 0 vi phạm")).toBeVisible();

  const home = await customer.newPage();
  await home.goto("/");
  const balance = home.getByRole("region", { name: "Số dư khả dụng" });
  await expect(balance.getByText(/250\.000/)).toBeVisible();
  await home.getByRole("link", { name: /Nạp tiền mô phỏng/ }).first().click();
  await expect(home.getByRole("heading", { level: 2, name: "Tiến trình" })).toBeVisible();
  await expect(home.getByText("Thành công").first()).toBeVisible();

  await Promise.all([customer.close(), makerContext.close(), checkerContext.close()]);
});
