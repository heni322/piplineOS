import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signInOperator, totp } from "./totp";

// Full stack after `demo conversations` + `demo billing` (scripts/e2e-fullstack.sh, stage 2).
const EMAIL = process.env.E2E_EMAIL, PASSWORD = process.env.E2E_PASSWORD, SECRET = process.env.E2E_TOTP_SECRET;
const CLIENT = process.env.E2E_CLIENT_EMAIL, CLIENT_PW = process.env.E2E_CLIENT_PASSWORD;
test.skip(!EMAIL || !PASSWORD || !SECRET || !CLIENT || !CLIENT_PW || process.env.E2E_STAGE !== "conversations",
  "full-stack billing stage: run scripts/e2e-fullstack.sh");

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

test("operator drafts the invoice; issuing is blocked until the owner's sign-offs", async ({ page }) => {
  await signInOperator(page, EMAIL!, PASSWORD!, SECRET!);

  await page.getByRole("link", { name: "Billing" }).click();
  await expect(page.getByRole("heading", { name: "Billing" })).toBeVisible();
  await page.getByRole("button", { name: /October 2026/ }).click();
  const doc = page.getByRole("article", { name: /Invoice DRAFT-/ });
  await expect(doc.getByText("Qualified meeting attended: Contoso Managed IT Ltd, 15 Oct 2026")).toBeVisible();
  await expect(doc.getByText("£1,050.00").last()).toBeVisible();
  await expect(doc.getByText("Attendance confirmed")).toBeVisible();
  await expect(page.getByText("Tax rule not yet reviewed by your accountant")).toBeVisible();
  await accessible(page, "billing");

  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByLabel("Authenticator code").fill(totp(SECRET!));
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  await expect(page.locator("p.error[role=alert]")).toContainText("Go-live checklist not signed off");

  await page.getByRole("link", { name: "Reports" }).click();
  await page.getByLabel("To", { exact: true }).fill("2026-12-31"); // the demo's simulated meeting is mid-October 2026
  await expect(page.locator(".ledger div").filter({ hasText: "Attended" }).locator("dd")).toContainText("1");
  await accessible(page, "reports");

  await page.getByRole("link", { name: "Clients" }).click();
  await expect(page.getByRole("heading", { name: "Northwind Demo Services Ltd" })).toBeVisible();
  await accessible(page, "clients");
});

test("client sees results, accepted terms and billable meetings, and can dispute one", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(CLIENT!);
  await page.getByLabel("Password").fill(CLIENT_PW!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/client");
  await expect(page.getByRole("heading", { name: "Your results" })).toBeVisible();
  await expect(page.locator(".ledger").first().locator("div").filter({ hasText: "Booked" }).locator("dd")).toHaveText("1");
  await expect(page.getByRole("note")).toContainText("dry-run, not sent"); // demo numbers are never read as results
  await expect(page.getByRole("heading", { name: "Per 100 people reached" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Emergency stop" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop all sending" })).toBeVisible();
  await accessible(page, "client-overview");

  await page.getByRole("link", { name: "Terms" }).click();
  await expect(page.getByText("£300.00")).toBeVisible();
  await expect(page.getByText(/Accepted on/)).toHaveCount(2);
  await accessible(page, "client-terms");

  await page.getByRole("link", { name: "Invoices" }).click();
  await expect(page.getByText("No invoices yet.")).toBeVisible(); // drafts stay with the operator

  await page.getByRole("link", { name: "Meetings" }).click();
  const row = page.getByRole("row").filter({ hasText: "Contoso" });
  await expect(row).toContainText("Billable");
  await accessible(page, "client-meetings");
  await row.getByRole("button", { name: "Dispute" }).click();
  await page.getByLabel("What's wrong?").selectOption("qualification");
  await page.getByLabel("Tell us what happened").fill("Budget wasn't approved, so this isn't a qualified meeting.");
  await page.getByRole("dialog").getByRole("button", { name: "Dispute" }).click();
  await expect(page.getByRole("status")).toContainText("Dispute sent");
  await expect(row).toContainText("Dispute open");
});

test("client connects their own CRM; the token is never shown back; disconnecting needs a reason", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(CLIENT!);
  await page.getByLabel("Password").fill(CLIENT_PW!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/client");
  await page.getByRole("link", { name: "Your CRM" }).click();
  await expect(page.getByRole("heading", { name: /Connected to HubSpot/ })).toBeVisible(); // the demo client's
  await expect(page.getByText("Nothing is written to your CRM until your account is switched to live.", { exact: false })).toBeVisible();
  await accessible(page, "client-crm");

  await page.getByRole("button", { name: "Replace connection" }).click();
  await expect(page.getByLabel("Private app access token")).toBeFocused();
  await page.getByLabel("Private app access token").fill("pat-eu1-e2e-0000-not-real");
  await page.getByLabel("Meeting attended").fill("contractsent");
  await accessible(page, "client-crm-connect");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Connected.");
  await expect(page.getByText("contractsent")).toBeVisible();
  await expect(page.getByText("pat-eu1-e2e-0000-not-real")).toHaveCount(0);
  expect(await page.content()).not.toContain("pat-eu1-e2e-0000-not-real");

  await page.getByRole("button", { name: "Disconnect" }).click();
  await page.getByLabel(/Why are you disconnecting/).fill("short");
  await page.getByRole("button", { name: "Disconnect HubSpot" }).click();
  await expect(page.getByRole("heading", { name: /Connected to HubSpot/ })).toBeVisible(); // too short: refused
  await page.getByLabel(/Why are you disconnecting/).fill("Switching to a different CRM next quarter");
  await page.getByRole("button", { name: "Disconnect HubSpot" }).click();
  await expect(page.getByRole("heading", { name: "Disconnected" })).toBeVisible();
  await expect(page.getByText(/Reason: Switching/)).toBeVisible();
});

test("client shares a testimonial with a chosen scope and can withdraw it", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(CLIENT!);
  await page.getByLabel("Password").fill(CLIENT_PW!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/client");
  const proof = page.getByRole("region", { name: "Recommend us" });
  await proof.getByLabel(/Your words/).fill("Two qualified meetings in our first month, both with new accounts.");
  await proof.getByLabel("Where we may use it").selectOption("named");
  await expect(proof.getByRole("button", { name: "Share" })).toBeDisabled(); // a named quote needs a name
  await proof.getByLabel(/How to name you/).fill("Sam Carter, MD");
  await proof.getByRole("button", { name: "Share" }).click();
  await expect(proof.getByRole("status")).toContainText("Thank you");
  await accessible(page, "client-testimonial");
  await proof.getByRole("button", { name: "Withdraw" }).click();
  await expect(proof.getByRole("status")).toContainText("will not be shown again");
  await expect(proof).toContainText("Withdrawn");
});
