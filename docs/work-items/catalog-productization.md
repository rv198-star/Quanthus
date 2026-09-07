# P3-DICT-002：数据目录产品化整改

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Acceptance | `../acceptance/catalog-productization-production-closeout-20260907.md`; `../acceptance/catalog-field-lineage-repair-20260907.md`; `../acceptance/catalog-field-lineage-production-closeout-20260907.md`; `../acceptance/catalog-lineage-node-identity-production-closeout-20260907.md`; `../acceptance/catalog-lineage-view-controls-production-closeout-20260907.md` |
| Current status | A–D and parent closed；view controls deployed on Web `d485508`；API remains `0c671f9` |
| Work item | `P3-DICT-002` / #232 |
| Parent | `index.md`; `data-indicator-dictionary.md` |
| Governing design | `../design/data-indicator-catalog-productization.md` |
| Machine plan | `../../config/data-indicator-catalog-productization.v1.json` |

## 目标

把只读元数据目录改为用户能读懂的中文业务说明和有证据的加工血缘，不修改数仓、指标
公式、Facts 数值、策略规则或执行权限。此前 #215 的验收保留为原范围的历史记录。

## 工单及顺序

| 工单 | 工作 | 交付边界 |
| --- | --- | --- |
| #228 / P3-DICT-002-A | 分离内部诊断和业务界面 | 首页及业务 summary 无开发缺口；内部检测保留，业务限制留在对应条目 |
| #229 / P3-DICT-002-B | 中文说明与分层卡片 | 664 个条目、33 表及 524 字段覆盖；英文 ID 可复制，技术详情折叠 |
| #230 / P3-DICT-002-C | 有依据且可管理视图的加工血缘 | 保留精确身份和 proof boundary；画布固定高度，统一放大/缩小/100%/适配/焦点居中及拖拽平移 |
| #231 / P3-DICT-002-D | 审计、回归与发布 | 语义反例、权限、桌面/窄屏浏览器、完整工程检查、提交与生产核查 |

机器计划是状态权威，按 A→B→C→D 关闭，父工单最后关闭。字段焦点修复见 `../acceptance/catalog-field-lineage-repair-20260907.md`；节点身份/表级图可读性复验见 `../acceptance/catalog-lineage-node-identity-repair-20260907.md`；固定画布与缩放/居中/平移复验见 `../acceptance/catalog-lineage-view-controls-20260907.md`。当前小版本仅改变浏览器画布与视图管理，不改变任何血缘节点、边或数据语义。

## 代码与验证入口

业务说明：`config/catalog-business-metadata.v1.json`、`src/quanthus/catalog_presentation.py`。
血缘：`config/catalog-lineage.v1.json`、`src/quanthus/catalog_lineage.py`。
目录/API：`src/quanthus/data_catalog.py`、`src/quanthus/platform/api.py`。
界面：`web/app/catalog-browser.tsx`、`web/app/catalog-lineage.tsx`。

聚焦验证：`tests/unit/test_catalog_productization.py`、
`tests/platform/test_catalog_productization_api.py` 及既有 Catalog 测试。
浏览器验证：在 `web` 下运行 `npx playwright test --config playwright.catalog.config.ts`。
浏览器用真实目录查询实现支撑受控 GET 数据，不替代生产 PostgreSQL 或真实登录验收。
Catalog严格类型检查：在 `web` 下运行 `npx tsc --noEmit --project tsconfig.catalog.json`。
全库TypeScript的原有11项诊断单独保留，不冒充本检查已覆盖全库。

生产只更新 API/Web；不发布 Worker、不重跑研究、不改数据或数据库结构。
