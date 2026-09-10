# P3-STAGE-006：真实参考采集可行性记录

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-006 |
| Governing design | [真实参考可行性](../design/stage-order-reference-feasibility.md) |
| Prior baseline | `6423d9625e00cc8691476b37fe61ffb1f44ca107` |
| Contract | `../../config/stage-learning/order-reference-feasibility.v1.json` |
| Config digest | `sha256:f161f1931c724df05b6dff0851185fe9e8571286841b3fd06e013a3821fdaf22` |
| Current checkpoint | COMPLETED: two succeeded Jobs, twelve real-input cases, twenty-four retained simulated opinions |
| Collection result | PASS_REAL_INPUT_REFERENCE_COLLECTION |
| Descriptive coverage | DESCRIPTIVE_REFERENCE_COVERAGE_OBSERVED; not a statistical or semantic qualification |
| Independent semantic validation | NOT ESTABLISHED; one actual origin, same-session role simulation, not blind |
| Claim ceiling | Real-input collection feasibility only, no independent semantic or market qualification |

## 1. 前置与范围

上一站P3-STAGE-005已接受。用户要求继续到里程碑完成，本次事前冻结12个日期选择案例、
同源self_simulated两角色24份意见、最多prepare/resolve两个Job和无补样停止条件。
两份来源是已接受F9冷运行的保留输入，不重新清洗或读取DWD，且不读取旧160例。
当前会话已看过前序运行摘要，所以不宣称完整认知盲测或独立专家验证。

源码读写只涉及已提供的不可变包和本次新产物。意见从外部传入，runner不生成意见；
意见与prepare身份都进入DurableJobQueue input digest，防止恢复时使用另一组意见。
原source bytes、两份manifest和readiness一并保留；冷验证检查其已接受SHA与case重建，
不会把只修文件SHA的篡改当成正确证据。目录恢复优先验证，不重算或覆盖。

## 2. 发布前工程检查（历史检查点）

聚焦协议/runner/Worker测试：67 passed。首次恢复测试误把内存测试队列的已终态Job直接
改成queued，未重入队；改为显式模拟已获得claim的恢复入口后通过。这是夹具修正，
不是更改生产队列语义。行长和import格式检查在发布前处理，不修改旧业务规则。

发布前 `make check` 完成：1932 passed、7 skipped，245.55秒；doctor/compile/planning均通过。
7项仍为2项生产专家产物未挂载、5项PostgreSQL测试DSN未设置。最终55项聚焦及文档检查
通过，新增代码/Worker/同步脚本/测试Ruff与git diff --check通过。工单LLD表头初次写成
Work item而非规划解析器要求的ID，导致两项traceability失败；只修正表头后重验通过。
这些发布前检查只证明当时A/B设计与工程入口就绪，不能代替真实运行验收。
随后完成的C/D运行及收尾结果见第3—6节。

GitHub工单：A #283、B #284、C #285、D #286、父 #287。

## 3. 实际执行、封存和读回

### 3.1 发布与两次运行

执行版本 `bf5d8b11c3869bdb7c1205943779ce3f5619cbe4` 经GitHub快进推送后，只部署
Event Worker，镜像 `quanthus-event:git-bf5d8b1`，镜像摘要
`sha256:4bf5fbb59f79630d3df74bfc8bba82912137b0a810ce9edc40c0e6d97e7f8df3`。
既有部署回执的健康/ready通过，API/Web/replay-worker未替换，没有迁移或清洗DWD。

两个Job均由固定 `quanthus.structure-mvp.v1` envelope、同一精确配置和代码提交。
所有时间保留服务端原始UTC回执，不按文档文件名日期改写。

| Phase | Job / Run suffix | Started UTC | Completed UTC | State / attempt |
| --- | --- | --- | --- | --- |
| prepare | fec6e60e382f47bc8dcc796919470748 | 2026-09-10T16:55:52.634635+00:00 | 2026-09-10T16:55:57.941410+00:00 | succeeded / 1 |
| resolve | 2a380e24cb5b4f8e8816558b6fbabb5d | 2026-09-10T17:05:27.812903+00:00 | 2026-09-10T17:05:32.078062+00:00 | succeeded / 1 |

完整Job为 `job_`加上表后缀，Run为 `run_`加后缀。两个error_reason均null。
没有失败重跑、换例、补样或第三个Job。两次幂等键分别为
`p3-stage-006-prepare-20260911-v1` / `p3-stage-006-resolve-20260911-v1`。

