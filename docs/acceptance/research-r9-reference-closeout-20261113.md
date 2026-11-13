# R9 RT2 / A3 / canonical A4：工程交付与学习阻塞验收

| 字段 | 状态 |
| --- | --- |
| Scope | #662/#665；R9 milestone23；#542参考资格、#543/#544学习用途 |
| Result | 当前固定学习分支`CLASS_SUPPORT_FAILED`；R9真实学习KR未完成 |
| Engineering | 精确GitHub `e043e67453c47e1d84d34248b7927f5b2dea112a` 已在CN02本地验收 |
| Source | HF `rv192/quanthus-base25-sealed@1923c874b43d47e69190a6dc9546e6fd8f3d1ca3`；126成员192,098,985B |
| Evidence root | `cn02:/srv/agentdock/quanthus-ops/research-r9-a3-20261113`；旧A2/RT2目录保留 |
| Owner | 明确继续R9，除无法解决阻碍外努力完成；当前硬门与未完成KR如实交付 |
| Parent | [S02批准语义](../design/stage-orderly-consolidation-boundary-repair.md) §§2–7；[探索治理](../design/exploration-governance.md) |

## 当前结论

源与模型通道均已可用，canonical参考提示的实际偏差也已修复。最终16份意见经独立语义QA仍是0正8负，守卫要求每类至少2。固定合同内无后续学习路径；不再支付其它角色参考，也不拟合、更新、调阈值或更换样本。R9里程碑及原学习工作项保持开放，当前工程成果可单独合入main。

## 真实执行链

| 批次 | 实际结果 | 后续处置 |
| --- | --- | --- |
| A1 | 64中56源可用，guard仅3可用；原资格截止晚854.020秒 | 固定批次停止，失败保留 |
| A2 | 源资格先行：256候选/229合格/固定64全部完整准入；8次研究CLI出现429 | 通道有界恢复，原失败计费 |
| RT2 | 同A2输入，16有效意见；独立153引用QA，0正8负 | 后段NOT_RUN |
| A3 | 一次1536池、1347源可用；六组R0-support容量9/24/5/26/41/12；取固定64、32/32分层 | 源/选择独立GO |
| A3原参考 | 16有效意见，173引用QA；0正7负1未知 | 原rubric分支停止；跨合同发现目标符合性缺口 |
| canonical A4 | 同64/角色/包，修复提示；16有效意见、137引用QA；0正8负 | 正类0<2，学习硬门失败，唯一协议修复结束 |

A3一次源执行29.569秒、峰值RSS807,161,856B、输出41,744,894B。旧A2资格输出字节回归匹配。独立角色从126物理成员重算身份人口、固定1536、全部61440会话坐标、1347精确Decimal谓词、哈希首取和最终2560条admission，未发现必要P1/P2。A4修复前又从原始HLC定点核4个已消费guard案：量纲、20/20窗口、21close效率公式和四位归一化均一致。

## 参考协议修复与实际QA

原提示的必要中心稳定/泛化内部不稳定否决，与已批准canonical语义发生偏差。修复只对齐已有低方向效率、实质窄幅、实质收缩、无连贯方向迁移的正命题，中心移动和带内往返自身不作否决。R0四个旧谓词仍作为固定采样代理和简单比较基线；参考不读取其输出或特征。

独立只读语义预审覆盖紧缩高往返、中心漂移无迁移、中宽双向失稳、收缩且迁移、窄而不收缩、收缩但仍宽、缺输入七类合成边界。修正三值逻辑：TRUE须全部必要条件有支持；任一必要条件有明确反证可FALSE；余者UNKNOWN。没有用合成意见替代市场参考。

代码前审发现并修复了两类P2：canonical声明缺失会静默回旧提示；同64下旧票可能错作新协议意见。现在新schema必须显式版本与SHA，raw/每调用/QA绑定请求及提示；VALID必须有字面`call_started=true`及准确prompt SHA。旧合同无新元数据时保留原提示/序列化行为。

A4的8例反证为4例全窗扩张、3例实际占据区间仍宽、1例跨早中晚持续下移；未见单凭带内往返或中心移动否决。QA只核验或排除原意见，未改标签、增加第三票或进行结果导向修复。

