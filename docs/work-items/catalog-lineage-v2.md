# P3-DICT-003：Catalog Lineage V2

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Acceptance | `../acceptance/catalog-lineage-v2-implementation-audit-20260907.md`; `../acceptance/catalog-lineage-v2-production-closeout-20260907.md`; `../acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md` |
| Current status | A-D 与父工单 closed；生产 checkout/Web `c25638a`，API `3aa4837` |
| Work item | `P3-DICT-003` |
| Parent | `catalog-productization.md`; `index.md` |
| Governing design | `../design/data-catalog-lineage-v2.md` |
| Machine plan | `../../config/data-catalog-lineage-v2-delivery.v1.json` |

## 目标

把 P3-DICT-002 的工程静态加工图升级为正式数据资产血缘：主 Data Lineage 只展示受治理 ODS、DWD 和 Research ADM；字段作为资产内部粒度；加工过程进入 edge metadata；定义依赖和使用关系独立展示。

## 工单

| ID | 工作 | 依赖 | 状态 / 证据 |
| --- | --- | --- | --- |
| P3-DICT-003-A | 正式资产 ontology + ADM Registry | P3-DICT-002 | closed；`../acceptance/catalog-lineage-v2-implementation-audit-20260907.md` |
| P3-DICT-003-B | ODS→DWD→ADM 完整资产/字段血缘 + full closure | A | closed；同上 |
| P3-DICT-003-C | 计算依赖 / 使用关系与 Data Lineage 分离 | B | closed；同上 |
| P3-DICT-003-D | 独立审计、浏览器/工程门禁与生产发布 | C | closed；主生产收尾见 `../acceptance/catalog-lineage-v2-production-closeout-20260907.md`，原位节点导航复验见 `../acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md` |

## 实现边界

当前不建设 generic DWS，不复活 ADS，不修改任何 ODS/DWD/ADM 物理数据。`process:*`、Job-local TMP、Direct-DWD projection、Feature/Label/Semantic/Consumer 等对象可以保留为工程证据或其他关系视图，但不能继续成为 Data Lineage 主节点。

验收入口：`tests/unit/test_catalog_*`、`tests/platform/test_catalog_*`、`web/tests/catalog-contract.test.mjs`、`web/tests/e2e/catalog-productization.spec.ts`，以及最终 `make check`。
