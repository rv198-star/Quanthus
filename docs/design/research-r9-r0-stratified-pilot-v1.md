# R9 A3：R0代理分层的单模块开发闭环

| 字段 | 状态 |
| --- | --- |
| Design status | approved — Owner要求继续R9直至完成或遇不可解决阻碍，独立方向审阅条件GO |
| Delivery gate | blocked — 原guard支持度失败；见[实际验收](../acceptance/research-r9-reference-closeout-20261113.md) |
| Parent | [探索治理](exploration-governance.md)；#542；R9 milestone23 |
| Work item | P5-RESEARCH-R9-STRATIFIED-001 / #665 |
| Contract | `config/planning/r9-r0-stratified-a3.v1.json` |
| Code | `r9_stratified.py`、`r9_qualification.py`、`r9_reference.py`、`r9_learning.py` |
| Tests | `tests/unit/test_r9_stratified.py`及对应qualification/reference/learning测试 |

## 问题与消费者

A2/RT2已取得真实16意见，独立语义QA确认8guard全部FALSE，当前固定样本缺正类，后续reference/fit/update按原门槛NOT_RUN。这条证据仅描述这8例；历史批次、来源和截止偏差保留。

A3是另行前瞻冻结的开发试验：在既有R0-support/other各半的源合格样本中，盲参考能否形成足够双类，从而完成新v0、最多一次条件更新及同一新evaluation上的版本选择？消费者限定#543/#544开发级识别版本评审。结果按此采样分布解释，报告同样本R0/v0/v1误报和漏报。

R0-support是有S02语义的代理输出。唯一允许的分层代理为原冻结规则四个谓词，精确Decimal值与原semantic digest不变；它应用于已批准Head2 exact40事实，不冒充完整61会话profile准入。R0-other表示不支持该谓词，独立标签由两位原rubric读者形成。

## 一次固定样本

使用同HF1923c874固定126成员，最新封存回溯视图；2020原六anchor和40会话不变。剔除A1/A2全部公司及能枚举的历史研究代码000001/300750/600519；历史全量公司名单不可达，未知曝光继续累计，开发用途不称新holdout。

新seed固定一次1536家公司，六组各256、组间公司隔离。池身份及完整窗口先于行情值冻结。全池只构建一次source/admission/Facts，保留全部不可用和strata账本；再各组按hash取R0-support/other各半：train每月6+6，guard4+4，update4+4，evaluation每月6+6，最终64。任意配额不足即停止本试验，不追加池、换日期或参数。

禁止新v0/v1输出、AI标签、未来结果和调参参与选样。参考包仍只含opaque case_id、availability和归一化HLC40，隐藏角色、身份、R0及所有特征。原资格入口固定64保持；新增1536入口仅服务本A3候选池。

## 学习与验收

原短线/基金双读者rubric、UNKNOWN/分歧保留及语义QA继续。guard→evaluation→train→update逐关支付参考费用，support地板全部不变。资格全过仅一次200step v0和原loss触发下的一次100step更新；train-only scaler、0.5阈值、正负retention及update loss守卫不变。未触发记NO_UPDATE，拒绝保留v0，不为了获得更新继续换批次。

evaluation意见封存；checkpoint、retention版本选择及两版预测提交后再解封。比较同一evaluation的R0/v0/v1 FP/FN，并进行所需独立物理源副本冷构建。源码/测试/合同闭包需完整本地验收；独立角色核真实源、参考和学习证据。

## 追加有界投入

新tranche从2026-11-13T14:42:00.588931UTC至17:42:00.588931UTC，准备/失败/审阅/等待/交付均计入；原08:10起算和16:10旧截止另列，成本与曝光累计，不作时间算术重置。源执行最多600秒，参考最多2小时且17:12硬停，新CLI最多128次，旧24次累计后最多152次；并发2、90秒/次、0修复/重投。guard16意见及QA为首个有界证据点。

配额或guard再次失败时停止此分层试验后段并交付真实容量结果；NO_UPDATE或retention拒绝按真实KR收口。更改门槛、重标、增加下一采样方案、共享源修复或生产/交易权限均需各自独立依据。
