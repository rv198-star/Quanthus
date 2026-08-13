import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const username = process.env.QUANTHUS_WEB_E2E_USERNAME;
const password = process.env.QUANTHUS_WEB_E2E_PASSWORD;

async function login(page: Page) {
  if (!username || !password) throw new Error("E2E credentials are required");
  await page.goto("/operator");
  await expect(page.getByRole("heading", { name: "进入 Quanthus 运行控制面" })).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByRole("heading", { name: "运行状态" })).toBeVisible();
}

test("operator can inspect runtime, gates, users, security, and attribution verification", async ({ page }) => {
  await login(page);
  await expect(page.getByText("PK6", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("PK4", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("已连接")).toBeVisible();

  const usersTab = page.getByRole("button", { name: "用户管理" });
  if (await usersTab.count()) {
    await usersTab.click();
    await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible();
    await expect(page.getByLabel("平台用户").getByText(username!, { exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "安全设置" }).click();
  await expect(page.getByRole("heading", { name: "安全设置" })).toBeVisible();
  await expect(page.getByText("多因素认证", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "归因验证" }).click();
  await expect(page.getByRole("heading", { name: "归因发布验证" })).toBeVisible();
  const submit = page.getByRole("button", { name: "提交验证" });
  if (await submit.isEnabled()) {
    await submit.click();
    await expect(page.getByText("已通过").first()).toBeVisible({ timeout: 15_000 });
  } else {
    await expect(page.getByText("发布目录为空")).toBeVisible();
  }

  await expect(page.locator("body")).not.toContainText(/MCP|Fast Analysis|AI Copilot|计费|经纪商/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});

test("anonymous route exposes an accessible login form without overflow", async ({ page }) => {
  await page.goto("/operator");
  await expect(page.getByRole("button", { name: "登录平台" })).toBeVisible();
  await expect(page.getByLabel("用户名")).toBeEditable();
  await expect(page.getByLabel("密码")).toBeEditable();
  await expect(page.getByRole("link", { name: "返回策略归因工作台" })).toHaveAttribute("href", "/");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
