import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signInOperator } from "./totp";

// Full stack after `pipelineos demo conversations` (scripts/e2e-fullstack.sh runs it as a second stage).
const EMAIL = process.env.E2E_EMAIL, PASSWORD = process.env.E2E_PASSWORD, SECRET = process.env.E2E_TOTP_SECRET;
test.skip(!EMAIL || !PASSWORD || !SECRET || process.env.E2E_STAGE !== "conversations",
  "full-stack conversations stage: run scripts/e2e-fullstack.sh");

async function signIn(page: Page) {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);
}

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

test("operator works the escalations, data-request and meeting pages", async ({ page }) => {
  await signIn(page);

  await page.getByRole("link", { name: "Escalations", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Escalations" })).toBeVisible();
  // the access request needs a person; the plain unsubscribe was actioned automatically and is not escalated
  await expect(page.getByText("Data-protection request")).toHaveCount(1);
  await expect(page.getByText("Request: access")).toBeVisible();
  await accessible(page, "escalations");

  await page.getByRole("link", { name: "Data requests" }).click();
  await expect(page.getByRole("heading", { name: "Data-protection requests" })).toBeVisible();
  await expect(page.getByText("Access", { exact: true })).toBeVisible();
  await expect(page.locator(".days").first()).toContainText(/days left|Overdue|Due today/);
  await accessible(page, "data-requests");
  const row = page.getByRole("row").filter({ hasText: "Access" });
  await row.getByRole("button", { name: "Mark in progress" }).click();
  await page.getByLabel("Note for the record").fill("Identity check sent to the requester");
  await page.getByRole("dialog").getByRole("button", { name: "Mark in progress" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Updated." })).toBeVisible();
  await expect(row).toContainText("in progress");

  await page.getByRole("link", { name: "Meetings" }).click();
  await expect(page.getByRole("heading", { name: "Meetings" })).toBeVisible();
  const meeting = page.getByRole("row").filter({ hasText: "Contoso" });
  await expect(meeting).toContainText("attended");
  await expect(meeting).toContainText("***"); // prospect masked in the list
  await meeting.getByRole("button", { name: "Open brief" }).click();
  await expect(page.getByRole("dialog").locator(".brief")).toContainText("Contoso");
  await accessible(page, "meetings");
});

test("operator works the contact tasks queue", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Contact tasks" }).click();
  await expect(page.getByRole("heading", { name: "Contact tasks" })).toBeVisible();
  // the demo's Germany-based IT Director: email blocked by country rules -> a task to reach them by hand
  const row = page.getByRole("row", { name: /Fabrikam Cloud Ltd/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("Personal, by hand")).toBeVisible();
  await accessible(page, "contact-tasks");

  await row.getByRole("button", { name: "Show name" }).click();
  await row.getByLabel("Why do you need to see this? (logged)").fill("Preparing the introduction");
  await row.getByRole("button", { name: "Show", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Your view was logged." })).toBeVisible();

  await row.getByRole("button", { name: "Close task" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Skipped: not contacted").check();
  await dialog.getByLabel("What did you do? (kept with the task)").fill("Client asked us to wait until Q1");
  await dialog.getByRole("button", { name: "Close task" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Task closed." })).toBeVisible();
  await expect(page.getByRole("row", { name: /Fabrikam Cloud Ltd/ })).toHaveCount(0);
  await accessible(page, "contact-tasks-closed");
});
