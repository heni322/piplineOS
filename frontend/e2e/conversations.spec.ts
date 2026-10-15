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

test("operator sets a client's targeting for the free sources", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Clients" }).click();
  await page.getByRole("button", { name: "Targeting" }).first().click();
  const panel = page.getByRole("dialog");
  await expect(panel.getByRole("heading", { name: /^Targeting:/ })).toBeVisible();
  await panel.getByLabel("SIC codes (five digits, one per line)").fill("62020\n62090");
  await panel.getByRole("checkbox", { name: "FR" }).check();
  await panel.getByRole("button", { name: "Add company" }).click();
  await panel.getByLabel("Company name 1").fill("Contoso Managed IT Ltd");
  await panel.getByLabel("UK company number 1").fill("01234567");
  await panel.getByLabel("Job board (e.g. greenhouse:contoso) 1").fill("greenhouse:contoso");
  await accessible(page, "client-targeting");
  await panel.getByRole("button", { name: "Save targeting" }).click();
  await expect(page.getByRole("status").filter({ hasText: /Targeting saved \(configuration version \d+\)/ })).toBeVisible();

  // an invalid SIC code is refused with a reason the operator can act on
  await page.getByRole("button", { name: "Targeting" }).first().click();
  await expect(panel.getByLabel("SIC codes (five digits, one per line)")).toHaveValue("62020\n62090");
  await expect(panel.getByLabel("Company name 1")).toHaveValue("Contoso Managed IT Ltd");
  await panel.getByLabel("SIC codes (five digits, one per line)").fill("6202");
  await panel.getByRole("button", { name: "Save targeting" }).click();
  await expect(panel.getByRole("alert")).toContainText("SIC codes are 5 digits");
});

test("operator onboards a brand-new client from the console, guided step by step", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Clients" }).click();
  await page.getByRole("button", { name: "Add client" }).click();
  await page.getByLabel("Legal name").fill("Fabrikam IT Ltd");
  await page.getByLabel("Short name (used in links)").fill("fabrikam-it");
  await page.getByLabel("Company number").fill("07654321");
  await page.getByLabel("Registered address").fill("2 Market St, York");
  await page.getByRole("button", { name: "Create client", exact: true }).click();
  const card = page.getByRole("region", { name: "Fabrikam IT Ltd" });
  const next = card.getByRole("group", { name: "Next step" });
  await expect(next).toContainText("Next step: Legitimate interests assessment");

  await next.getByRole("button", { name: "Record LIA" }).click();
  await card.getByLabel("Reference").fill("LIA-FAB-1");
  await card.getByLabel("Approved on").fill("2026-10-01");
  await card.getByRole("button", { name: "Save" }).click();
  await expect(next).toContainText("Next step: Data processing agreement");
  await next.getByRole("button", { name: "Record DPA" }).click();
  await card.getByLabel("Version").fill("v1");
  await card.getByLabel("Signed on").fill("2026-10-02");
  await card.getByRole("button", { name: "Save" }).click();
  await expect(next).toContainText("Next step: Offer, criteria and pricing");

  await next.getByRole("button", { name: "Offer & pricing" }).click();
  const panel = page.getByRole("dialog");
  await panel.getByLabel("Sender's name").fill("Alex Morgan");
  await panel.getByLabel("Sender's job title").fill("Managing Director");
  await panel.getByLabel(/What the client offers/).fill("fixed-price IT support for growing UK companies");
  await panel.getByLabel(/Purpose of the outreach/).fill("IT support for growing companies");
  await expect(panel.getByLabel("Criterion 1")).not.toHaveValue("");
  await panel.getByLabel("Per qualified, attended meeting").fill("250");
  await accessible(page, "client-offer");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status").filter({ hasText: /configuration version \d+/ })).toBeVisible();
  await expect(next).toContainText("Next step: Client login");

  await card.getByRole("button", { name: "Sending domain" }).click();
  await panel.getByLabel(/^Domain/).fill("outreach.fabrikam-it.example");
  await panel.getByRole("button", { name: "Add domain" }).click();
  await panel.getByLabel("Inbox address").fill("alex");
  await panel.getByLabel("Name shown to recipients").fill("Alex Morgan");
  await panel.getByRole("button", { name: "Add inbox" }).click();
  await expect(page.getByRole("status").filter({ hasText: "warming at 10 emails a day" })).toBeVisible();

  await page.getByRole("link", { name: "Campaigns" }).click();
  await page.getByLabel("Client", { exact: true }).selectOption({ label: "Fabrikam IT Ltd" });
  await page.getByLabel("Campaign name").fill("Pilot: contract winners");
  await page.getByRole("button", { name: "Create campaign" }).click();
  const row = page.getByRole("row").filter({ hasText: "Pilot: contract winners" });
  await expect(row).toContainText("draft");
  await expect(row).toContainText("0 of 100");
  await row.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Activate the client first" })).toBeVisible();
});
