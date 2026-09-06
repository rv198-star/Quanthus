# P3-DICT-001: 维护数据、指标与语义目录

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked |
| Scheduling | separately deferred backlog; non-blocking for P3-STRUCT-004 B1-B5 |
| Governing work item | `P3-DICT-001` |
| GitHub issue | `rv198-star/Quanthus#215` (open; created 2026-09-06) |
| Parent/owner | `../design/lld/03-label-feature-system.md` |
| Cross-domain source | existing LLD 01 ODS/DWD dictionary and lineage, not a new warehouse authority |

## 诉求

建立可以持续维护的数据与指标字典，让人和AI能够可靠查询：某数据/指标是什么、归哪类、
来自哪里、如何计算、在哪种时间/坐标口径下可用、当前版本与验收状态是什么。
这是后续独立任务，不阻塞当前 Facts v2 的边界、时间契约和最小纵向切片。
本页只登记任务范围，不实现完整字典、不改现有指标公式。

## 不要从零再造一份平行真相

复用并关联：

- `docs/design/lld/01-data-foundation/ods-dwd-data-dictionary.md`；
- `docs/design/lld/01-data-foundation/ods-to-dwd-lineage.md`；
- `src/quanthus/feature_registry.py`、既有特征配置及其源公式；
- `src/quanthus/label_registry.py` 与已批准的标签卡；
- `config/structure-semantic-catalog.v1.json` 与 Facts v2 的对象/时间边界。

字典是这些权威定义的可查询目录与维护流程，不拥有另一套公式或数据值。

## 交付范围

1. 数据集/字段字典：粒度、证券/上市身份、类型、单位、坐标、源字段、源与平台可知时间、
   修订有效区间、缺失含义、质量条件、血缘、更新方式和使用边界。
2. 指标/计算规格字典：规范概念ID、参数化规格ID/摘要、显示别名、输入、精确公式、
   平滑/初始化、窗口/会话、含T规则、复权/数量坐标、空值策略、numeric policy、代码版本、
   所有者、消费者和可用/验收状态。
3. 命名与兼容：旧ID不重写；同名不同公式有不同规格；关系分 exact-equivalent、related、
   replacement，只有通过等价验证的关联才可成为执行别名。
4. 维护机制：定义变更与文档/目录检查进入同一变更；检测重复、失效链接、单位缺失、
   未登记依赖、前缀归属冲突和静默公式替换。可生成只读Markdown/JSON索引，UI按需求另议。
5. 计算权威：可复现派生指标由Quanthus统一计算，优先复用已有计算或固定库；供应商指标
   标注来源、公式、坐标、时间与授权范围，默认独立对照，不因同名替换内部权威。

## 首批必须核对的例子

- `price.sma20@1.0.0` 的输入是总回报指数、单位index，不等于原价MA20。
- `price.rsi14@1.0.0` 保留既有总回报简单窗口定义，不静默改成Wilder RSI。
- MA10需明确SMA/EMA及输入、窗口与初始化；MACD柱状图缩放需明确。
- DWD `turnover` 到 `amount` 的映射不意味着有换手率；换手率需要合格的历史分母。
- 同一历史日期的事后修正版不能被目录标成当时已知；区分实际平台历史、公开证据重建、回顾视图。

## 验收与非目标

至少交付一个从既有注册源生成的可查目录、上述例子的回归检查、新增/修订/废弃流程、
缺失条目台账与所有者。目录必须明确区分 planned、implemented、verified、accepted scope。
不要求一次性实现全部MACD/CCI/KDJ等指标，不重建DWD，不开放13状态、ML、Shadow或实盘。

当前任务仅登记并排期；`P3-STRUCT-004` 不依赖此任务完成。它需要的少量新概念由当前
语义合同登记，后续字典任务再汇总，不能用全量字典建设阻塞当前工作。
