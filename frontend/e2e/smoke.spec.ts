import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("login page is accessible and never indexed", async ({ page }) => {
  const res = await page.goto("/login");
  expect(res?.headers()["x-robots-tag"]).toBe("noindex, nofollow");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(a11y.violations).toEqual([]);
});

test("operator dashboard is noindex and sends signed-out visitors to sign in", async ({ page }) => {
  const res = await page.goto("/operator");
  expect(res?.headers()["x-robots-tag"]).toBe("noindex, nofollow");
  await page.waitForURL("**/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("privacy notice is public and indexable", async ({ page }) => {
  const res = await page.goto("/privacy/northwind-demo");
  expect(res?.status()).toBe(200);
  expect(res?.headers()["x-robots-tag"]).toBeUndefined();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /index, follow/);
  await expect(page.getByText(/object at any time/)).toBeVisible();
});

test("robots.txt disallows dashboards", async ({ request }) => {
  const body = await (await request.get("/robots.txt")).text();
  expect(body).toContain("Disallow: /operator");
  expect(body).toContain("Allow: /privacy/");
});

test("account pages are accessible, never indexed, and keep the token out of the address bar", async ({ page }) => {
  for (const path of ["/forgot-password", "/set-password"]) {
    const res = await page.goto(path);
    expect(res?.headers()["x-robots-tag"]).toBe("noindex, nofollow");
    const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(a11y.violations).toEqual([]);
  }
  await expect(page.getByText("This page needs the link from your email.")).toBeVisible();
  await page.goto("/set-password?token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG");
  await expect(page.getByLabel("New password")).toBeVisible();
  await expect(page).toHaveURL(/\/set-password$/);
});