| Binding | SHA256 |
| --- | --- |
| prepare input | `sha256:98505c36c78f5e28c89acc216bde024fc398743e6aecb71260c14ea95b350ab1` |
| prepare manifest | `sha256:fdb7cc8b3c34566fee18ea5ee00589ad12e1da6dbe74e2f624bb030d10acf5ec` |
| prepare evidence / resolve bundle evidence | `sha256:5af53e4c1b852c48ce0d5b04d2b02ef3623badf7949b87424ab7ddb941839a94` |
| resolve input | `sha256:98f9fd853236d04b724e350006cc703cbb8b6823fcda8805e35a2d5cf884949b` |
| resolve manifest | `sha256:ed8252670525962f224dee6f0bfb6c2e622b8c9f0ebc729df529a7bfe4ecfed9` |
| resolved bundle manifest | `sha256:79f8ecc5470ef2eaca85e6e7e798a50624b5b47745ca80ddf26fa831ce343eda` |
| retained raw opinions | `sha256:d281a2b37e522c44413f6738d045deac642e9b021e1c6fc3d93f789db87a488c` |
| resolved per-case records | `sha256:ab8b8319af9bf5b75091fbbb66e59ec20c192968a8296519359319f00161772d` |

产物均保留在 `/data/event-runs/structure-runs/<run_id>/`。prepare含原source-inputs、
两份源manifest/readiness绑定、案例证据与计时；resolve的 `bundle/reviews.json` 保留24份
原始意见，`bundle/resolved.json` 保留逐例解析。两者不提交Git，也不只留下不可解引用的哈希。
最终Worker终态的verified=true并绑定同一prepare manifest。通过正式状态工具读回了
prepare全部12例T-only视图、resolve的24份输入原意见、最终汇总及完整外层manifest。

Worker的独立读回是在同一执行版本下，从保留原始输入重新构建并比对，包含源成员身份、
固定选择、意见引用和逐例解析；它不是第二套独立算法，也不是第二个模型对业务标签背书。
终态状态读回没有再启动计算Job。先前一次在shell中裁剪准备视图的读取被工具安全检查
拦截，未反复尝试该方式；随后用已提供的正式状态工具获得完整视图。

### 3.2 时间与复用账本

prepare Worker起止5.306775秒，其中runner计时3.375350秒，源产物校验2.884829秒。
构建阶段357个唯一输入共享一次准入、一次Trend和一次Order构建；其记录分别约
0.010260、0.259681和0.159626秒。生成12例证据428,351字节，组装约0.043668秒。
A/B源输入字节相同，不算714个独立原始样本。

resolve Worker起止4.265159秒，runner计时1.082528秒，记录
`new_fact_materializations=0` / `reuse_prepared_evidence_plus_explicit_audits`。
这是复用prepare产物；显式校验仍会重建所保留证据，不声称整个验收过程没有重复计算。
两次Worker起止合计9.571934秒，不包含编程、回归测试、读取展示和本助手写意见。
prepare完成至resolve提交间隔569.341792秒；它包含案例展开与模拟判断，不是某个行情
算法的耗时测量，也不能据此承诺后续每例成本。小批3证券试点不与旧512证券实验宣称速度倍数。

## 4. 参考采集结果与四视角验收

### 4.1 结果及分母

固定12例均可提供主尺度和近期事实。实际输入357行，证券3个、报告日期4个；每证券
四个报告会话实际为2020-01-02、2020-02-06、2020-03-04、2020-03-31。
原选择不依赖特征或意见。24份意见实际由本助手按短线/基金两角色撰写，来源均为
`self_simulated`、`origin_id=chatgpt-owner-session-stage006`，来源数=1。

| 主尺度参考解析 | 数量 |
| --- | ---: |
| STAGE_0 | 4 |
| STAGE_2 | 1 |
| NEITHER | 3 |
| AMBIGUOUS | 4 |
| RULE_CONFLICT | 0 |
| INPUT_UNAVAILABLE | 0 |

合计12。四个AMBIGUOUS中，三个案例两角色主意见不同，一个案例两者共同保留UNKNOWN；
这些细分来自已读回的24份原意见，不能把所有模糊案例都记成角色分歧。
另外有1个近期中心描述分歧，独立于主尺度解析。原意见未被第三方裁决强制一致。

