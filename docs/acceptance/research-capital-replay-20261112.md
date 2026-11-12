# #628 B/C/D：执行后自审与交付

日期：2026-11-12（Asia/Shanghai）。主计划：[最小资金回放](../design/research-capital-replay-plan-v1.md)；负责人入口：[价值简报](../work-items/research-capital-replay-value-brief.md)。

## 1. 交付结论

B完成了保守原生消费者；C完成一次正式运行和独立算法冷建，取得相同的实际数据阻塞。本报告记录全部执行后的作者自审，独立核对指另一套算法和冷建输入；本批没有另行模型/角色审计。

候选于2021-08-04遇601186公司行动多记录经济键，受影响臂停止，保留339个已完成P2步骤。对照完成全窗。主比较为`blocked / not_evaluated`，`primary_increment=null`；#628及R4保持OPEN，研究采用等待P10用途交付。

代码正式执行提交为`10cf1d4b0d5c465afbbc667e363ff8f872a56149`；独立日历恢复及完整回归提交为`aa0dbb8a47fcc83b12306d1ef2c164371cbae07f`。文档和规划跟进复用检查须另有逐文件闭包相同证据。A及其PR #630、旧失败和样本曝光保持原记录。

## 2. 冻结输入、时钟和用途

- 来源：`rv192/quanthus-base25-sealed@1923c874b43d47e69190a6dc9546e6fd8f3d1ca3`。
- DWD manifest SHA256：`8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef`；content digest：`1d0e4bd5fbf40bf2876883faf82b98fd81e95a93a82b9d23d248759a38b3f692`。
- 126个成员，192,098,985B；2020-01-02..2021-12-31行情，2019仅日历；不读取2022+市场结果。
- 请求：`config/research/research-capital/request.v1.json`，canonical digest `sha256:9e27c9a3139a85a277ff19bac0025ed80e2535406dad8a9c8120a3b8720a5089`。
- 源cutoff：`2026-11-11T20:19:54.776092+00:00`；模拟收盘为2020–2021当地15:00，显式`quanthus.capital-retrospective-clock.v1`。原生knowledge/announced/effective时钟保留。
- 环境：CN02 `agentdock@quanthus-cn02`，Python3.10.12、DuckDB1.5.6、PyArrow23.0.1、pandas2.3.3、numpy2.2.6；25项依赖锁逐项核对。

用途为固定latest-sealed来源条件下的回顾资金研究。缺行动记录采用“精确封存集合无已记录行动”；已有例外、多记录经济键、未知条款继续阻断。无ex_date的IMPLEMENTED行动对该证券整窗关闭入场及持仓消费，不取effective_at占位日期。现金分红沿P2在ex_date入账；payment_date应收款和股息税时序不在本合同内。

## 3. 实现与执行后审计

| 核对项 | 证据与判断 |
| --- | --- |
| 原默认PIT和旧P6 | 默认frame/signal仍拒绝源知识晚于决策；旧SnapshotReplayClock保持2026范围；新时钟严格2020–2021，official run拒绝回顾fixture。 |
| 上下文绑定 | 每个资本Frame绑定同一cutoff与content；action不能向默认路径单独引入资本时钟；action日期、源cutoff、content及official状态均核对。 |
| 选股和定量 | R2闭区间[0.02,0.12]与互斥对照；原波动率公式，排序值冻结12位后按opaque listing_id；数量只用T日原生收盘及可用现金，不使用次日开盘。 |
| 交易/费用/结算 | 复用既有P2；未修改撮合费用数值、资金池结算公式。T+1买入，T+1收盘决定退出，最早T+2卖出；受阻同卖单携带。原生can_buy/can_sell分别执行和留痕。 |
| 公司行动 | 单行原生每股/比例资格与经济键歧义分开；多记录、特殊条款和未解源例外逐键hold；未知不填零。 |
| DAG及资源 | 同Job一次T特征、一次native_execution TMP，两臂共享；独立者另复制126成员、重建面板和资金。各Guardian均在2线程、6GiB RSS、4GiB job上限内。 |
| 独立会计 | 参照不调用P2/生产资金消费者；独立Decimal重算费用、收支、资金池、曲线、交易与决策。所有已完成步骤与正式结果一致。 |
| 终态与声明 | blocked臂无可采用资本指标；对照保留完整结果；全窗收益差保持待评价，当前不开训练、生产或新Worker。 |

