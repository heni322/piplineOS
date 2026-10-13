import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000, // a sign-in may wait up to 31 s for a fresh single-use code
  use: {
    baseURL: "http://localhost:3100",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  webServer: { command: "pnpm exec next start -p 3100", url: "http://localhost:3100/login", reuseExistingServer: true, timeout: 120_000 },
});
