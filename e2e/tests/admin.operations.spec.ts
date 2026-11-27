import { expect, test } from "@playwright/test";

import { enrolledStaff, fund, idem, secondStaff, signedIn } from "./staff";
import { createCustomer, createMerchant, PIN, resetRateLimits, uniqueEmail } from "./support";

test.describe.configure({ timeout: 180_000, mode: "serial" });
test.beforeEach(() => {
  resetRateLimits();
});

test("journey 9: a large refund is proposed by Operations and approved by Finance", async ({
  page,
  browser,
}) => {
  const shopEmail = uniqueEmail("shop9");
  createMerchant(shopEmail, "Tiệm Sách Gió E2E", "Lê Thu Gió");
  const shop = await signedIn(shopEmail);
  const customerEmail = uniqueEmail("buyer9");
  createCustomer(customerEmail, "Phạm Minh Châu");
  const buyer = await signedIn(customerEmail);
  await fund(buyer, "500000");

  const ask = (await (
    await shop.post("/api/v1/payment-requests", {
      headers: idem(),
      data: { kind: "MERCHANT", amount: "300000", description: "Bộ sách" },
    })
  ).json()) as { code: string };
  const quote = (await (
    await buyer.post(`/api/v1/payment-requests/${ask.code}/pay`, {
      headers: idem(),
      data: { amount: null },
    })
  ).json()) as { reference: string };
  const paid = await buyer.post(`/api/v1/payments/${quote.reference}/confirm`, {
    headers: idem(),
    data: { pin: PIN },
  });
  expect(((await paid.json()) as { status: string }).status).toBe("COMPLETED");

  // Operations proposes the refund from the transaction profile.
  await enrolledStaff(page, ["OPERATIONS"]);
  await page.goto(`/transactions/${quote.reference}`);
  await page.getByRole("button", { name: "Đề xuất hoàn tiền" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Số tiền (VND)").fill("200000");
  await dialog.getByLabel("Lý do").fill("Khách trả lại hai cuốn bị lỗi in");
  await dialog.getByRole("button", { name: "Gửi đề xuất" }).click();
  await expect(page.getByText("Đã gửi đề xuất hoàn tiền")).toBeVisible();

  // Finance sees the request with its numbers and approves it.
  const finance = await secondStaff(browser, ["FINANCE"]);
  await finance.goto("/approvals");
  const card = finance.getByRole("article").filter({ hasText: quote.reference });
  await expect(card.getByText("Khách trả lại hai cuốn bị lỗi in")).toBeVisible();
  await card.getByRole("button", { name: "Duyệt và thực hiện" }).click();
  await expect(finance.getByText("Đã duyệt và thực hiện")).toBeVisible();

  const history = (await (await buyer.get("/api/v1/transactions?type=REFUND")).json()) as {
    items: { status: string; amount: { amount: string } }[];
  };
  expect(history.items.map((i) => [i.status, i.amount.amount])).toEqual([["COMPLETED", "200000"]]);
  await finance.goto(`/transactions/${quote.reference}`);
  await expect(finance.getByText("PARTIALLY_REFUNDED", { exact: true })).toBeVisible();
  await Promise.all([shop.dispose(), buyer.dispose(), finance.context().close()]);
});