| 绑定 | SHA256 |
| --- | --- |
| selected identity | `33aab2d6c9ca99c1b4dc4c0695c377723a2a82fbec221e8592222ab3a2d2b449` |
| qualified cases | `8d6c981b378d4cbd67187654b3cd31ae2d2df4f1632bbdf00d804d3f7d773d95` |
| blind packets | `eb640c683d372e7a2a507b68159b47a29c215db647f5423edc460b31506efe19` |
| A4 request | `ff2e0e8c7352e6b1d3a77cb3d1d32b14beb7f1928502827cf05b92f5ac6cbf66` |
| canonical rubric | `26876e84b4d60e09932c995f1406baea97d55cb4326ff3ceaed32036d7376979` |
| A4 raw reference | `5a860f7a72a5dadfab9eb89fd8fb95f00c55252c49fea3bc1d93b8e1905956b3` |
| A4 QA sealed | `9a2473a58dc8762899f99f51c5bf93e678ea478ae46f1fb0cd3d66f4d380d643` |

## 工程验收

精确e043e674，Linux/Python3.10.12，独立前审必要P1/P2=0。47个针对性测试通过；变更4文件Ruff检查/格式通过，2模块隔离mypy通过。完整`make check`包括原base的维护选择、doctor、compile、pytest和规划检查：**9298 passed、35 skipped、63 warnings**，退出0，871.518秒；log SHA `c8d997ae5b7087fd40dc76ce5be35794e99a124303ebdab85ac1573923ae5ba4`。

35跳过依赖可选sklearn/torch/astock、PostgreSQL、生产验收挂载或root内核环境。GitHub托管CI未作为验收来源；无管理绕过或新增runner。最终文档/结论账本后续提交须证明源码、运行请求、依赖和测试闭包与e043相同，并重跑受影响的文档/规划/不可变历史检查。

## 成本、产物与生命周期

研究CLI实际56次＝原8失败＋RT2 16＋A3 16＋A4 16；A4执行377.950秒。原08:10:14UTC起点、原16:10:14截止、K1晚854秒保留；新增tranche自14:42:00.588931开始，参考17:12/总17:42不延长。新增修复允许最多128、A3与修复累计144、全R9最多168；实际未用满额度。准备、通道失败、工程验收、审阅、等待、传输、交付均累计；精确活跃模型/账单与更早成本未知。

HF私有仓`rv192/quanthus-research-r1`固定revision **`55224432f1c23f18334debd6cd365e48e6983f68`**，288文件、41,584,250B全部按size/SHA读回。回执SHA为`f3788d6a8325a5aaaa87c6fa5293a611a6ab8e71bb968b745c9d64278f88a243`；各批manifest分别479af94c…/dd47886f…/fb4d7f3a…，完整摘要在回执。目录分别对应`RESEARCH-R9-LOCAL-001/T1/RT2`和`RESEARCH-R9-A3-001/T1/A3`、`A4-canonical-reference-repair`，完整目录名在publication回执；旧A2原固定版本保留。引用任务/批次和manifest可直接定位证据。

只清理已退出验收的专属TMP及已验证HF读回缓存；源126成员、原失败/引用/模型、生产和原dirty不动。有持续保存价值的输入资格、原意见、QA及控制回执保留CN02；可恢复代码在GitHub，证据在私有HF。普通合并、远端head及本地工作区保留/释放身份由最终交接记录，不提前宣称完成。

## 精确未完成KR和恢复步骤

- 新v0 fit、条件更新、same-evaluation FP/FN、retention版本选择、学习冷复现均0；当前仅有QA/支持度门，实际学习编排尚未实现。
- 当前固定guard正类支持0<2；不以剩余预算、旧汇总正类、补样或阈值修改绕过此门。
- 恢复首先需要可解引用、同canonical语义的双类参考资产及原train/guard/update/evaluation支持度、公司/时间/episode/key和月块隔离；或独立完成有正负语义锚的参考校准，再事前冻结新研究。现有56/128/87等旧汇总不能直接提供逐例训练标签。
- 若学习资格成立，v0调用`fit_head`与`revision_policy`；更新用`head2_revision.begin_update`，保留train/update各0.5及guard retention；raw≥0.5统一统计，先封存预测再解封eval。冷线从独立126成员物理副本重建原A3池及同64，字节绑定排除机器路径/耗时。

当前不申请更多Worker，不改生产或交易/P6，不进行P10采集或源修复。项目下一项有决策价值的投入是参考可操作性与双类资格，之后才是学习计算。
