import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appRoot = new URL("../app/", import.meta.url);
async function source(path) { return readFile(new URL(path, appRoot), "utf8"); }

test("catalog remains a first-class read-only route without development dashboard", async () => {
  const [page, browser] = await Promise.all([source("catalog/page.tsx"), source("catalog-browser.tsx")]);
  assert.match(page, /CatalogBrowser/);
  for (const route of ["/api/platform/v1/catalog/summary", "/api/platform/v1/catalog/search", "/api/platform/v1/catalog/items/", "/relations"]) {
    assert.ok(browser.includes(route));
  }
  assert.doesNotMatch(browser, /method:\s*["'](?:POST|DELETE|PUT|PATCH)["']/);
  assert.doesNotMatch(browser, /已知缺口|gap_count|summary\.gaps|gapsOpen/);
  assert.match(browser, /data-testid="catalog-page"/);
});

test("Chinese business card precedes collapsed machine details", async () => {
  const browser = await source("catalog-browser.tsx");
  for (const term of ["如何理解和使用", "使用时请注意", "技术详情：精确规格、版本与来源依据", "注册 / 生命周期", "实现状态", "验证状态", "验收范围", "business-description"]) {
    assert.ok(browser.includes(term), term);
  }
  assert.match(browser, /<details[^>]*data-testid="technical-details"/);
  assert.doesNotMatch(browser, /<details[^>]*open[^>]*data-testid="technical-details"/);
  assert.doesNotMatch(browser, /下游 \/ 引用|上游 \/ 被引用/);
  assert.match(browser, /相关，但不等价/);
  assert.match(browser, /所属对象与概念关联（不作为加工箭头）/);
  for (const term of ["保存公式", "创建指标", "修改字段", "一键添加到策略"]) assert.ok(!browser.includes(term));
});

test("lineage has overview, focus, keyboard navigation and honest boundaries", async () => {
  const graph = await source("catalog-lineage.tsx");
  for (const term of ["lineage-overview", "lineage-focus", "lineage-svg", "lineage-boundary", "lineage-evidence", "lineage-controls", "lineage-viewport", "lineage-minimap", "缩小血缘图", "放大血缘图", "恢复100%缩放", "适配并居中全部血缘图", "居中当前焦点", "onWheel", "ResizeObserver", "onPointerDown", "onPointerMove", "markerEnd", "onKeyDown", "血缘关系", "字段级血缘", "表级上下文", "范围、限制与加工依据", "非运行回执", "visibleEdges"]) {
    assert.ok(graph.includes(term), term);
  }
  assert.doesNotMatch(graph, /所属表已证明的加工上下文|上游来源与下游使用/);
  assert.doesNotMatch(graph, /scrollLeft|scrollTop|scrollWidth|scrollTo\(/);
});

test("existing primary workspaces link to the Catalog", async () => {
  const [operator, attribution] = await Promise.all([source("operator-console.tsx"), source("attribution-workbench.tsx")]);
  assert.match(operator, /href="\/catalog"/);
  assert.match(attribution, /href="\/catalog"/);
});
