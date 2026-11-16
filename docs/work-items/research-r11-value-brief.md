<!-- r11-lead-convergence:start -->
# R11：保留共享核心，停放完整历史比较

本批为P11与P12补齐同一份候选/固定桶/有限储备/账户风险纯政策。当前实现1.1.1已固定到GitHub `deb4422ef5ae2994328729d89cdbefc3daa835fd`；研究负责人完成两轮内部复核和修复，CN02/Python3.10最终完整检查9388 passed/35 skipped/63 warnings，Ruff及五模块mypy通过。Task[#690](https://github.com/rv198-star/Quanthus/issues/690)和draft PR[#691](https://github.com/rv198-star/Quanthus/pull/691)保持开放，等待整批终审。

可保留的工程价值是P11/P12共用一份资金与风险数学：同一原子入场入口约束原专家上限，返回并固化平衡资金分录，重复命令保持原数量/预约/摘要且不重复入账；旧周期迟到现金不会污染新周期B。原T+1窗口与实际订单/持仓事实分开。21个冻结预期、3条因果故事、原P2费用/持仓接缝及5项新增独立回归随包保留，原冻结JSON未改。

完整历史比较暂缓、日常采用false。A01多日新买点/rearm语义、A02专家semantic_decision_authority=inconclusive、A03逐决策PIT/精确供给manifest、A04实际账户阈值和快照、A06实际费用/窗口/多笔成交、P12事实/事务与OPS/USE均有具名缺项。当前研究有效性为not_evaluated；完整问题按有证据的受限停止组织，交审计判断收敛。

投入偏高，主要返工来自资金状态/命令重放/专家边界和验收器遗漏。原Mac失败、两次旧Linux检查、DS两包、内部反例和GitHub传输失败全部保留。DS002从冻结开工到最新回合完成4442.266359秒，超过3600秒保护842.266359秒；原641.176574秒是较早快照，追加整回合对账。原累计脚本CPU2078.412495秒是当前lead之前的已测小计，当前lead与发布另计；模型和未仪表化成本unknown_not_zero。

下一笔不自动启动46–92小时研究估计。先由P12提供实际消费者账户事实/参数，由P10交接现成合格用途/时钟manifest，P11核定专家用途；多日触发须先明确一个改变真实决策的问题。条件齐备后才重估最小旧control/新组合比较。实际节省工时尚待P12接收验证。原R6–R10成本、失败、开发曝光、B/v0保留及v1拒绝决定延续。

[完整交付与P12接收](/Users/william/Documents/Codex/2026-11-14/jie-s/outputs/quanthus-r11-convergence/README.md) · [设计](../design/research-r11-d1b-v1.md) · [合成验收](../acceptance/research-r11-synthetic-preparation.md)
<!-- r11-lead-convergence:end -->

<details><summary>历史：设计、DS首包与1.1.0修复</summary>

# R11 当前价值与决定

当前工作项[#690](https://github.com/rv198-star/Quanthus/issues/690)把C13候选跨日身份和C15固定桶/有限储备/账户风险组织成一个P12 D1-B采用问题。研究负责人负责设计、内部验收与整合；DS完成批量实现和自测；审计只接R11合并最终包。

首包已核对main `87d886986c522bd5982d9fa71061461e4b0550c2`上15项源绑定、P12v8六份合同，并登记A01–A07的用途与缺项。现有持仓/排序/有限池结算复用，新核心只补候选观察衔接、启用前补足、迟到现金差额及整户风险容量，供研究与P12 W3/W4共用。21个独立合成golden在实现前冻结。自审将风险容量修正为保守成交后权益，1.0.0未执行草案保持历史，当前合同见[CURRENT](../../config/research/r11/CURRENT.json)。

原专家接受包semantic_decision_authority=inconclusive、发展reader的PIT用途限制、日常账户阈值和跨日重新择时缺项均保留。首包只做元信息和合成工程，新增市场读取、付费参考、训练、资金回放为0。后段完整比较合同在逐项前置与完整成本核定后继续；R11采用结论尚待实际证据。

144项受影响检查与精确base规划/追加历史通过。Mac/Python3.12完整检查出现790failed/8570passed/38skipped，其中两个代表性/proc和private-var路径失败在精确base复现；原始日志保留，不给其余失败统一豁免。已核对CN02既有Python3.10完整依赖环境，DS代码经GitHub固定后从独立任务工作树fresh验收，保护节点已有变更。

4主动小时为此前准备建议与当前操作保护，不写成新增Owner逐项预算。首DS包预计60–120主动分钟，150分钟墙钟/2400脚本CPU秒/2GiB/128MiB；模型与未仪表化成本unknown_not_zero。P12原W1/W2/W3/W4工程估计与本次研究实际成本分列，共用准备只计一次。原R6–R10失败、曝光、成本与结论保留。

[设计](../design/research-r11-d1b-v1.md) · [采用覆盖](../../config/research/r11/adoption-coverage.json) · [消费者](../../config/research/r11/data-consumer-demand.json)。DS整包返回研究负责人，完成内部整合后再推进R11合并终审。

DS 首包已形成 `quanthus.product_daily` 纯政策核心和独立合成验收入口。21 个冻结 goldens、3 条重建故事、CAS/幂等、同批 reserve、迟到费用、保守最大整手及 P2 synthetic buy→hold→exit→settlement 接缝均进入同一实现闭包。当前仍是非授权 synthetic preparation；真实账户阈值、A02 semantic decision authority、发展 reader 的 per-decision PIT 证明和历史完整比较继续由后段采用合同处理。

P12 W3/W4 只接收纯 state/command/receipt 接口并在自己的事务层持久化，不复制资金数学。后段完整研究新增工作估计为 46–92 主动小时，P12/P10 原估计另计；精确 compute、窗口和产物量待冻结 manifest 后报价。

内部验收发现七项可执行逻辑缺陷、strict-input 缺口和 Q02 actual 指标错误后，DS 已完成 R1–R10 整批修复并升级纯政策实现到 `1.1.0`。修复闭合 completed-settlement history、reserve 债务优先、unsettled-cycle reuse、canonical command binding、C13 临时阻断重评、原 expert entry action、composite entry atomic replay、正 E_floor、typed money/boolean 边界及逐字段 frozen oracle。Daily adoption、A01/A02/A03/A04/A06/P12 事实层缺项保持不变。

</details>
