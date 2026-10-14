import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000, // a sign-in may wait for up to three fresh single-use codes (31 s each)
  use: {
    baseURL: "http://localhost:3100",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  webServer: { command: "pnpm exec next start -p 3100", url: "http://localhost:3100/login", reuseExistingServer: true, timeout: 120_000 },
});
