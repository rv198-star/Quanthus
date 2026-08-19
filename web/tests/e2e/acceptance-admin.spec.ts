import { expect, test } from "@playwright/test";

const username = process.env.QUANTHUS_WEB_E2E_USERNAME;
const password = process.env.QUANTHUS_WEB_E2E_PASSWORD;
const acceptanceUsername = process.env.QUANTHUS_WEB_E2E_NEW_USERNAME;
const acceptancePassword = process.env.QUANTHUS_WEB_E2E_NEW_PASSWORD;

test("admin can create a least-privilege user through the operator Web", async ({ page }, testInfo) => {
  if (!username || !password || !acceptanceUsername || !acceptancePassword) {
    throw new Error("admin and acceptance user credentials are required");
  }
  const runSuffix = `${testInfo.project.name}-${Date.now().toString(36)}`;
  const generatedUsername = `${acceptanceUsername.slice(0, 63 - runSuffix.length)}-${runSuffix}`;
  await page.goto("/operator");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await page.getByRole("button", { name: "用户管理" }).click();

  await page.getByRole("region", { name: "创建用户" }).getByLabel("用户名").fill(generatedUsername);
  await page.getByRole("region", { name: "创建用户" }).getByLabel("初始密码").fill(acceptancePassword);
  await page.getByRole("region", { name: "创建用户" }).getByLabel("角色").selectOption("viewer");
  await page.getByRole("button", { name: "创建用户" }).click();

  const createdUser = page.getByRole("listitem", { name: new RegExp(`^${generatedUsername}`) });
  await expect(createdUser).toBeVisible();
  await expect(createdUser.getByText("只读用户", { exact: true })).toBeVisible();
});
