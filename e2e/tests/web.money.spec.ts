import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

import { PASSWORD, PIN, setKycTier, sixDigitCode, uniqueEmail, waitForEmail } from "./support";

const CSRF = { "X-VW-CSRF": "1" };

/** Sign-up through the API (the UI path is covered in web.auth.spec.ts); the page shares cookies. */
async function signedInCustomer(page: Page, request: APIRequestContext): Promise<string> {
  const email = uniqueEmail("money");
  await request.post("/api/v1/auth/register", {
    headers: CSRF,
    data: { email, password: PASSWORD, full_name: "Phạm Minh Châu" },
  });
  const code = sixDigitCode(await waitForEmail(email, /Mã xác minh/));
  await request.post("/api/v1/auth/verify-email", { headers: CSRF, data: { email, code } });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await page.getByLabel("PIN mới").fill(PIN);
  await page.getByLabel("Nhập lại PIN").fill(PIN);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Đặt PIN" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Xin chào/ })).toBeVisible();
  return email;
}

async function deposit(page: Page, amount: string, approve = true): Promise<void> {
  await page.goto("/deposit");
  await page.getByLabel("Số tiền (₫)").fill(amount);
  await page.getByRole("button", { name: "Tiếp tục tới SimBank" }).click();
  await expect(page.getByRole("heading", { name: "Xác nhận thanh toán" })).toBeVisible();
  await expect(page.getByText("SimBank — ngân hàng mô phỏng")).toBeVisible();
  await page
    .getByRole("button", { name: approve ? "Duyệt thanh toán" : "Từ chối" })
    .click();
  await expect(page).toHaveURL(/\/deposits\/VW\d{6}[0-9A-Z]{10}$/);
}

test.describe.configure({ timeout: 120_000 });

test("journey 3: deposit succeeds, is declined, and completes later", async ({ page }) => {
  await signedInCustomer(page, page.request);

  await deposit(page, "500000");
  await expect(page.getByRole("heading", { level: 1, name: "Nạp tiền thành công" })).toBeVisible({
    timeout: 20_000,
  });

  await deposit(page, "100000", false);
  await expect(page.getByRole("heading", { level: 1, name: "Nạp tiền không thành công" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText("Bạn đã từ chối thanh toán tại ngân hàng.")).toBeVisible();

  // Scenario 222: the bank confirms later (shortened to a few seconds in E2E).
  await deposit(page, "1222000");
  await expect(page.getByRole("heading", { level: 1, name: "Đang chờ ngân hàng xác nhận" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Nạp tiền thành công" })).toBeVisible({
    timeout: 45_000,
  });

  await page.goto("/");
  const balance = page.getByRole("region", { name: "Số dư khả dụng" });
  await expect(balance.getByText(/1\.722\.000/)).toBeVisible();
  await page.getByRole("link", { name: /thông báo chưa đọc/ }).click();
  await expect(page.getByText("Nạp tiền thành công").first()).toBeVisible();
});

test("journey 7: withdrawal succeeds, fails with refund, and a timeout is resolved", async ({
  page,
}) => {
  const email = await signedInCustomer(page, page.request);
  await deposit(page, "5000000");
  await expect(page.getByRole("heading", { level: 1, name: "Nạp tiền thành công" })).toBeVisible({
    timeout: 20_000,
  });

  await page.goto("/withdraw");
  await expect(page.getByText("Cần xác minh danh tính (KYC) để rút tiền")).toBeVisible();
  setKycTier(email, 1);

  await page.goto("/wallet/destinations");
  await page.getByLabel("Số tài khoản").fill("0123456789");
  await page.getByLabel("Tên chủ tài khoản").fill("Pham Minh Chau");
  await page.getByRole("button", { name: "Lưu tài khoản" }).click();
  await expect(page.getByText(/••••6789/)).toBeVisible();

  async function withdraw(amount: string): Promise<void> {
    await page.goto("/withdraw");
    await page.getByLabel("Số tiền (₫)").fill(amount);
    await page.getByRole("button", { name: "Xem phí và tổng tiền" }).click();
    await expect(page.getByText("1.100 ₫", { exact: true })).toBeVisible();
    await page.getByLabel("PIN hiện tại").fill(PIN);
    await page.getByRole("button", { name: "Xác nhận rút tiền" }).click();
    await expect(page).toHaveURL(/\/withdrawals\/VW/, { timeout: 15_000 });
  }

  await withdraw("1000000");
  await expect(page.getByRole("heading", { level: 1, name: "Rút tiền thành công" })).toBeVisible({
    timeout: 30_000,
  });

  await withdraw("1111000"); // scenario 111: the bank refuses
  await expect(page.getByRole("heading", { level: 1, name: "Rút tiền không thành công" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("Tiền đã được hoàn về ví.")).toBeVisible();

  await withdraw("1333000"); // scenario 333: the answer is too slow -> UNKNOWN -> resolved later
  await expect(async () => {
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Rút tiền thành công" })).toBeVisible({
      timeout: 5_000,
    });
  }).toPass({ timeout: 90_000, intervals: [5_000] });

  await page.goto("/");
  // 5 000 000 - (1 000 000 + 1 100) - (1 333 000 + 1 100); the failed one was refunded.
  await expect(page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/2\.664\.800/)).toBeVisible();
});
