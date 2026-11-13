# R9：校准后的独立 S02 学习与版本选择

| 字段 | 状态 |
|---|---|
| Design status | approved — Owner 要求继续完成 R9；独立语义和代码事前审阅通过 |
| Delivery gate | open — 按真实支持度逐门执行 |
| Parent | [参考校准设计](research-r9-reference-calibration-v1.md)、[探索治理](exploration-governance.md) |
| Work item | P5-RESEARCH-R9-QUALIFY-001 / #662；#543/#544 研究版本选择 |
| Contract | `config/planning/r9-prospective-s02.v1.json` |
| Code / tests | `src/quanthus/r9_study.py`；`tests/unit/test_r9_study.py` |
| Predecessor | [真实校准验收](../acceptance/research-r9-real-calibration-20261114.md) |

## 目的和选择

校准的固定12例已取得2正10负及独立完整路径QA，四条件参考可操作。
本批用既有1536候选池中的独立公司检验一次S02学习、原条件更新和研究版本选择。
旧A1/A2/RT2/A3/A4、全部旧角色、原失败和费用继续封存。

源仍为固定HF1923c874的已物化2020开发输入。事前绑定原池、原64公司及校准12公司的排除文件，
固定下表后一次选择；保留1536全部候选的资格、排除与选择账本。代理只用于取样，参考标签由后续盲读产生。

| 角色/月份 | 固定代理份额 |
|---|---|
| train / 三月 | 6收缩边界、6扩张 |
| train / 四月 | 8最窄收缩、4收缩边界 |
| guard / 八月 | 4最窄收缩、2收缩边界、2扩张 |
| update / 八月 | 其余候选按原固定company/security/listing hash取8 |
| evaluation / 十一、十二月 | 每月4最窄收缩、4收缩边界、4扩张 |

最窄/边界代理共用原低方向效率与收缩采样条件，按全幅排序分别取前部及剩余集合中部；
扩张代理按扩张幅度取前部。完整算法、平局顺序和数量绑定代码及合同。
边界份额直接覆盖校准暴露的“已收缩、全窗仍宽”误报难例。任一源配额不足停止，不补样或调配。

八月同月guard/update使用不同公司，保留`train < guard <= update < evaluation`，
十一、十二月承担时间外评价。July属于原具体合同，旧安排保持原样；新布局在新意见前冻结。
64个company/security/listing/sample/episode全部互斥，源40会话和原生输入资格保持。

## 分门参考与学习

按guard、evaluation、train、update支付意见，固定Sol/Astra、两个新临时上下文、同四条件协议，
最多16/48/48/16个CLI启动，总128，零重试。独立QA逐路径、条件、引文及trace核验，只排除意见。
任何支持门失败，后继角色和学习停止；UNKNOWN及完整64机会保留。

原支持门：guard每类至少2；evaluation至少12明确、每类至少5家公司、至少2个月块；
train至少12明确、每类至少4、至少2个月块；update至少1明确。
独立评价QA可先看原意见形成支持回执，学习执行器只取得该回执；逐例评价标签另行封存。

资格全过后一次200步v0拟合，七特征、学习率0.05、L2为0.01、train-only scaler及0.5阈值保持。
原loss触发值0.6931471805599453达到才做一次100步更新；原训练/新更新各权重0.5，guard不入梯度。
未触发则NO_UPDATE，保留v0。retention要求guard正/负错误与update平均loss分别不增加。

学习执行器/评价器解封评价标签前，封存checkpoint、diagnostic、retention选择及全64个机会的R0/v0/v1预测。
同一明确evaluation集合上，v1须retention通过、FP严格下降且FN不增加才研究采用；其余保留v0。
消费者为实际可读取的S02研究sidecar，逐例给原参考/UNKNOWN、raw score及判定，汇总完整分母、FP/FN和选择原因。
本批报告代理富集分布的开发版本决定；概率字段为空，专家入口保持pending/ABSTAIN。

## 复建和资源

一次cold从原126成员原生缓存独立重建40窗资格、特征及R0 facts，重新从零拟合和原条件更新，
核对checkpoint、两版本预测、retention和最终FP/FN/采用决定；参考不重新投票。

本笔从2026-11-13T22:52:01Z起，包括实现准备、检查、失败、QA、等待和交付；
2026-11-14T01:52:01Z总截止，参考不晚于01:22:01Z且首次采集后最多7200秒。
整批共享2CPU、2GiB RSS、1GiB临时空间、64MiB输出；各角色共用study根目录累计监控。
fit/compare/cold分别使用600/120/600秒受限子进程，并受总截止约束。
此前106个研究/通道CLI启动及2个离线loopback fixture另列保留；本笔最大累计234个研究/通道CLI启动。
