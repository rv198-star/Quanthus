import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appRoot = new URL("../app/", import.meta.url);

async function source(path) {
  return readFile(new URL(path, appRoot), "utf8");
}

test("catalog page is a first-class route backed only by read APIs", async () => {
  const [page, browser] = await Promise.all([
    source("catalog/page.tsx"),
    source("catalog-browser.tsx"),
  ]);
  assert.match(page, /CatalogBrowser/);
  for (const route of [
    "/api/platform/v1/catalog/summary",
    "/api/platform/v1/catalog/search",
    "/api/platform/v1/catalog/items/",
    "/relations",
  ]) {
    assert.match(browser, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.doesNotMatch(browser, /method:\s*["'](?:POST|DELETE)["']/);
  assert.match(browser, /已知缺口/);
  assert.match(browser, /Read-only authority projection/);
  assert.match(browser, /data-testid="catalog-page"/);
});

test("catalog UI exposes authority, lifecycle and relation semantics instead of an editor", async () => {
  const browser = await source("catalog-browser.tsx");
  for (const term of [
    "Authority path",
    "Authority SHA",
    "定义 / 计算规格",
    "关系 / Lineage",
    "Reserved",
    "Registry 已校验",
    "仅合同",
  ]) {
    assert.match(browser, new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  for (const forbidden of ["保存公式", "创建指标", "修改字段", "一键添加到策略"] ) {
    assert.doesNotMatch(browser, new RegExp(forbidden));
  }
});

test("existing primary workspaces link to the Catalog", async () => {
  const [operator, attribution] = await Promise.all([
    source("operator-console.tsx"),
    source("attribution-workbench.tsx"),
  ]);
  assert.match(operator, /href="\/catalog"/);
  assert.match(attribution, /href="\/catalog"/);
});
