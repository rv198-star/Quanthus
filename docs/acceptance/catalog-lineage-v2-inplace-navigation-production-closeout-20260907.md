# Catalog Lineage V2 原位节点导航生产收尾 — 2026-09-07

## 结论

`P3-DICT-003` 的血缘图节点导航交互已完成产品细节修正并在生产 Web 发布。点击血缘图中的其他节点时，详情卡与关系数据仍会更新，但浏览器保持血缘区域在当前视口位置，不再因为加载中间态移除图或缩短详情区而跳回页面上方。

## 实现

结果提交：`c25638a73c7ca02fda11d891078c8d1a16769e82`。

`web/app/catalog-browser.tsx` 将“列表/搜索选中”和“血缘图内追查”拆开：图内导航保留既有 relations 与详情内容直到新响应就绪，并记录血缘区域的 viewport top；新详情/关系完成时通过 layout-phase 补偿上方内容高度变化。该行为只影响浏览器导航，不改变 Catalog API、Data Lineage/Definition/Usage 图语义、节点/边、字段证明或权限。

## 验证

- Catalog TypeScript strict：PASS；
- Web lint：PASS；
- Web unit/contract：18 passed；
- Chromium 1440px / 390px / anonymous：3 passed；
- 新浏览器断言在桌面与窄屏均验证：点击 `adm_dataset:p4-event-research-adm.candidate_daily` 后详情确实更新，血缘区域相对 viewport 的位置漂移小于 16px，且页面仍保持在滚动后的血缘位置；
- 完整仓库 `make check`：1602 passed / 7 existing environment skips。

7 个 skip 均为既有环境限制：production expert artifacts 未挂载，或测试 PostgreSQL DSN 未配置；不计为通过项。

## 生产

Web-only 部署：

- production checkout：`c25638a73c7ca02fda11d891078c8d1a16769e82`；
- Web：`quanthus-platform-web:git-c25638a`；
- Web image digest：`sha256:4e18472a2973d6f6e8791c0bb107ddb1f1a52da39f32999a5e3adadb1924a07d`；
- API 保持 `quanthus-platform-kernel:git-3aa4837`；
- API health 200 healthy；ready 200 / postgres=true；Web healthy；
- Replay Worker、Event Worker、PostgreSQL、DWD 均未修改。

## Claim ceiling

这是 Web 原位导航行为修正，不改变 `P3-DICT-003` 已验收的资产 ontology、ADM Registry、完整已登记资产闭包、Definition Dependency、Usage Dependency 或静态/运行审计边界。
