# 仓库维护循环合同 v1.1.1

实施与持久回执：[维护工单 #632](https://github.com/rv198-star/Quanthus/issues/632)。配置：[`maintenance-loop.v1.json`](../../config/maintenance-loop.v1.json)。

## 归属与范围

工程维护走正常修复线。blueprint_progress、exploration_governance 与 TPlan/job 保留原写入权威。数据采集、共享质量与准入由 P10 负责；业务研究走所属业务线。cn02-dev 摘要由登记采集器产生，main、已接受候选与实际执行版本分别记录。

GitHub 持有源码和研发工单；开发环境持有工作现场与本地验证；实际执行器和既有账本持有执行/验收事实。维护 inbox/outbox 保存传输身份、去重和回传，云端据原证据判断进度、根因和效果。

盘点覆盖 GitHub 及配置登记的必需环境。环境摘要保存采集时间、checkout、分支、匿名工作区身份、未提交工作数量、worktree 数、依赖摘要与采集范围。原工作区只读，编码任务使用独立 worktree，原始日志和凭据留在原存储。

## 正式入口与身份

Python 3.9+ 标准库、Git、已有 gh 认证。WFF 的验证使用 Python 3.12。配置登记三个真实开发环境：WFF oci-dev（OCI）、Quanthus cn02-dev（CN02）、AIWork oci-dev（OCI）。Mac 上的源码副本退出登记，接收器直接在各项目实际开发服务器运行。私有部署清单固定源码提交、登记工作区、持久 state-dir、解释器和 sources_file，维护源码通过 GitHub 分发。

`scripts/maintenance_receiver.py` 校验固定提交与维护代码配置后调用 `maintenance_loop.py poll`。环境内 poll/claim 进程锁保护同一 SQLite inbox/outbox。原工作区只读；repair 仍使用独立 worktree 和原有 Codex CLI。merge、业务验收及 TPlan/Coordinator/P10 的事实归属沿用项目现有规则。

每个实际执行轮次有持久 `receiver_run_id`；正式认领时记录 `received_at`、随机 `attempt_id`、实际序号与开始时间，完成时记录完成时间和运行身份。探针声明 `execution_model=in_process_probe`，后续消费者声明 `queued_rule_consumer_fixture`。它们使用接收器账本作为原生执行系统，external task 为不适用；repair 引用真实 Codex thread。重复投递沿用同一业务 attempt，发布重试另有 publication attempt。

同一 request_id 绑定同一 canonical request digest。原生回执写入后保持不变。旧版 inbox、已发布回执及其摘要保留；退出登记的 Mac 账本归档保存，旧批次仍可从原 GitHub 回执追溯；旧批次缺失的时间/事件导出 `legacy_not_recorded`。新请求使用 1.1.1。

## 环境来源

`snapshot()` 导出工作区 checkout、分支、匿名身份、修改数量、worktree 引用和依赖摘要，并读取私有 sources_file 中登记的原生任务与验证来源。请求 payload 无权选择本地路径。

- `github_work`：实时开放 Issue/PR 摘要，属于选定工作；与正在执行的 attempt 分开呈现。
- `native_tasks`：从既有 TPlan mission 或 AgentDock task JSON 读取 ID、状态、原生引用、来源摘要。已查询且无活跃任务显示 `status=ok, active_count=0`。
- `executor`：实际 Python 接收器/消费者能力；登记的开发服务器查询既有 Codex 版本与认证状态；`repair_environment` 指向该实际开发环境。其他只承担采集职责的角色才按配置声明编码执行器不适用。
- `verification`：`maintenance_verify.py` 实际执行相关测试，保存退出码、测试数、命令、时间、日志 SHA256、测试代码闭包与维护指纹；采集时重新核对来源与日志摘要。
- `maintenance_executions`：原维护账本的 receiver 身份和活跃 attempt，不把开放 Issue 当成运行中的进程。

每类来源分别导出 `ok / failed / not_configured / not_applicable`，缺口进入 `missing_sources`。原始任务正文、凭据和日志保留在原存储。原生引用与其脱敏摘要从正式回执可读。私有登记文件示例：

```json
{"task_sources":[{"id":"native-work","kind":"tplan","path":"/private/mission.json"}],"verification_sources":[{"id":"focused-tests","path":"/private/verification/verification.json"}]}
```

AgentDock 来源的 kind 为 `agentdock`。路径由部署维护者登记。新增消费者、加载器、采集器、验证器、wrapper、规则入口和合同均纳入 operational_fingerprint；验证报告另外覆盖测试文件。

## Web 请求与恢复握手

向本仓维护工单追加独立评论，正文只含以下 marker 与一个 JSON fence。同批使用全新 batch/challenge；同请求重投保持 JSON 内容一致。支持 snapshot、acceptance_probe、reconcile、repair、effect。作者必须位于配置 allowlist。

````text
<!-- maintenance.request.v1 -->
```json
{"schema":"maintenance.request.v1","contract_version":"1.1.1","repository":"rv198-star/Quanthus","environment_id":"cn02-dev","request_id":"BATCH-local","batch_id":"NEW-BATCH","challenge":"NEW-CHALLENGE","kind":"acceptance_probe","payload":{}}
```
````

对每个必需环境写入两条相同探针评论，随后写一条独立查询。查询可以预先排队；接收器会等原结果进入 deferred outbox 后执行：

````text
<!-- maintenance.request.v1 -->
```json
{"schema":"maintenance.request.v1","contract_version":"1.1.1","repository":"rv198-star/Quanthus","environment_id":"cn02-dev","request_id":"BATCH-local-reconcile","batch_id":"NEW-BATCH","challenge":"NEW-CHALLENGE","kind":"reconcile","payload":{"target_request_id":"BATCH-local"}}
```
````

后续精确查询可增加 `ledger_handle`，其值来自回执。查询沿同一私有账本导出原 request/slot/attempt、时间、result digest、outbox 状态和事件链。需要更新的查询结果时用新的 query request_id，原探针身份保持不变。

典型四轮（接收器每 60 秒轮询，验收期限 300 秒）：

1. 正式接收探针与两次投递，持久认领父 attempt，排队 `parent:fixture`。
2. 后续运行正式 claim 子任务，由消费者自行加载激活规则；持久保存子结果和父回执，将父结果留在原 outbox，追加真实 `publication_deferred`。
3. 正式 reconcile 查询原账本，追加 reconciliation 并发布查询回执；该回执导出待恢复状态、原结果摘要和业务 attempt。
4. 后续运行读原 outbox，记录 recovery/publication attempt，发布原回执并从 GitHub GET 读回。整个过程沿用原业务 attempt，不重跑消费者。

已执行而回传中断时保留原结果；丢失网络确认时先匹配已有回执评论。新 receipt 仍是每请求一份 immutable 原生回执（`<!-- maintenance.receipt.v1 -->`）；其后单独发布 `<!-- maintenance.delivery.v1 -->`，绑定 receipt_digest 与最终地址，导出恢复链和消费者账本。补充证据也 GET 读回；它的地址通过后续 reconcile 可查。这样避免地址与自身摘要的循环依赖。

事件有真实时间、run ID、前一事件摘要与自身摘要；events 表只追加，关键身份/结果与事件在同一事务提交。每次实际发布均有独立 publication_attempt_id。没有历史事件的旧批次保留历史缺口。

## 后续规则消费与效果

正式规则入口是 `config/maintenance-consumer-rule.v1.json`，激活版本 1.1.1，权威内容为本合同。正式消费者入口为 `scripts/maintenance_consumer.py:consume`，通过 inbox 的 `rule_consumer_fixture` 队列进入，公网投递不得伪造此内部 kind 或保留的 `:fixture` 身份。

父探针只排队候选输入；后续 receiver run 的消费者自己调用 `load_rule()` 加载实际入口、版本、authority digest。错误摘要产生 `rule_digest_mismatch` 拒绝，正确摘要产生 `activated_rule_matched` 接受。两例分别保存输入摘要、期望、实际行为与事件引用。消费者保存 rule_loaded、behavior_evaluated、rule_consumed；实际使用次数由持久 rule_consumed 事件查询得出。重投与恢复沿用子 attempt，使用次数不增加。

父回执从持久子结果生成受控效果：基线、适用条件、消费身份、使用次数、行为证据和 keep/wait。重开 SQLite 后核对消费结果，补充证据导出子账本。此 bootstrap 的验证范围是维护传输和受控版本规则。每日真实改进的效果需要其实际目标消费者的后续任务证据；Agent 负责收益、误阻、成本及 keep/revise/revert/wait/stop 判断。

## 八项验收与确认信号

`maintenance_audit.py` 根据来源、身份、引用、事件链及实际结果逐项验证；Agent 复核语义和授权。每个必需环境分别核对：

| 项 | 必需证据 |
|---|---|
| 1 上下文 | 当前合同/配置、实际 main、接收器提交、维护指纹对应 |
| 2 环境 | 请求后的新鲜快照，实际 native work、角色执行器、来源绑定的验证；无隐藏缺口 |
| 3 执行 | 首次 received_at、真实 run/attempt/model、完成时间与同一执行槽 |
| 4 重投 | 两条相同请求 GET 读回，同一 request digest 和唯一业务 attempt |
| 5 恢复 | result_persisted → defer → 原账本 reconcile → later recovery → 发布/读回，原摘要和 attempt 稳定 |
| 6 消费 | 后续正式 claim、独立规则加载、坏拒绝好接受、持久消费事件 |
| 7 效果 | 基线/适用机会、真实消费引用、从事件重算的次数和处置，重开读取一致 |
| 8 读回 | 原生回执、补充事件证据、分仓报告及汇总再次从 GitHub GET 读取一致 |

摘要算法：移除相应 `receipt_digest / evidence_digest / event_digest` 字段，JSON 按键排序、UTF-8、无多余空白，计算 SHA256。查询回执自身与其 target receipt digest 分开核对。

项目侧先完成三个真实开发环境验收并持久发布三仓报告、跨仓汇总与读回确认；Web 再以新 batch/challenge 独立复查。三环境、三仓八项均通过且汇总读回后，输出 `LOOP3_READY_FOR_SCHEDULE`，附实际 batch、3/3、3/3 与汇总链接。每日任务仍由 Owner 创建。

## 修复与每日运行

repair 的 payload 给已有开放维护 Issue 的 issue_number 和精确 current-main base_sha，只路由本仓登记的实际开发环境 `cn02-dev`。候选代码/检查/原生日志交项目审阅；已发出的原生回执保持不变，awaiting_executor/deferred_budget 处置后经审阅使用新请求继续，中断编码先检查原生日志和保留 worktree。

每日先对账旧任务、恢复 outbox、复查后续效果，再按价值选新改进。每仓最多一项新编码任务，预算见配置；无适用机会记录 wait。用同一 improvement_id 串联来源、Issue、attempt、验证、PR、合入、目标规则、后续消费和效果决定。维护自身心跳排除出变化扫描；未完成来源沿原游标重试。

保存新 run_id、来源游标、环境状态、阶段变化、效果和恢复项，追加汇总并 GET 读回。确认信号：`LOOP3_DAILY_OK / LOOP3_DAILY_NOOP / LOOP3_DAILY_PARTIAL / LOOP3_DAILY_BLOCKED`。每日 OK/NOOP 需要三仓完成本轮盘点；首次 READY 依上面的完整八项。接口、权限、加载语义改变后对受影响环境重新验收。
