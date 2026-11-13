# R9：同一固定输入的参考协议符合性修复

| 字段 | 状态 |
| --- | --- |
| Design status | approved — Owner持续推进R9授权，独立方向审阅确认具体canonical接缝 |
| Delivery gate | open；先语义/代码预审，再逐关参考与学习 |
| Work item | P5-RESEARCH-R9-STRATIFIED-001 / #665；父#542、R9 milestone23 |
| Parent | [S02边界设计](stage-orderly-consolidation-boundary-repair.md) §§2–7；[探索治理](exploration-governance.md) |
| Contract | `config/planning/r9-canonical-reference-repair.v1.json` |
| Code / tests | `src/quanthus/r9_reference.py`；`tests/unit/test_r9_reference.py` |

A3源及R0分层已通过独立核验；原提示的guard是0正7负1未知，原试验后段停止。语义QA当时核的是冻结提示内部符合性。跨合同审阅发现，该提示的强制中心稳定和泛化内部不稳定否决，与既有S02设计冲突。修复目标是恢复已批准的语义：低方向进展、实质窄幅、实质收缩、没有连贯方向重定价。中心移动为修饰，紧缩带内反复往返自身没有否决权。

这次只修改有版本/摘要绑定的参考提示。同一64身份、角色、源、HLC40、七特征和分层身份完全保留；无新池、补样、角色转移或门槛修改。旧提示、旧意见及原支持度失败保留其用途；新上下文看不到旧票、计数、R0、特征或角色。新的意见独立封存，不能与旧意见混票。R0仍是旧比较基线，不决定参考真值。

预审必须涵盖：紧缩窄带高往返、中心移动但无连贯重定价、窄而不收缩、收缩但仍宽、窄幅收缩且连贯重定价、信息不清六种边界。前两种允许正命题，后三种不得仅凭某一个正条件判正；不清保留UNKNOWN。语义预审由只读独立角色完成，代码测试只验证版本/摘要隔离，不能自动授予标签。

仍按guard→evaluation→train→update及原双类/公司/月块地板逐关支付。任何支持度或QA失败即停止后段，无第二次语义修复或投票。通过后仅原200步新v0与原loss触发的一次100步更新；retention、0.5阈值和FP严格减少且FN不增加的选择门保留，eval预测提交后解封。

本次修复最多新128次CLI，A3及修复合计144次、R9全部研究CLI合计168次；已发生的40次和所有失败继续计费。保留08:10原起点、14:42新增tranche及16:10原截止偏差。参考17:12、总17:42UTC墙钟均不延长；等待、预审、工程与交付全部计入。共享源与生产不改。