审阅生产改动包括`capital_clock.py`、`contracts.py`、`engine.py`、`snapshot_clock.py`、`strategy_replay.py`、`native_capital_adapter.py`、`native_capital_replay.py`及特征/运行/参照脚本。资金费用及结算相关diff与原P2比对，关键负例和完整合成双年路径通过。最终自审未发现待修复的本批实现缺陷。

### 独立参照的一次必要恢复

`independent-a1`在资金核对之前失败：原生包包含其他交易所日历，参照遗漏原R2冻结的两个required_venues，触发范围断言。正式实现已有该过滤，正式结果不变。

恢复合同`config/research/research-capital/independent-recovery.v1.json`绑定原请求、原正式Git、原结果digest，变更只限参照脚本、额外venue合成回归及恢复声明三路径。TPlan记录`targeted_fix`，仅增加一次`independent-a2`冷建；该次通过。失败日志、73.953秒成本和旧Git一同留存。

## 4. 运行事实

| 项 | 正式结果 | 独立核对 |
| --- | --- | --- |
| 候选 | blocked；2021-08-04，601186，ACTION_EVENT_AMBIGUOUS；339个P2步骤、179次名义尝试 | 同一hold及全部已完成现金分/成交/订单一致 |
| 对照 | completed；末值97,682.53元，净收益-0.02317470，最大回撤0.03551944，费用5,988.47元，224笔闭合交易 | 完整财富曲线、交易、费用/资金池和449个P2步骤一致 |
| 主决策 | blocked / not_evaluated；增量null | 一致 |
| 耗时/RSS | Guardian92.452秒、3,306,156,032B | A1失败73.953秒；A2通过77.213秒、3,404,476,416B |

正式result digest：`sha256:fcc912e8935564f9bb517954a2af40310989eae0bf7c33d6ddca331153212070`。
独立result digest：`sha256:5079b01af1f72f45d595e0284b99861c5eee289da3346614a1348788a8270b92`。

## 5. 检查和证据交付

原正式Git专项166通过，完整`make check`为8,974通过/35跳过/62 warnings，pytest866.38秒。变更范围Ruff及8个生产模块mypy通过；本地缺可选依赖导致的早期失败保留。

恢复Git专项19通过；完整`make check`为8,974通过/35跳过/62 warnings，pytest843.26秒，make848.13秒，exit0。完整日志SHA256为`4a894608737db335689d53625ce5cd59d8192e66bb5d3bbc306debb4b51393cc`。最终文档/规划检查与闭包复用证据随固定交付记录追加。GitHub-hosted CI按仓库规则使用本地验收路线。

文档/账本跟进已逐文件核对2,304个其余受跟踪文件与恢复Git完全相同，闭包SHA256为`fe03baf628e07e2af6784f88b8aab226204bc36c60eb34328862ee5b4ba30cb6`。`docs/`和`config/planning/`另跑受影响检查；已有无关`.github/PULL_REQUEST_TEMPLATE.md`脏改不在交付中。

CN02隔离根：`/srv/agentdock/quanthus-ops/research-capital-replay-20261112`。固定结果包使用资本研究专用manifest和READY，文件名以`RESEARCH-CAPITAL-001-T1-A1-`开头；失败参照另带`independent-a1`，恢复参照带`independent-a2`。结果与完整日志可用确定性gzip保留，不删失败以迎合4MiB预算。

结果发布到私有HF `rv192/quanthus-research-r1` 的`runs/RESEARCH-CAPITAL-001/RESEARCH-CAPITAL-001-T1/A1`；发布前核对正式及成功参照Guardian/身份/digest，发布后固定OID逐文件全回读。长期有价值的结果、来源冲突、审计和收据保留在CN02 `retained/`，TMP及源副本2026-12-12起由研究维护者复核，审计或恢复需要时延长。

## 6. P10 #556：可直接执行的精确交接

只处理本消费者实际持仓遇到的键：security `security_afaa576cda4f2b0bd37af3b3395a27d8`，listing `listing_0621c4e83fdd19ed237e5c3b397ddd10`，601186，record_date2021-08-03，ex_date2021-08-04。

