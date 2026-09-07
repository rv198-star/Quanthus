# Catalog Lineage V2：正式数据资产血缘

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Work item | `P3-DICT-003` |
| Parent | `data-indicator-catalog-productization.md` |
| Machine plan | `../../config/data-catalog-lineage-v2-delivery.v1.json` |
| Review | 数仓主审通过；技术架构、量化业务方辅审通过 |

## 1. 问题与目标

P3-DICT-002 的静态加工图把正式数据资产、实现过程、临时 projection、定义对象和消费者都提升为图节点。该模型适合工程追查，但不适合作为用户理解的 Data Lineage；完整展开会把工程中间态误认为数据资产，并使“全链”失去业务含义。

V2 把主血缘定义为**正式受治理数据资产之间的血缘**。当前正式层级为：

```text
ODS -> DWD -> Research ADM
```

未来只有正式受治理的 DWS 或其他数据层进入运行权威后，才增加新的资产层。旧 ADS 继续按 ADR 0013 作为历史/重放产物，不进入当前主血缘。

## 2. 正式数据资产定义

一个主血缘节点必须具备稳定技术身份，并由机器契约、版本/manifest 或明确的受治理 ADM policy 绑定生命周期和用途。当前节点只接受：

- ODS 正式 dataset；
- DWD 正式 table；
- Research ADM 中正式登记的业务 dataset；
- 上述 dataset 内的字段只作为资产节点内部的字段粒度，不与 table/dataset 并列成大节点。

以下对象不得作为主 Data Lineage 节点：

- `process:*` 加工步骤；
- Job-local TMP、Direct-DWD 临时 projection、兼容 projection；
- source-field 辅助节点；
- Feature / Indicator 定义；
- semantic concept / family；
- Label；
- Expert / Strategy / consumer；
- manifest、receipt、coverage audit 等治理 sidecar。

这些对象不是删除：加工步骤降为 edge metadata；定义对象进入“计算依赖”；消费者进入“使用关系”；治理 sidecar 作为资产证据。

## 3. Research ADM Registry

Catalog 新增显式 ADM Asset Registry。首批登记当前已经具有稳定身份、用途和受治理边界的 Research ADM：

1. `p4-event-research-adm.candidate_daily`：P4 Event Library 的正式候选/资格日级输入数据集；
2. `p4-event-research-adm.static_industry_mapping`：P4 静态非 PIT 行业映射数据集，由候选证券身份、DWD 行业关系和冻结补充证据形成；
3. `structure-research-adm.rows`：Structure MVP 的薄 Research ADM；
4. `structure-continuous-facts.rows`：Continuous Structure Facts v1 的正式事实 ADM。

`quanthus.structure-v2-adm.v1` 当前是固定 B1-B5 Run 内的 manifest-bound 结果面，但尚无独立稳定 ADM asset policy；V2 不凭名称把它提升为 Catalog ADM dataset。后续如果需要把 Facts V2 作为长期共享数据资产，应先单独冻结其 ADM asset identity/fields，再加入 registry。

Registry 对每个 ADM dataset 登记：ADM id/version、业务数据集 identity、用途、权威配置/实现、字段、上游 DWD/ADM 资产、精确字段映射、转换说明和正式消费者。不得把 TMP 只因为可复用就登记成 ADM。

ODS 侧仍以逻辑业务资产为节点，不把 daily/weekly delta、hotfix 或容器 member 单独画成节点。对于旧 Base-only Catalog 未覆盖、但 DWD 机器契约已明确声明为 modeled recurring business asset 的 `raw_f10`、`financial_statements`、`security_lifecycle`、`industry_membership`，V2 以精确机器契约指针补登记正式 ODS dataset；物理发布实例只保留在 edge evidence。当前 33 张 DWD 表因此均至少接入一条正式 ODS/ADM 资产血缘，不靠伪造中间节点补齐。

## 4. 三种关系模型

### 4.1 数据血缘（默认）

回答“数据从哪里来，形成了哪个正式数据资产”。节点只有 ODS/DWD/ADM。

- dataset/table 查询：显示当前资产的完整已登记 ancestor + descendant closure；
- 字段查询：节点仍是所属正式资产，字段显示在节点内部；只有精确字段映射才能沿字段链继续传播；
- 没有字段级映射时回退为所属资产完整血缘，并紧凑标注“仅资产级上下文”，不得猜字段连线。

默认范围是 `full`，不再默认 2 跳。用户可切到 1/2/3/5 跳作为聚焦模式。节点预算仍 fail-closed：若完整闭包超过预算，必须返回明确截断信息。

### 4.2 计算依赖

回答“这个指标、Fact、Label 依赖哪些数据/定义”。Feature、Indicator、Fact、Label 可以作为此视图的定义节点；实现过程本身仍不成为节点。算法/转换名称、公式、代码证据放在依赖边上。

### 4.3 使用关系

回答“哪些正式研究/专家/策略/消费者使用该资产或定义”。消费者可以作为此视图节点，但必须明确它不是数据资产；实际某次 Job 是否消费仍以运行审计为准。

三种图不得因为 UI 方便再次混成一张图。

## 5. Edge 语义

Data Lineage edge 表示 source asset -> target asset 的受治理数据形成关系。Edge metadata 可包含：

- `transformation`：业务可读加工说明；
- `field_mappings[]`：精确 `source_field -> target_field` 映射；
- `evidence[]`：权威路径、定位与 SHA；
- `proof_level`：`asset_declared` / `field_proven`；
- `notes`：PIT、非 PIT 补充、适用范围等限制。

加工函数、SQL CTE、projection 名称只允许出现在 edge metadata / evidence 中，不成为节点。

## 6. UI

详情页血缘区域默认打开“数据血缘”，并提供：

- `数据血缘` / `计算依赖` / `使用关系` 三个视图；
- `全链` / `1跳` / `2跳` / `3跳` / `5跳` 范围；
- 继续复用已验收无限 viewport、fitView、MiniMap、缩放和平移；
- Data Lineage node 显示层级、资产业务名、精确 ID；字段焦点时在节点内部显示字段；
- edge 点击后查看转换说明、字段映射与证据；
- 默认不显示 process、projection、consumer 等工程节点。

## 7. 验收反例

必须至少通过：

- `dwd_adjustment_factor.effective_from`：ODS `adjustment_factors_pit.effective_date` -> DWD `dwd_adjustment_factor.effective_from` -> P4 ADM `candidate_daily.factor_effective_from/adjustment_effective`，只有 ODS/DWD/ADM 三类资产节点；
- `dwd_security_bar.close`：从正式 ODS 行情 dataset，经 DWD 行情表，到已登记 Structure/P4 ADM 的精确字段链；
- `dwd_adjustment_factor.factor_value`：无精确字段下游时只展示完整资产级链，不制造字段映射；
- `price.sma20@1.0.0`：不得出现在 Data Lineage 主节点中；应进入计算依赖；
- Structure Expert / P4 Event Library：不得出现在 Data Lineage；只能进入使用关系；
- 主数据血缘完整闭包不得包含 `process:*`、`projection:*`、`source-field:*`、semantic/label/consumer 节点。

## 8. 边界

本轮只改 Catalog 的登记、查询和展示，不修改 ODS/DWD 数据、ADM 物理产物、Feature/Fact 公式、Expert/Strategy 决策或执行权限。Catalog 仍是确定性只读投影，不成为第二份数据/公式权威。
