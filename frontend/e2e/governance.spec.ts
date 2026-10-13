import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signInOperator, totp } from "./totp";

// Full stack after `demo conversations` + `demo billing` (scripts/e2e-fullstack.sh, stage 2).
const EMAIL = process.env.E2E_EMAIL, PASSWORD = process.env.E2E_PASSWORD, SECRET = process.env.E2E_TOTP_SECRET;
test.skip(!EMAIL || !PASSWORD || !SECRET || process.env.E2E_STAGE !== "conversations", "full-stack stage: run scripts/e2e-fullstack.sh");

async function accessible(page: Page, name: string) {
  const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(a11y.violations).toEqual([]);
  const dir = process.env.E2E_SCREENSHOT_DIR;
  if (dir) {
    await page.setViewportSize({ width: 1360, height: 1000 });
    await page.screenshot({ path: `${dir}/${name}-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 900 });
    await page.screenshot({ path: `${dir}/${name}-mobile.png`, fullPage: true });
    await page.setViewportSize({ width: 1360, height: 1000 });
  }
}

test("governance: keys off, checklist unsigned, a country needs a solicitor's review before it can be enabled", async ({ page }) => {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);
  await page.getByRole("link", { name: "Go-live" }).click();
  await expect(page.getByRole("heading", { name: "Go-live and governance" })).toBeVisible();
  await expect(page.locator(".keys li")).toHaveCount(3);
  await expect(page.locator(".keys .key-state").filter({ hasText: "On" })).toHaveCount(0);
  await expect(page.getByText(/0 of \d+ checklist items signed off/)).toBeVisible();

  const ireland = page.getByRole("row", { name: /Ireland/ });
  await expect(ireland).toContainText("needs your solicitor's review first");
  await expect(ireland.getByRole("button", { name: "Enable" })).toHaveCount(0);
  await accessible(page, "governance");

  await ireland.getByRole("button", { name: "Record solicitor review" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Solicitor and firm").fill("A. Solicitor, Example LLP");
  await dialog.getByLabel("Authenticator code").fill("000000");
  await dialog.getByRole("button", { name: "Save review" }).click();
  await expect(page.locator("p.error[role=alert]")).toContainText("authenticator code didn't work");
  await dialog.getByLabel("Authenticator code").fill(totp(SECRET!));
  await dialog.getByRole("button", { name: "Save review" }).click();
  await expect(page.locator("p.status")).toContainText("Solicitor review recorded for Ireland");
  await expect(ireland.getByRole("button", { name: "Enable" })).toBeVisible();
  // Not enabled here: enabling is the owner's decision; the API test covers it.
});

test("leads are masked until a reason is given; inboxes show health and can be paused", async ({ page }) => {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);
  await page.getByRole("link", { name: "Leads" }).click();
  await expect(page.getByRole("heading", { name: "Leads" })).toBeVisible();
  const contoso = page.getByRole("row", { name: /Contoso Managed IT Ltd/ }).first();
  await expect(contoso).toContainText("*");
  await accessible(page, "leads");
  await contoso.getByRole("button", { name: "Show details" }).click();
  await page.getByLabel(/Why do you need to see this/).fill("Preparing the meeting brief");
  await page.getByRole("dialog").getByRole("button", { name: "Show" }).click();
  await expect(page.getByText("Your view was logged.")).toBeVisible();
  await expect(contoso).toContainText("@");
  await contoso.getByRole("button", { name: "Compliance decisions" }).click();
  await expect(page.locator(".reasons").first()).toContainText("allow");

  await page.getByRole("link", { name: "Inboxes" }).click();
  await expect(page.getByRole("heading", { name: "Inboxes" })).toBeVisible();
  await expect(page.getByText("not enough live sends yet").first()).toBeVisible();
  await accessible(page, "inboxes");
  await page.getByRole("button", { name: "Pause" }).first().click();
  await page.getByLabel(/Why are you pausing it/).fill("Checking DNS records after a change");
  await page.getByRole("dialog").getByRole("button", { name: "Pause" }).click();
  await expect(page.locator("p.status")).toContainText("paused");
  await page.getByRole("button", { name: "Resume" }).first().click();
  await page.getByLabel(/What was fixed/).fill("DNS verified, all records correct");
  await page.getByRole("dialog").getByRole("button", { name: "Resume" }).click();
  await expect(page.locator("p.status")).toContainText("resumed");

  await page.getByRole("link", { name: "Overview" }).click();
  await page.getByRole("button", { name: "Send approved emails (dry-run)" }).click();
  await expect(page.locator("p.status")).toContainText("Done:");
});