| latest action | 来源 | 每股现金 | 原始artifact SHA256 |
| --- | --- | ---: | --- |
| corporate_action_2129f0de9a107452e1fa088c88481d4c | base22_f10/dividend_implemented | 0.23 | 9744415f523c0771e22e65937e70817f789341dd6137fd76b047bd5e2f8e9fef |
| corporate_action_cce1b9a2654d80f34bea54f2f3ad3dcc | dividend_history | 2.30 | a955a17c4930714a889854a812905ea0673fe4fc28074d1b2b6ce5dcfcd01ac5 |
| corporate_action_e75033f34702e038efd7ce183f9f52bf | p10_retained_eastmoney_implementation | 0.23 | 0d71e207cf079d60aba0f913e31eb4298fa3cbb6ba50eb7ab94d55263e63a11e |

同一e750的旧历史版本有`knowledge_to`，不计为第四条latest。现有`base22_action_authority`、`base22_canonical_action_revision`、`base22_cash_restatement`已只读核对：需要原始计划文本/单位、raw及receipt闭包和真实新知识时钟。按10倍关系折算或二比一投票都不满足该来源规则。

建议下一笔最多45分钟，仅核对现有原始绑定和已有已资格修订。P10交付内容为：源行到经济事件的副本/替代/组合关系、每股权益、作用范围、用途版本、知识时钟、异常全集、原始SHA/receipt与回放证明。证据不足则交付明确缺口和后继成本；新采集单独分配。本会话职责切换问题仍待Owner答复，本批未启动P10修复或改封板。

研究恢复按新用途版本重新资格，固定新请求、累计成本和有限次数，保留本次正式及两个参照attempt；当前请求的正式次数已用完。后续遇到的新未知继续记录实际键和影响，不逐次放宽守卫。代码合入由原集成专线执行，本会话保留最终审阅入口。

## 7. 固定发布与在途收尾记录

固定交付：私有HF `rv192/quanthus-research-r1@0c049aa223232abf04d56261d170ad618443f3f0`，目录`runs/RESEARCH-CAPITAL-001/RESEARCH-CAPITAL-001-T1/A1`，78文件/712,321B全回读一致；manifest SHA256 `07852757b1b663fd8b536faaed222126f3f4919c3868a25c4f73fcae6f690bf7`。CN02长期根`/srv/agentdock/quanthus-ops/research-capital-replay-20261112/retained/package`；收据为上一级`publish-readback.json`。

首轮原子发布及全回读132.16秒，采用一次发布次数。包内报告为发布前快照；本节、最终文档/规划检查和TPlan终态作为独立sidecar长期留存，原包保持固定。两份Guardian的成功状态与金融比较的source hold分别记录。

恢复Git之后只有文档/规划账本修改；完整检查复用上方2,304文件闭包证明。当前本地Python3.12缺pytest，受影响文档/规划回归改在已锁定CN02环境执行，最终收据追加在本节。

最新main通过GitHub API核到`28f6081b950dcf2b271ea81e3c8b8c79bc98d75d`；普通git fetch本次返回Empty reply，未将缓存origin/main冒充最新。已读新CONTRIBUTING在途收尾要求。main新增的META-GATE-001账本均为TRACE无模块变更观察，研究三模块当前facet不受影响；集成专线需保留两支追加历史，并检查新主线回归。

本研究分支精确叠加于PR #630 head `05e3d774f49d79e93996527580233d803046cfb1`；相关分支与隔离目录留给审阅/集成，待后续PR、未合入提交和唯一证据依赖解除后再归档。无关PR模板修改保持原样。

冻结请求scope.simulation_clock保留A阶段“待资格”说明文字，实际执行以同次冻结的显式资本时钟schema、cutoff、content绑定及§9合同完成核对；后继恢复请求应清理该历史描述，旧执行请求保持原身份。

交付[Draft PR #631](https://github.com/rv198-star/Quanthus/pull/631)，base为#630。最终文档/账本树`78efacaac9510e1f50d54bd6facc6dd60e459c12`在CN02通过planning-check和64项文档/治理回归（1.82秒），issue元数据对账为RECONCILED。随后仅本报告和价值简报增加检查及PR定位文字，文档检查再行核对。

普通HTTPS推送50秒超时后，经GitHub Git Data API交付相同树，使用非强制引用更新；本地未推送文档提交`c872166d43093b73c5d87461fd6bd1064d08edda`与GitHub提交`c2b310ed7d975e532933d231482282edb6b7fe67`仅author/committer时区表示不同，epoch与内容相同，旧提交保留在本地`refs/keep/research-capital-local-docs-20261112`。后续检查绑定GitHub实际提交和树，具体交付映射/运行记录作为CN02 closeout sidecar保存。