本固定小批中同时出现一致的0、2和未覆盖/模糊案例，满足事前的**描述覆盖观察**，因此
返回 `DESCRIPTIVE_REFERENCE_COVERAGE_OBSERVED`。这不是旧实验的20正例门槛通过，更不是
降低门槛到1个来给分类器发证。本批没有运行候选生成、评分或参数选择，不能计算或宣称
分类准确率、全市场发生率或收益。`primary_stage=null`。

### 4.2 量化与数仓符合性审查

量化视角检查冻结选择、原意见与终态计数的对应，并否决任何独立验证或市场外推主张。
四个模糊案例保留为真实的协议输出，而不是工程失败；同时，一个2参考远不足以支持统计
意义的稳定性结论。三证券共享四个日期，不是12个独立市场事件；来源也不是新盲测。

数仓视角核对两份既有源包绑定、完整来源保留、T-only机器视图、主/近期分槽、原意见与
manifest引用和冷重建链。所有真实运行均 `dwd_provider_opens=0`，没有从DWD重建旧160例。
只对“给定源版本的retrospective视图”作结论，不把2026年保留/生成时钟改写成2020年
当时的平台获知时间。

两视角由本助手执行符合性分析，不构成新增独立票数。当前会话事前见过前序摘要，且此次
同一会话先后看了多个日期案例；虽每例机器视图没有T后行，**完整认知盲测不成立**，
故 `full_context_blinding_claimed=false`，独立性与market_reference_validated仍false。
没有真实人类专家评审，也没有把401失败改标为成功调用。

### 4.3 短线/基金模拟讨论的实际发现

本次真正保留下来的业务分歧集中于：下行过程与低方向失稳的边界，主区间收窄但中心仍
迁移，以及最后一个会话的剧烈变化是否已反证整个主尺度。近期中心的小幅移动在两种
观察粒度下也有不同解释。这些是后继规则化研究需要说明的边界，不以当场调阈值消除。

同时观察到主尺度整理参考与近期扩张并存，及近期收缩但主尺度仍不稳定的记录，说明
分尺度接口能承载不同的业务描述；没有因此证明哪位角色意见一定正确。

## 5. 本里程碑处置与不变项

`P3-STAGE-006` 完成真实输入参考采集可行性闭环：prepare、实际模拟判断、resolve、
可追溯原意见与冷读回均已完成。接受范围是采集机制在这批真实保留输入上可用，并取得
有限的描述覆盖观察；**没有接受Stage-0/2候选或独立业务语义资格**。

交付计划关闭A—D及父里程碑、关闭prepare/resolve活动授权；两Job预算已用尽，没有
追加执行。固定runtime配置/代码保持原样供证据复现，不把停止授权写成历史代码被删除。
本次closeout只更新交付元数据、设计现状、证据和相关回归检查，不改已运行的算法或意见。

原P3-STAGE-004仍INCONCLUSIVE_REFERENCE_SUFFICIENCY，旧统计门槛和无候选结论不变；
2022—2024 arbitration不重读，2025不动；不发布正式标签、primary_stage，不启动日更、
策略消费或交易。参考证据不晋升永久ADM。以后进一步验证必须回答此次明确留下的边界
和参考独立性问题，不能把本12例重新包装为独立金标准，或再次重复本里程碑的工程建设。

## 6. 收尾验证

发布前1932项测试记录见第2节。收尾增加18项终态来源、父产物、计时一致性、来源等级、禁止追加Job和旧实验不被改判的测试。

收尾聚焦测试：116 passed，11.02秒。全仓make check的doctor、compile、pytest和planning-check均完成：1950 passed、7 skipped，pytest耗时251.00秒。跳过项仍为2项未挂载的生产专家产物和5项未配置的PostgreSQL测试DSN，不声称已执行。

静态检查初次发现同步脚本一处行长问题，已只作拆行处理；没有改动执行逻辑。最终聚焦静态检查、文档回归、提交和工单状态以相应工具回执为准。本轮不扩范围修复既有全仓静态检查问题。

收尾再次读取了resolve的实际终态、父产物绑定、24份原意见和外层manifest；第3节保留原始时间和摘要值，不因会话恢复改写。执行源码、runtime配置和已封存意见保持不变，没有提交第三个Job。

本记录支持完成真实输入参考采集可行性，不支持分类器、独立语义或金融收益资格。行情、原始意见和运行产物不进入Git；文档收尾不重新部署Worker。
