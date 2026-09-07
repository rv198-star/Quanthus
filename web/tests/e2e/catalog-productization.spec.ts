import { test, expect, type Page } from "@playwright/test";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const execute = promisify(execFile);
// Browser interaction uses the real deterministic query implementation via intercepted GETs.
// This is not evidence of production login or PostgreSQL access; those have separate API tests.
const pythonQuery = `
import json,sys
from quanthus.data_catalog import DataIndicatorCatalog
catalog=DataIndicatorCatalog()
kind,options=sys.argv[1],json.loads(sys.argv[2])
if kind=='summary': result=catalog.summary()
elif kind=='search': result=catalog.search(options.pop('q',''),**options)
elif kind=='relations': result=catalog.relations(**options)
else: result=catalog.item(options['entry_id'])
print(json.dumps(result,ensure_ascii=False))
`;

async function installCatalogFixture(page: Page) {
  await page.addInitScript(() => sessionStorage.setItem("quanthus.platform.session.v1", JSON.stringify({
    token: "catalog-browser-fixture-only", expiresAt: new Date(Date.now() + 3600000).toISOString(),
  })));
  await page.route("**/api/platform/v1/catalog/**", async route => {
    const url = new URL(route.request().url());
    const suffix = url.pathname.split("/catalog/")[1];
    let kind: string;
    const options: Record<string, string | number> = {};
    if (suffix === "summary") kind = "summary";
    else if (suffix === "search") {
      kind = "search";
      url.searchParams.forEach((value, key) => { options[key] = key === "limit" ? Number(value) : value; });
    } else {
      kind = suffix.endsWith("/relations") ? "relations" : "item";
      options.entry_id = decodeURIComponent(suffix.slice("items/".length).replace(/\/relations$/, ""));
      if (kind === "relations") {
        options.depth = Number(url.searchParams.get("depth") ?? 2);
        options.limit = Number(url.searchParams.get("limit") ?? 60);
        options.asset_scope = url.searchParams.get("asset_scope") ?? "full";
      }
    }
    const response = await execute(process.env.QUANTHUS_PYTHON ?? "python3", ["-c", pythonQuery, kind, JSON.stringify(options)], {
      cwd: root, env: { ...process.env, PYTHONPATH: `${root}/src`, PYTHONDONTWRITEBYTECODE: "1" }, maxBuffer: 8 * 1024 * 1024,
    });
    await route.fulfill({ status: 200, contentType: "application/json", body: response.stdout });
  });
}

