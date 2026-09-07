import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "catalog-productization.spec.ts",
  timeout: 60000,
  workers: 1,
  reporter: [["line"]],
  use: { baseURL: "http://127.0.0.1:4317", browserName: "chromium", headless: true },
  webServer: {
    command: "npm run dev -- --port 4317 --hostname 127.0.0.1",
    url: "http://127.0.0.1:4317/catalog",
    timeout: 90000,
    reuseExistingServer: false,
  },
});
