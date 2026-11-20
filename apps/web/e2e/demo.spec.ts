import { expect, test } from "@playwright/test";

// The pitch demo, end to end: load the demo → accept the correction → verify → prove the reduction → report → share.
test("C-Trace demo flow", async ({ page, browser }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Trace every tonne." })).toBeVisible();
  await page.getByRole("button", { name: /Try the live demo/ }).click();
  await expect(page).toHaveURL(/\/app\/dashboard/, { timeout: 60_000 });
  await expect(page.getByText("Total Scope 1 + 2")).toBeVisible();
  await expect(page.getByText(/Live demo sandbox/)).toBeVisible();

  // bills: every bill behind the total is listed and opens with what was read
  await page.getByRole("link", { name: /See every bill/ }).click();
  await expect(page.getByText("65 of 65 bills")).toBeVisible();
  await expect(page.getByText("15,297.9 tCO₂e")).toBeVisible();
  await page.getByRole("row", { name: /2025-09_natural_gas\.pdf/ }).click();
  await expect(page.getByText("What C-Trace read")).toBeVisible();
  await expect(page.getByText(/Implied tariff/).first()).toBeVisible();

  // review: the dropped-digit bill comes first, with a suggestion that needs an explicit accept
  await page.goto("/app/review");
  await expect(page.getByText("2025-09_natural_gas.pdf").first()).toBeVisible();
  await expect(page.getByText(/Implied tariff 315.00 BDT\/m3/)).toBeVisible();
  await page.getByRole("button", { name: "Accept correction" }).click();
  await expect(page.getByRole("button", { name: "Accept correction" })).toHaveCount(0);
  await page.getByRole("button", { name: "Confirm all clean bills" }).click();
  await expect(page.getByText(/64 confirmed · 0 left to review/)).toBeVisible();

  // verify: after the correction only the missing diesel month stays Low
  await page.goto("/app/verify");
  await page.getByRole("button", { name: "Run verification" }).first().click();
  await expect(page.getByText("Low — human review")).toBeVisible();
  await expect(page.getByRole("button", { name: "2026-07 Low confidence" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "2025-09 High confidence" }).first()).toBeVisible();

  // reductions: boiler retrofit and solar both verified; sign the boiler proof
  await page.goto("/app/reductions");
  await page.getByRole("button", { name: "Compute reductions" }).first().click();
  await expect(page.getByText("Boiler economiser + steam-trap repair")).toBeVisible();
  await expect(page.getByText("Verified", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "Sign off" }).first().click();
  await page.getByLabel(/What did you check before signing/).fill("Checked baseline bills and the economiser commissioning report");
  await page.getByRole("button", { name: "Sign reduction" }).click();
  await expect(page.getByText(/Signed \d/)).toBeVisible();

  // reports: buyer report → share link → a logged-out viewer sees it
  await page.goto("/app/reports");
  await page.getByRole("button", { name: "Generate report" }).click();
  await expect(page.getByRole("heading", { name: /Supplier Carbon Disclosure/ })).toBeVisible();
  await page.getByRole("button", { name: "Create link" }).click();
  const link = await page.locator("code").first().textContent();
  expect(link).toMatch(/\/share\/[A-Za-z0-9_-]{40,}$/);
  const viewer = await (await browser.newContext()).newPage();
  await viewer.goto(link!);
  await expect(viewer.getByRole("heading", { name: /Supplier Carbon Disclosure/ })).toBeVisible();
  await expect(viewer.getByText("Shared, read-only report")).toBeVisible();
  await expect(viewer.getByText(/Verified reductions/)).toBeVisible();
});

test("Copilot answers with citations (EN and BN)", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Try the live demo/ }).click();
  await expect(page).toHaveURL(/\/app\/dashboard/, { timeout: 60_000 });
  await page.goto("/app/copilot");
  await page.getByRole("button", { name: "Why did emissions change in July 2026?" }).click();
  await expect(page.getByRole("link", { name: /^CT-2026-07-/ }).first()).toBeVisible();
  await page.getByLabel("Your question").fill("জুলাই ২০২৬-এ নির্গমন কেন বেড়েছে?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText(/নির্গমন ছিল/)).toBeVisible();
});
