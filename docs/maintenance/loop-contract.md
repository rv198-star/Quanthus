# 仓库维护循环合同 v1.0.0

实施与持久回执：[维护工单 #632](https://github.com/rv198-star/Quanthus/issues/632)。配置：[`maintenance-loop.v1.json`](../../config/maintenance-loop.v1.json)。

## 归属与范围

工程维护走正常修复线。blueprint_progress、exploration_governance 与 TPlan/job 保留原写入权威。数据采集、共享质量与准入由 P10 负责；业务研究走所属业务线。cn02-dev 摘要由登记采集器产生，main、已接受候选与实际执行版本分别记录。

GitHub 持有源码和研发工单；开发环境持有工作现场与本地验证；实际执行器和既有账本持有执行/验收事实。维护 inbox/outbox 保存传输身份、去重和回传，云端据原证据判断进度、根因和效果。

盘点覆盖 GitHub 及配置登记的必需环境。环境摘要保存采集时间、checkout、分支、匿名工作区身份、未提交工作数量、worktree 数、依赖摘要与采集范围。原工作区只读，编码任务使用独立 worktree，原始日志和凭据留在原存储。

## 已实现入口

运行环境：Python 3.9+ 标准库、Git；GitHub 传输使用已有 gh 认证。WFF 项目验证沿用 Python 3.12。

```sh
python3 scripts/maintenance_loop.py export --environment-id local-dev --workspace /registered/project
python3 scripts/maintenance_loop.py claim --request request.json --state-dir /private/maintenance-state --workspace /registered/project
python3 scripts/maintenance_loop.py reconcile --state-dir /private/maintenance-state
python3 scripts/maintenance_loop.py effect --request effect-request.json --state-dir /private/maintenance-state --workspace /registered/project
python3 scripts/maintenance_loop.py poll --state-dir /private/maintenance-state --workspace /registered/project --environment-id local-dev
```

路径由私有部署清单提供；Git 配置保存环境 ID 与职责。每个环境使用独立持久 state-dir，通过进程锁和 SQLite 事务保护。`poll` 是单次消费，可挂在项目现有服务或用户级接收入口，每 60 秒检查一次。当前部署身份与在线证据见工单中的 deployment/receipt 记录。

`--executor /absolute/path/to/codex` 接入已有编码 CLI，使用隔离 worktree，保留原生 thread ID、日志摘要、候选分支与检查，结果交原审阅流程。中断的编码执行先检查原生日志与 worktree，再决定继续。合入和部署走项目原权限流程。

## Web 请求

向上方维护工单追加一条独立评论，格式如下。使用新批次和 challenge；同请求重投保持 JSON 内容一致；每个必需 environment_id 分别投递。

````text
<!-- maintenance.request.v1 -->
```json
{
  "schema": "maintenance.request.v1",
  "contract_version": "1.0.0",
  "repository": "rv198-star/Quanthus",
  "environment_id": "local-dev",
  "request_id": "BATCH-REPO-local",
  "batch_id": "NEW-BATCH",
  "challenge": "NEW-CHALLENGE",
  "kind": "acceptance_probe",
  "payload": {}
}
```
````

支持 `snapshot / acceptance_probe / repair / effect`。只接收配置内作者；Web 通过工具核对实际评论作者。`repair` 的 payload 提供已有开放维护工单的 issue_number，只路由到 local-dev 的编码执行器。

## 回执与首次验收

回执为同工单评论，标记 `<!-- maintenance.receipt.v1 -->`，JSON schema 为 `maintenance.receipt.v1`。按 repository、environment_id、request_id、batch_id、challenge 对应，核对 request_digest、receipt_digest、源码/维护指纹、环境采集时间和 result。

摘要算法：移除 receipt_digest 后，JSON 按键排序、UTF-8、无多余空白的 SHA-256。

探针覆盖事务去重、坏规则版本拒绝、正确版本接受、重开持久存储、规则版本消费与效果回传。探针首次将 outbox 留存，下次接收端运行再从持久存储发布。Web 按配置期限等待；超时查询原请求与接收端，并沿原身份恢复。

三仓所有必需环境均返回本批次 pass、汇总保存并读回后，输出 `LOOP3_READY_FOR_SCHEDULE`，附 batch、3/3 与实际汇总评论链接。部署或证据缺口逐项记录 pending/blocked 与恢复位置。

## 改进与效果

同一 improvement_id 串联来源、Issue、原生执行/attempt、验证、PR、合入、实际消费版本、后续适用任务、效果评价及下一决定。工程完成后，可用关联效果工单继续观察。

效果请求保留 rule_id、实际消费版本、consumer_run_id、基线、适用机会、原证据、keep/revise/revert/wait/stop 判断及理由。脚本核对身份与引用；Agent 判断收益、误阻、成本和处置。无适用机会记录 wait 和复查条件。

每日先对账旧任务和复查效果，再选择新改进。每仓每日最多一项新编码任务，时长上限见配置。维护协议随正常 PR 进化，相关接口/权限/加载语义变化后重新验收受影响部分。

## 每日确认

保存新 run_id、来源游标、环境新鲜度、阶段变化、效果决定和恢复项，追加汇总并读回。无变化也保留确认心跳。

- `LOOP3_DAILY_OK`：三仓本轮完成且有有效处置。
- `LOOP3_DAILY_NOOP`：三仓本轮完成，无新增处置。
- `LOOP3_DAILY_PARTIAL`：部分仓或汇总有缺口，已有项目证据可读。
- `LOOP3_DAILY_BLOCKED`：全部未完成或共享入口阻断。

未完成来源保留原游标，维护自身心跳排除出变化扫描，同根因发现续写原工单。部署、集成与用户首次验收在本工单中分别留痕。