async function search(page: Page, query: string) {
  await page.getByRole("textbox", { name: "搜索目录" }).fill(query);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByTestId("business-description")).toBeVisible();
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`Chinese cards and real graph navigation at ${viewport.width}px`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await installCatalogFixture(page);
    await page.goto("/catalog");
    await expect(page.getByRole("heading", { name: "先读懂数据，再使用数据" })).toBeVisible();
    await expect(page.getByText("已知缺口", { exact: false })).toHaveCount(0);
    await search(page, "dwd_security_bar.close");
    await expect(page.getByTestId("business-description")).toContainText("未复权");
    const technical = page.getByTestId("technical-details");
    await expect(technical).not.toHaveAttribute("open", "");
    const focusView = page.getByTestId("lineage-focus");
    await expect(focusView).toContainText("字段：close");
    const graphCanvas = focusView.getByTestId("lineage-canvas").first();
    const graphViewport = focusView.getByTestId("lineage-viewport").first();
    const graphBox = await graphCanvas.boundingBox();
    expect(Math.round(graphBox?.height ?? 0)).toBe(viewport.width <= 720 ? 420 : 520);
    const zoomReadout = focusView.getByTestId("lineage-zoom").first();
    const graphWorld = focusView.getByTestId("lineage-svg").first();
    const minimap = focusView.getByTestId("lineage-minimap").first();
    await expect(minimap).toBeVisible();
    await expect.poll(async () => graphWorld.evaluate(element => {
      const world = element.getBoundingClientRect();
      const canvas = element.parentElement!.getBoundingClientRect();
      return Math.max(Math.abs((world.left + world.right) / 2 - (canvas.left + canvas.right) / 2),
        Math.abs((world.top + world.bottom) / 2 - (canvas.top + canvas.bottom) / 2));
    })).toBeLessThan(3);
    const initialZoom = await zoomReadout.textContent();
    await focusView.getByRole("button", { name: "放大血缘图" }).first().click();
    await expect.poll(() => zoomReadout.textContent()).not.toBe(initialZoom);
    await focusView.getByRole("button", { name: "恢复100%缩放" }).first().click();
    await expect(zoomReadout).toHaveText("100%");
    await focusView.getByRole("button", { name: "适配并居中全部血缘图" }).first().click();
    await expect.poll(async () => graphWorld.evaluate(element => {
      const world = element.getBoundingClientRect();
      const canvas = element.parentElement!.getBoundingClientRect();
      return Math.max(Math.abs((world.left + world.right) / 2 - (canvas.left + canvas.right) / 2),
        Math.abs((world.top + world.bottom) / 2 - (canvas.top + canvas.bottom) / 2));
    })).toBeLessThan(3);
    await focusView.getByRole("button", { name: "居中当前焦点" }).first().click();
    const centerNode = focusView.locator('[data-lineage-node="dwd_table:dwd_security_bar"]');
    await expect.poll(async () => centerNode.evaluate(element => {
      const node = element.getBoundingClientRect();
      const canvas = element.closest('[data-testid="lineage-viewport"]')!.getBoundingClientRect();
      return Math.max(Math.abs((node.left + node.right) / 2 - (canvas.left + canvas.right) / 2),
        Math.abs((node.top + node.bottom) / 2 - (canvas.top + canvas.bottom) / 2));
    })).toBeLessThan(3);
    const panBox = await graphViewport.boundingBox();
    if (panBox) {
      const beforePan = Number(await graphWorld.getAttribute("data-viewport-x"));
      await page.mouse.move(panBox.x + panBox.width / 2, panBox.y + 45);
      await page.mouse.down();
      await expect(graphViewport).toHaveAttribute("data-grabbing", "true");
      await page.mouse.move(panBox.x + panBox.width / 2 + 180, panBox.y + 145, { steps: 5 });
      await page.mouse.up();
      await expect.poll(async () => Math.abs(Number(await graphWorld.getAttribute("data-viewport-x")) - beforePan)).toBeGreaterThan(150);
      const beforeWheel = await zoomReadout.textContent();
      await page.mouse.move(panBox.x + panBox.width / 2, panBox.y + panBox.height / 2);
      await page.mouse.wheel(0, -240);
      await expect.poll(() => zoomReadout.textContent()).not.toBe(beforeWheel);
    }
    await expect(page.getByTestId("lineage-coverage-level")).toContainText("字段全链已登记");
    for (const id of [
      "ods_dataset:daily_ohlcv_unadjusted",
      "dwd_table:dwd_security_bar",
      "adm_dataset:p4-event-research-adm.candidate_daily",
      "adm_dataset:structure-research-adm.rows",
      "adm_dataset:structure-continuous-facts.rows",
    ]) await expect(page.locator(`[data-lineage-node="${id}"]`)).toBeVisible();
    await expect(page.locator('[data-lineage-node^="process:"]')).toHaveCount(0);
    await expect(page.locator('[data-lineage-node^="projection:"]')).toHaveCount(0);
    await page.getByRole("combobox", { name: "血缘查看范围" }).selectOption("1");
    await expect(page.getByTestId("lineage-focus")).toContainText("1 跳");
    await page.getByRole("combobox", { name: "血缘查看范围" }).selectOption("full");
    await expect(page.getByTestId("lineage-focus")).toContainText("全链");

    // Node-to-node lineage navigation must keep the graph at the same viewport position instead of
    // jumping back to the detail card above while the selected entry and graph are refreshed.
    await focusView.scrollIntoViewIfNeeded();
    const beforeLineageNavigationTop = await focusView.evaluate(element => element.getBoundingClientRect().top);
    await page.locator('[data-lineage-node="adm_dataset:p4-event-research-adm.candidate_daily"]').click();
    await expect(page.getByTestId("business-description")).toContainText("P4 Event Library");
    await expect.poll(async () => Math.abs(
      await focusView.evaluate(element => element.getBoundingClientRect().top) - beforeLineageNavigationTop,
    )).toBeLessThan(16);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);

    await search(page, "dwd_adjustment_factor.effective_from");
    await expect(page.getByTestId("lineage-coverage-level")).toContainText("字段全链已登记");
    await expect(page.locator('[data-lineage-node="ods_dataset:adjustment_factors_pit"]')).toBeVisible();
    await expect(page.locator('[data-lineage-node="dwd_table:dwd_adjustment_factor"]')).toBeVisible();
    await expect(page.locator('[data-lineage-node="adm_dataset:p4-event-research-adm.candidate_daily"]')).toBeVisible();
    await expect(page.locator('[data-lineage-node^="source-field:"]')).toHaveCount(0);
    await expect(page.locator('[data-lineage-node^="projection:"]')).toHaveCount(0);

    await search(page, "dwd_adjustment_factor.factor_value");
    await expect(page.getByTestId("lineage-coverage-level")).toContainText("仅资产级上下文");
    await expect(page.getByTestId("lineage-focus").getByTestId("lineage-canvas")).toHaveCount(1);
    await expect(page.getByTestId("lineage-focus")).toContainText("不推断字段映射");

    await search(page, "price.sma20@1.0.0");
    await expect(page.getByTestId("business-description")).toBeVisible();
    await expect(page.getByRole("tab", { name: "计算依赖" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("lineage-focus")).toContainText("总回报指数");
    await expect(page.locator('[data-lineage-node^="process:"]')).toHaveCount(0);
    await expect(page.locator('[data-lineage-node^="projection:"]')).toHaveCount(0);
    await page.getByRole("tab", { name: "数据血缘" }).click();
    await expect(page.getByTestId("lineage-focus")).toContainText("不是已登记正式数据资产");
    await page.getByRole("tab", { name: "计算依赖" }).click();

    await search(page, "p4-event-research-adm.candidate_daily");
    await expect(page.getByTestId("business-description")).toContainText("P4 Event Library");
    await page.getByRole("tab", { name: "使用关系" }).click();
    await expect(page.getByTestId("lineage-focus")).toContainText("P4 Event Library");
    await page.getByRole("tab", { name: "数据血缘" }).click();
    await expect(page.locator('[data-lineage-node="consumer:p4-event-library"]')).toHaveCount(0);

    await page.getByRole("button", { name: "全局血缘地图" }).click();
    const global = page.getByTestId("lineage-overview");
    await expect(global).toBeVisible();
    for (const id of ["ods", "dwd", "adm"]) await expect(global.locator(`[data-lineage-node="${id}"]`)).toBeVisible();
    await expect(global).toContainText("70/94 个正式资产已接入血缘");
    await expect(global.locator('[data-lineage-node="indicator"]')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`catalog-${viewport.width}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}

test("anonymous readers see only the login entry", async ({ page }) => {
  await page.goto("/catalog");
  await expect(page.getByTestId("catalog-login-required")).toBeVisible();
  await expect(page.getByRole("link", { name: "前往登录" })).toBeVisible();
  await expect(page.getByTestId("lineage-overview")).toHaveCount(0);
});
