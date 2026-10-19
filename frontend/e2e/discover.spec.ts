import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signInOperator } from "./totp";

// Full stack after `demo conversations` + `demo billing` (scripts/e2e-fullstack.sh, stage 2). No worker and no
// Google key here: a search is recorded, and the page must say plainly that Google is not called.
const EMAIL = process.env.E2E_EMAIL, PASSWORD = process.env.E2E_PASSWORD, SECRET = process.env.E2E_TOTP_SECRET;
test.skip(!EMAIL || !PASSWORD || !SECRET || process.env.E2E_STAGE !== "conversations", "full-stack stage: run scripts/e2e-fullstack.sh");

test("find companies: the page explains why Google is not called, and a search is recorded, not lost", async ({ page }) => {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);
  await page.getByRole("link", { name: "Find companies" }).click();
  await expect(page.getByRole("heading", { name: "Find companies", level: 1 })).toBeVisible();
  // one of the setup notes is always shown above the form; in this environment Google is never reachable
  await expect(page.getByRole("note")).toContainText(/FREE_ONLY|No Google Places key|Dry-run/);
  await expect(page.getByText(/Google Places searches this month: 0 of \d+\./)).toBeVisible();
  await expect(page.getByText("No searches yet.")).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(accessibility.violations).toEqual([]);

  await page.getByLabel("Activity and area").fill("agence web Lyon");
  await page.getByLabel("Country").selectOption("FR");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Search started." })).toBeVisible();  // not the dry-run banner
  const search = page.getByRole("region", { name: "agence web Lyon (FR)" });
  await expect(search).toBeVisible();
  // without a worker it waits; without the job queue it fails with that reason. Never silently gone.
  await expect(search).toContainText(/Waiting|job queue|FREE_ONLY|GOOGLE_PLACES_API_KEY/);
});
