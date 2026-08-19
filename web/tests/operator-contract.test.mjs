import assert from "node:assert/strict";
import test from "node:test";

async function render(pathname) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("operator-test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the operator entry route and hydrates the complete login product", async () => {
  const response = await render("/operator");
  assert.equal(response.status, 200);
  const html = await response.text();

  assert.match(html, /Quanthus/);
  assert.match(html, /平台控制台/);
  assert.match(html, /<title>平台控制台 · Quanthus<\/title>/);
  assert.match(html, /进入 Quanthus 运行控制面/);
  assert.match(html, /登录平台/);
  assert.match(html, /返回策略归因工作台/);
  assert.doesNotMatch(
    html,
    /MCP|Fast Analysis|AI Copilot|计费|经纪商|实盘交易|社区市场/i,
  );
});

test("operator bundle calls only admitted Platform Kernel and publication routes", async () => {
  const [{ readFile }, { readdir }] = await Promise.all([
    import("node:fs/promises"),
    import("node:fs/promises"),
  ]);
  const assetsRoot = new URL("../dist/client/assets/", import.meta.url);
  const assets = await readdir(assetsRoot);
  const javascript = (
    await Promise.all(
      assets
        .filter((name) => name.endsWith(".js"))
        .map((name) => readFile(new URL(name, assetsRoot), "utf8")),
    )
  ).join("\n");

  assert.match(javascript, /进入 Quanthus 运行控制面/);
  assert.match(javascript, /登录平台/);
  assert.match(javascript, /返回策略归因工作台/);

  for (const route of [
    "/api/platform/v1/auth/login",
    "/api/platform/v1/auth/logout",
    "/api/platform/v1/auth/me",
    "/api/platform/v1/auth/mfa/enroll",
    "/api/platform/v1/auth/mfa/confirm",
    "/api/platform/v1/admin/users",
    "/api/platform/v1/quanthus/gates",
    "/api/platform/v1/jobs/attribution-verifications",
    "/data/attribution-releases/catalog.json",
  ]) {
    assert.match(javascript, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.doesNotMatch(
    javascript,
    /\/mcp|oauth|billing|credits|broker|fast-analysis|ai-chat|copilot/i,
  );
});
