# R11 当前价值与决定

当前工作项[#690](https://github.com/rv198-star/Quanthus/issues/690)把C13候选跨日身份和C15固定桶/有限储备/账户风险组织成一个P12 D1-B采用问题。研究负责人负责设计、内部验收与整合；DS完成批量实现和自测；审计只接R11合并最终包。

首包已核对main `87d886986c522bd5982d9fa71061461e4b0550c2`上15项源绑定、P12v8六份合同，并登记A01–A07的用途与缺项。现有持仓/排序/有限池结算复用，新核心只补候选观察衔接、启用前补足、迟到现金差额及整户风险容量，供研究与P12 W3/W4共用。21个独立合成golden在实现前冻结。自审将风险容量修正为保守成交后权益，1.0.0未执行草案保持历史，当前合同见[CURRENT](../../config/research/r11/CURRENT.json)。

原专家接受包semantic_decision_authority=inconclusive、发展reader的PIT用途限制、日常账户阈值和跨日重新择时缺项均保留。首包只做元信息和合成工程，新增市场读取、付费参考、训练、资金回放为0。后段完整比较合同在逐项前置与完整成本核定后继续；R11采用结论尚待实际证据。

144项受影响检查与精确base规划/追加历史通过。Mac/Python3.12完整检查出现790failed/8570passed/38skipped，其中两个代表性/proc和private-var路径失败在精确base复现；原始日志保留，不给其余失败统一豁免。已核对CN02既有Python3.10完整依赖环境，DS代码经GitHub固定后从独立任务工作树fresh验收，保护节点已有变更。

4主动小时为此前准备建议与当前操作保护，不写成新增Owner逐项预算。首DS包预计60–120主动分钟，150分钟墙钟/2400脚本CPU秒/2GiB/128MiB；模型与未仪表化成本unknown_not_zero。P12原W1/W2/W3/W4工程估计与本次研究实际成本分列，共用准备只计一次。原R6–R10失败、曝光、成本与结论保留。

[设计](../design/research-r11-d1b-v1.md) · [采用覆盖](../../config/research/r11/adoption-coverage.json) · [消费者](../../config/research/r11/data-consumer-demand.json)。DS整包返回研究负责人，完成内部整合后再推进R11合并终审。
