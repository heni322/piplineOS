import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signInOperator } from "./totp";

// Full stack: needs the API + seeded demo database (run via scripts/e2e-fullstack.sh).
const EMAIL = process.env.E2E_EMAIL, PASSWORD = process.env.E2E_PASSWORD, SECRET = process.env.E2E_TOTP_SECRET;
test.skip(!EMAIL || !PASSWORD || !SECRET, "full-stack e2e: run scripts/e2e-fullstack.sh");

test("operator reviews the queue: approve, reject and edit, all dry-run", async ({ page }) => {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);
  await expect(page.getByRole("heading", { name: "Operator overview" })).toBeVisible();

  await page.getByRole("link", { name: "Approvals", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Approvals \(5\)/ })).toBeVisible();
  // every sourced claim is highlighted with a numbered link to its source
  await expect(page.locator(".letter mark").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Source 1/ }).first()).toHaveAttribute("href", /^https:\/\//);
  await expect(page.getByText("Registered in England & Wales")).toBeVisible();
  // names are masked until a reason is given
  await expect(page.locator(".queue .meta").first()).toContainText("***");

  const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(a11y.violations).toEqual([]);
  if (process.env.E2E_SCREENSHOT_DIR) {
    await page.setViewportSize({ width: 1360, height: 1100 });
    await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/approvals-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 900 });
    await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/approvals-mobile.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 1360, height: 1100 });
    await page.screenshot({ path: `${process.env.E2E_SCREENSHOT_DIR}/approvals-dark.png` });
    await page.emulateMedia({ colorScheme: "light" });
  }

  // Approve with the keyboard shortcut
  await page.keyboard.press("a");
  await expect(page.getByRole("status").filter({ hasText: "Approved" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Approvals \(4\)/ })).toBeVisible();

  // Reject with a reason
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await page.getByLabel("Reason for rejecting").fill("Not a fit: they run an in-house service desk");
  await page.getByRole("button", { name: "Reject and disqualify lead" }).click();
  await expect(page.getByRole("heading", { name: /Approvals \(3\)/ })).toBeVisible();

  // Edit: requires a logged reason to unmask, then edits are saved and approved
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Why do you need to see the name?").fill("Adjusting the greeting");
  await page.getByRole("button", { name: "Show name and edit" }).click();
  const first = page.locator("textarea").first();
  await expect(first).not.toHaveValue(/\*\*\*/);
  const current = await first.inputValue();
  await first.fill(current.replace(/^Hi /, "Hello "));
  await page.getByRole("button", { name: "Save and approve" }).click();
  await expect(page.getByRole("heading", { name: /Approvals \(2\)/ })).toBeVisible();

  // Nothing real was sent: the overview counts zero real emails
  await page.getByRole("link", { name: "Overview" }).click();
  const real = page.locator(".ledger div").filter({ hasText: "Real emails sent" }).locator("dd");
  await expect(real).toHaveText("0");
});
