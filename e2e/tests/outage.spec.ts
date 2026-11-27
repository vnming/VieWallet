/**
 * Journey 10 switches the shared simulator to OUTAGE, so it runs in its own project after every
 * other project has finished (playwright.config.ts dependencies).
 */
import { expect, test } from "@playwright/test";

import { enrolledStaff, idem, providerCard, signedIn } from "./staff";
import { createCustomer, resetRateLimits, uniqueEmail } from "./support";

test.describe.configure({ timeout: 180_000 });
test.beforeEach(() => {
  resetRateLimits();
});

test("journey 10: a provider outage shows up and is recovered from the panel", async ({ page }) => {
  const email = uniqueEmail("outage");
  createCustomer(email, "Nguyễn Văn An");
  const buyer = await signedIn(email);
  await enrolledStaff(page, ["OPERATIONS"]);

  await page.goto("/simulator");
  await page.getByLabel("Chế độ mới").selectOption("OUTAGE");
  await page.getByRole("button", { name: "Áp dụng" }).click();
  await page.getByLabel("Lý do (ghi audit)").fill("Diễn tập sự cố provider");
  await page.getByRole("dialog").getByRole("button", { name: "Xác nhận" }).click();
  await expect(page.getByText("Đã thực hiện")).toBeVisible();

  try {
    const failed = (await (
      await buyer.post("/api/v1/deposits", { headers: idem(), data: { amount: "100000" } })
    ).json()) as { status: string; failure_code: string };
    expect([failed.status, failed.failure_code]).toEqual(["FAILED", "PROVIDER_UNAVAILABLE"]);

    await page.goto("/system");
    const provider = providerCard(page);
    await expect(provider.getByText("Ngừng")).toBeVisible();
    await expect(provider.getByText("OUTAGE")).toBeVisible();
  } finally {
    // Always restore the simulator: later tests depend on it.
    await page.goto("/simulator");
    await page.getByLabel("Chế độ mới").selectOption("NORMAL");
    await page.getByRole("button", { name: "Áp dụng" }).click();
    await page.getByLabel("Lý do (ghi audit)").fill("Kết thúc diễn tập");
    await page.getByRole("dialog").getByRole("button", { name: "Xác nhận" }).click();
    await expect(page.getByText("Đã thực hiện")).toBeVisible();
  }
  await page.goto("/system");
  await expect(providerCard(page).getByText("NORMAL")).toBeVisible();
  await page.goto("/operations");
  await expect(page.getByRole("heading", { level: 1, name: "Hàng đợi vận hành" })).toBeVisible();
  await buyer.dispose();
});
