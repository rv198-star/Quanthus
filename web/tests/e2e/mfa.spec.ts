import { expect, test } from "@playwright/test";

const adminUsername = process.env.QUANTHUS_WEB_E2E_USERNAME;
const adminPassword = process.env.QUANTHUS_WEB_E2E_PASSWORD;
const usernamePrefix = process.env.QUANTHUS_WEB_E2E_MFA_USERNAME;
const password = process.env.QUANTHUS_WEB_E2E_MFA_PASSWORD;

function decodeBase32(value: string) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const character of value.toUpperCase()) {
    const index = alphabet.indexOf(character);
    if (index < 0) continue;
    bits += index.toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let cursor = 0; cursor + 8 <= bits.length; cursor += 8) {
    bytes.push(Number.parseInt(bits.slice(cursor, cursor + 8), 2));
  }
  return new Uint8Array(bytes);
}

async function totp(secret: string, timestamp = Date.now()) {
  const counter = BigInt(Math.floor(timestamp / 1000 / 30));
  const message = new Uint8Array(8);
  new DataView(message.buffer).setBigUint64(0, counter);
  const key = await crypto.subtle.importKey(
    "raw",
    decodeBase32(secret),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const digest = new Uint8Array(await crypto.subtle.sign("HMAC", key, message));
  const offset = digest[digest.length - 1] & 0x0f;
  const value =
    (((digest[offset] & 0x7f) << 24) |
      (digest[offset + 1] << 16) |
      (digest[offset + 2] << 8) |
      digest[offset + 3]) %
    1_000_000;
  return value.toString().padStart(6, "0");
}

test("MFA enrollment, challenged login, and disable complete through the Web", async ({ page, request }, testInfo) => {
  if (!adminUsername || !adminPassword || !usernamePrefix || !password) {
    throw new Error("admin and MFA acceptance credentials are required");
  }
  const runSuffix = `${testInfo.project.name}-${Date.now().toString(36)}`;
  const username = `${usernamePrefix.slice(0, 63 - runSuffix.length)}-${runSuffix}`;
  const login = await request.post("/api/platform/v1/auth/login", {
    data: { username: adminUsername, password: adminPassword },
  });
  expect(login.ok()).toBe(true);
  const { token } = (await login.json()) as { token: string };
  const created = await request.post("/api/platform/v1/admin/users", {
    headers: { Authorization: `Bearer ${token}` },
    data: { username, password, role: "operator" },
  });
  expect(created.status()).toBe(201);

  await page.goto("/operator");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await page.getByRole("button", { name: "安全设置" }).click();
  await page.getByRole("button", { name: "开始配置" }).click();

  const secret = (await page.locator("code").first().textContent())?.trim();
  if (!secret) throw new Error("MFA secret was not rendered");
  await page.getByLabel("应用生成的 6 位验证码").fill(await totp(secret));
  await page.getByRole("button", { name: "确认启用" }).click();
  await expect(page.getByRole("heading", { name: "已启用" })).toBeVisible();

  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录平台" }).click();
  await expect(page.getByLabel("动态验证码")).toBeVisible();
  await page.getByLabel("动态验证码").fill(await totp(secret));
  await page.getByRole("button", { name: "验证并登录" }).click();
  await expect(page.getByRole("heading", { name: "运行状态" })).toBeVisible();

  await page.getByRole("button", { name: "安全设置" }).click();
  await page.getByLabel("当前 6 位动态验证码").fill(await totp(secret));
  await page.getByRole("button", { name: "关闭 MFA" }).click();
  await expect(page.getByRole("heading", { name: "未启用" })).toBeVisible();
});
