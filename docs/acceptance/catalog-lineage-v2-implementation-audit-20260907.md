# Catalog Lineage V2 实现与语义审计 — 2026-09-07

## 结论

`P3-DICT-003-A/B/C` 在仓库实现范围内通过，可进入 D 的全库门禁与生产发布。

本轮不再把旧静态工程图当作 Data Lineage。主数据血缘节点只接受受治理 ODS、DWD、Research ADM 数据资产；字段作为资产内部坐标，加工过程作为 edge metadata。计算定义和消费者分别进入 Definition Dependency / Usage Dependency。

## 资产 ontology / ADM Registry

确定性 Catalog 当前为：

- 748 个条目；10 个既有内部维护 gap；
- 57 个 ODS dataset；
- 33 个 DWD table / 524 个 DWD field；
- 4 个正式 Research ADM dataset / 76 个 ADM field；
- 其余 Feature / Label / semantic 定义保持原有登记边界。

首批 ADM 数据资产：

1. `p4-event-research-adm.candidate_daily`
2. `p4-event-research-adm.static_industry_mapping`
3. `structure-research-adm.rows`
4. `structure-continuous-facts.rows`

`quanthus.structure-v2-adm.v1` 仍明确排除：它当前是固定 B1-B5 Run 内 manifest-bound 结果面，尚无独立稳定 ADM asset policy；Catalog 不自行升级其生命周期。

另外从 DWD 机器源契约补登记 4 个此前未进入 Base-only Catalog 投影、但属于正式 recurring ODS 业务资产的逻辑身份：`raw_f10`、`financial_statements`、`security_lifecycle`、`industry_membership`。日/周 delta、hotfix、SQLite member 等物理实例不成为独立主节点；它们保留在 edge evidence/notes。

## Data Lineage V2

当前登记：

- 94 个正式资产节点；
- 70 个资产实际参与已登记血缘；
- 80 条资产边；
- ODS：57 登记 / 33 参与；
- DWD：33 / 33 全部参与；
- Research ADM：4 / 4 全部参与。

边分布：65 条 ODS→DWD、14 条 DWD→ADM、1 条 ADM→ADM。ADM→ADM 是 `candidate_daily` 到正式 `static_industry_mapping` 的受治理派生，不是临时 process/projection。

主图不存在 `process:*`、`projection:*`、`source-field:*`、Feature、Label、semantic、consumer 节点。物理加工函数、SQL/CTE、provider projection 仅保留在 transformation/evidence 中。

### 强制反例

`dwd_adjustment_factor.effective_from`：

- ODS `adjustment_factors_pit.effective_date`
- → DWD `dwd_adjustment_factor.effective_from`
- → ADM `candidate_daily.factor_effective_from / adjustment_effective`
- 3 个正式资产节点 / 2 条边 / boundary=0 / omitted=0。

`dwd_security_bar.close`：

- ODS `daily_ohlcv_unadjusted.close`
- → DWD `dwd_security_bar.close`
- → P4 `candidate_daily.close_price`
- →/并行进入 `structure-research-adm.rows.close` 与 `structure-continuous-facts.rows` 的已登记精确字段；
- 5 个正式资产节点 / 4 条边 / boundary=0。

`dwd_adjustment_factor.factor_value` 没有精确下游字段 mapping：返回 `asset_context_only`，保留当前字段在中心资产内，但完整展示所属正式资产上下文；所有显示 edge 的 field mapping 均为空，不猜字段线。

## Definition / Usage 分离

Definition Dependency：30 个定义节点 / 65 条依赖边。`price.sma20@1.0.0` 位于此图，不进入 Data Lineage；工程 process/projection 被压缩为依赖边 transformation/evidence。

Usage Dependency：11 个静态使用节点 / 6 条使用边。P4 Event Library、Structure 消费者只在使用图出现；静态登记不证明某次 Job 实际消费，实际使用仍由 operation/run audit 负责。

## 查询与 UI

- Data Lineage 默认 `full`：递归到当前资产所有已登记根上游和最终下游；
- `1/2/3/5` 跳是可选 focus 模式；
- 节点预算最多 100，超限时必须显式返回 boundary / omitted；
- `/catalog` 提供“数据血缘 / 计算依赖 / 使用关系”三个独立 Tab；
- 继续复用无限 viewport、真实 fit-center、拖拽、指针缩放、MiniMap；
- edge 可展开查看转换说明、精确字段 mapping、路径、locator 和 SHA。

## 已执行验证

- Lineage V2 / Catalog unit + Platform + traceability 专项：57 passed；
- Catalog snapshot exact：748 entries / 10 gaps；
- Web lint：PASS；
- Web unit/contract：18 passed；
- Catalog TypeScript strict：PASS；
- Chromium desktop 1440px / narrow 390px / anonymous：3/3 passed。

完整仓库 `make check` 已作为 `P3-DICT-003-D` 发布前门禁执行：1602 passed / 7 个既有环境 skip。Skip 仍仅为未挂载 production expert artifacts 与未配置 PostgreSQL 测试 DSN；没有把 skip 冒充 pass。`git diff --check` 通过。

## Claim ceiling

“全链”只表示**当前已登记的正式资产关系闭包**，不是物理文件/每次 Run 的完整实例血缘。24 个已登记 ODS dataset 当前没有进入任何 DWD/ADM 业务资产边，它们仍可在 Catalog 中被发现，但不会为了视觉完整性制造关系。实际一次 Job 的消费/版本/时间属于运行审计，不由静态 Catalog 代替。
