# P3-DICT-002：数据目录产品化整改

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
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
| #230 / P3-DICT-002-C | 有依据的加工血缘 | 33 表声明来源概览，有限范围字段焦点图、加工步骤、粒度、证据及截断信息 |
| #231 / P3-DICT-002-D | 审计、回归与发布 | 语义反例、权限、桌面/窄屏浏览器、完整工程检查、提交与生产核查 |

机器计划是状态权威，按 A→B→C→D 关闭，父工单最后关闭。

## 代码与验证入口

业务说明：`config/catalog-business-metadata.v1.json`、`src/quanthus/catalog_presentation.py`。
血缘：`config/catalog-lineage.v1.json`、`src/quanthus/catalog_lineage.py`。
目录/API：`src/quanthus/data_catalog.py`、`src/quanthus/platform/api.py`。
界面：`web/app/catalog-browser.tsx`、`web/app/catalog-lineage.tsx`。

聚焦验证：`tests/unit/test_catalog_productization.py`、
`tests/platform/test_catalog_productization_api.py` 及既有 Catalog 测试。
浏览器验证：在 `web` 下运行 `npx playwright test --config playwright.catalog.config.ts`。
浏览器用真实目录查询实现支撑受控 GET 数据，不替代生产 PostgreSQL 或真实登录验收。

生产只更新 API/Web；不发布 Worker、不重跑研究、不改数据或数据库结构。
