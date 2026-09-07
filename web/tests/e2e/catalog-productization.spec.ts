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
    await expect(page.getByTestId("lineage-focus")).toContainText("收盘价");
    const upstream = page.locator('[data-lineage-node="source-field:daily_ohlcv_unadjusted.close"]');
    await expect(upstream).toBeVisible();
    await page.getByRole("combobox", { name: "血缘展开层数" }).selectOption("3");
    await expect(page.getByTestId("lineage-focus")).toContainText("受展开层数和节点预算限制");
    await page.locator('[data-lineage-node="projection:daily_ohlcv_none.close"]').click();
    await expect(page.getByTestId("lineage-focus")).toContainText("当前焦点：研究日线 · 收盘价");
    await page.getByRole("button", { name: "返回当前卡片的血缘" }).click();
    await page.getByRole("button", { name: "全局血缘地图" }).click();
    const global = page.getByTestId("lineage-overview");
    await expect(global).toBeVisible();
    await global.locator('[data-lineage-node="indicator"]').focus();
    await page.keyboard.press("Enter");
    await global.getByRole("button", { name: "20期简单均线（总回报指数）", exact: false }).click();
    await expect(page.getByTestId("business-description")).toContainText("包含当前时段");
    await expect(page.getByTestId("catalog-detail")).toContainText("完整研究管道");
    await search(page, "dwd_adjustment_factor");
    const factorTableGraph = page.getByTestId("lineage-focus");
    for (const exactId of ["adjustment_factors_pit", "adjustment_evidence_delta", "factor_evidence_delta", "adjustment_evidence", "dwd_adjustment_factor"]) {
      await expect(factorTableGraph).toContainText(exactId);
    }
    await expect(factorTableGraph).not.toContainText("复权因子来源 · 日发布工件");
    await expect(page.locator('[data-lineage-node="process:release_artifacts-adjustment_evidence_delta"]')).toHaveCount(0);
    await search(page, "dwd_adjustment_factor.effective_from");
    await expect(page.getByTestId("lineage-coverage-level")).toContainText("字段级血缘已证明");
    await expect(page.locator('[data-lineage-node="source-field:adjustment_factors_pit.effective_date"]')).toBeVisible();
    await expect(page.locator('[data-lineage-node="projection:p4-event.factor_effective_from"]')).toBeVisible();
    await search(page, "dwd_adjustment_factor.factor_value");
    await expect(page.getByTestId("lineage-coverage-level")).toContainText("当前仅有表级加工上下文");
    await expect(page.getByTestId("table-lineage-context")).toBeVisible();
    await search(page, "indicator.momentum.macd");
    await expect(page.getByTestId("catalog-detail")).toContainText("暂无");
    await expect(page.getByTestId("lineage-focus")).toContainText("不能据此认定");
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
