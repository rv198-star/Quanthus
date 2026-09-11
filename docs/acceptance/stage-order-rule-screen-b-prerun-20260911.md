# P3-STAGE-007-B：Prepare 运行前就绪记录

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-007-B / #289 |
| Governing design | [固定规则筛查](../design/stage-order-rule-screen.md) |
| Prior blocked checkpoint | [B source blocker](stage-order-rule-screen-b-checkpoint-20260911.md) |
| Current disposition | READY_FOR_PREPARE_RUN；不是B验收、不是C执行、不是父里程碑完成 |
| Prepare contract | `config/stage-learning/order-rule-prepare.v1.json` |
| Contract digest | `sha256:b27bbc1d3d54f9e410a8ca7b931af63f62690be9b911d538aeb0cda5666e9bd9` |
| Worker kind | `quanthus.stage-order-rule-prepare.v1` |
| New market reads at this checkpoint | 0 |
| New Stage-007 jobs submitted at this checkpoint | 0 |

## 1. 旧连接阻断已经排除

前一检查点记录的SSH问题没有被删除或改写。后续核验发现OCI侧生产MCP服务实际以
`ubuntu`运行，而此前训练私钥由`devspace`持有并通过ACL给`ubuntu`读取，OpenSSH因而
拒绝使用该身份。服务身份改为读取`ubuntu`独占的0600私钥后，RX6800网络节点恢复在线，
生产MCP也已重启。

恢复后通过`/run/quanthus-mcp-production/control.sock`的既有`stage_worker_status`受控操作
读取旧Stage-004 Job，而不是绕过MCP直接抓文件。实际回执确认：

- Job `stage_87d166eb3f4a4f7f858f9b4f3e238462`；
- revision `1878c625ad04b23793507d92bf0e3008052a5a85`；
- state `succeeded`、exit code 0；
- manifest `sha256:67a1159eba611ceb8af1f43c246f49acc101786e223730af6b61a9b0a8c237ac`；
- observation count 224346；reference 160，0=69、2=9、ambiguous=65、neither=17；
- selected parameters仍为null，原INCONCLUSIVE处置不变。

这只证明受控RX通道恢复、旧Job身份可读，不等于旧512经济身份已经完成重建，也没有
读取旧160逐例上下文。

## 2. Prepare 路由已经实现

新增prepare只承担B的来源与固定选例职责：

1. 从旧已接受artifact读取其真实`cohort_digest`和`source_manifest_digest`；
2. 用旧冻结2019 cohort算法和完全相同开发源重建512代码，必须精确匹配旧digest；
3. 重新构建旧source manifest并与旧summary绑定，证明不是只凑512个代码；
4. 在2019—2021身份元数据内，把旧512及Stage-006三证券对应的全部经济身份排除；
5. 从2020-12-31之前已确定的开发人口中形成最多512个新经济身份池；
6. 只读2019—2021开发数据，共享构建正式Facts，按既有固定哈希规则选择2021覆盖/挑战案例；
7. 对最终案例仅保留每例61会话T-only证据并做冷重建绑定。

Prepare不生成短线/基金意见，不评分007规则，不搜索参数，不读取2022—2024 arbitration，
不触碰2025，不发布LabelCard、primary stage、日更、策略或交易结果。

相关实现：

- `src/quanthus/stage_learning/order_rule_prepare.py`；
- `src/quanthus/stage_learning/source.py`中的开发身份投影；
- `src/quanthus/stage_learning/worker.py`中的固定Worker kind与结果摘要；
- `tests/unit/test_stage_order_rule_prepare.py`；
- `tests/unit/test_stage_training_worker.py`。

## 3. 单次执行与重复提交保护

Stage Worker对007 prepare新增独立的不可变预算标记。第一次提交把exact revision、kind、
config和input digest绑定到唯一Job；相同输入再次提交只返回原Job，不创建第二个Job。
不同revision再次提交会fail-closed；即使原Job在claim前被取消，也不会因此打开第二次实验。
这满足本轮“prepare最多1个Job”的实际幂等边界，而不是靠操作人员记忆避免重复。

整个007仍保留最多2个新Job的总预算；第二个只能在B真实来源隔离完成后另行绑定为
resolve+score，当前未开放。

## 4. 当前尚未完成的证据

本记录不是B关闭证据。以下值仍必须由真实prepare Job给出后才能关闭B：

- 旧512完整重建后的真实cohort digest确认；
- 旧代码与Stage-006证券的真实经济身份排除集合；
- 新池、真实源manifest、实际扫描/Fact计数；
- 固定案例数及各面板shortfall；
- T-only evidence digest与Worker终态回执。

当前生产MCP内部已经存在`stage_worker_status/result/deploy/submit`操作，但其旧
`stage_worker_submit`执行器仍硬编码qualification kind。因此本会话不能把“代码ready”
伪装成已经提交007 prepare。节点本地受控CLI能够接受新kind；若MCP提交入口未先升级，
提交需由具备RX操作权限的维护入口执行一次，且幂等标记禁止重复Job。

## 5. 工程检查边界

新增prepare、Worker kind、固定选择/评分组件和计划契约的最终聚焦回归为136 passed；
相关Python文件Ruff通过，`git diff --check`通过。全仓`make check`完成doctor、compile、
pytest及planning检查：2072 passed、7 skipped，pytest耗时250.52秒。7项跳过仍为两项
未挂载生产专家产物和五项未配置PostgreSQL测试DSN，不声称已执行。工程绿色只证明代码
和合同可执行，不证明任何市场语义、参考独立性或规则质量。
