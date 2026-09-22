# ODS/DWD 长期优化及第一批技术评审

| Field | Value |
| --- | --- |
| 日期 / 基线 | 2026-09-22 / 4985a6d56a2c7f668cfe727f6401627913cac02b |
| 评审范围 | 长期方案 #424；进程生命周期第一批 #425 / META-OPS-PROCESS-001 |
| Governing design | LLD 01 的 Daily process ownership and safe retry |
| 评审方式 | 同一助手分阶段进行设计、反向审查与测试验收；无独立人类或独立模型审批 |
| 结论 | PASS_SCOPED_IMPLEMENTATION；NO_GO_PRODUCTION |
| Delivery gate | blocked（正式 Linux、节点部署及运行验收未完成） |

## 1. 方案评审的实质性修改

1. 部署回执、历史日期恢复、邮件终态、代码合并不再合并为一个成功状态。
2. 禁止用 PR 的关闭关键字提前关闭运维总任务或历史缺口。
3. 将一分钟原始流共享/五分钟聚合作为独立契约问题：ADR 0003/0004 要求正式 5m 直接采集，
   当前 Eastmoney fallback 有聚合实现。此为既有一致性缺口，不因本次优化获得新授权。
4. 代码实现可以先行；Linux 回归、NexusAgent 连接、逐节点部署为独立放行条件。
5. 不建设新的调度平台、不修改支付设置、不恢复 OCI 远程执行入口、不删除生产数据。

## 2. 第一批代码审查

审查路径：

- `src/quanthus/pipelines/process_control.py`：只针对调用方以独立 session 启动的组；
  TERM/KILL 两段有界等待，唯有确认组不存在才返回成功。
- `ops/bin/run-cn01-ods-daily`：lane、catalog、DWD 使用统一回收语义；
  父进程先退出仍保留组回收；启动及异常路径清理其他已启动作业；截止后不启动新 lane。
- `ops/bin/run-ods-daily-with-retry`：75 为不能确认回收的保守终态，不启动第二轮。
- LLD、runbook、work-item index 与新增测试共同绑定到 #424/#425。

审查中的整改：

- 初版探测在 macOS 组退出期间得到 EPERM 时立即报错。修订为“仍视为存在，限时继续等待”；
  EPERM 从未当作已消失，真实信号权限错误仍 fail-closed。
- 某组回收失败不跳过其余组的回收；清理异常仍关闭父进程日志句柄。
- 未启动 lane 明确记为 not_started，不把它伪装成运行失败。
- 无法确认回收时，run.json、通知上下文均记失败，不写 orchestrator READY。

没有放宽数据门禁，也没有修改 provider、schedule、Base、数据包或 CURRENT。
本批不承诺终止进程在宽限期内没有任何写入，而是保证“确认回收并返回之后”
不存在该受控组的继续写入。强制终止不等于 checkpoint 已刷盘，后者属于后续工作包 C。

## 3. 回归证据

环境：macOS arm64，Python 3.10.16，uv 隔离环境，项目 dev/p1/platform 加 pandas/requests。
未安装完整生产 ODS 依赖，不是 Linux/systemd 验收。

| 验证 | 结果 |
| --- | --- |
| 最初新增 8 个用例对旧主线 | 8 failed，2.72 秒；真实 shell/Python/tee，无行情和生产数据访问 |
| 第一批新增 15 个用例 + 既有编排 7 个 | 22 passed |
| 日链路及文档扩大专项 | 186 passed / 3 failed；2 个 GNU/BSD sed 差异，1 个既有 P3-STRAT-003-B-R1 planning 缺口 |
| 全仓 make check | 2,703 passed / 90 failed / 7 skipped；失败测试 ID 集合与先前基线完全相同 |
| 静态检查 | 本批 Python ruff check、bash -n、git diff --check 通过 |

新增用例覆盖：正常 TERM、忽略 TERM 后 KILL、父进程先退出、poll 终态回收、
catalog timeout、截止后零启动、启动失败关闭日志、半启动组回收、回收超时、
权限探测不能误判成功、拒绝操作调用方进程组、一个清理失败不跳过其他组、
命令清理失败返回75、wrapper不重试75、失败证据与READY隔离。

全量检查没有绿色通过，不能因“失败集合相同”宣称仓库验收完成。
planning 缺口由既有 #412 / PR #420 路线处理，本批不替其改写设计或关闭问题。
GitHub main check annotation 仍为账单/额度导致 runner 未启动，不是执行后的测试失败。

## 4. 尚未解除的上线阻断

- CN01 未列在当前 NexusAgent 节点列表；Hermes 管理连接离线。不能由此断言其数据服务离线。
- 正式 Linux/Systemd cgroup 终止测试未执行；外部 collector 不得 daemonize 或 setsid 脱离所有权组。
- 控制器 SIGKILL 及主动逃离进程组的后代依赖外层 cgroup；需验证安装后的 KillMode、
  SendSIGKILL、停止超时与无残留写入，不只查看 unit 模板。
- 僵尸进程长期未被宿主回收会保守阻止重试。应修复宿主回收或确认真实进程状态，
  不通过忽略组存在性消除错误。
- 部署后才可核验修复 revision、unit 与下一次 timer，并启动 10 个交易日观察窗口。
- #311 历史日期、#423 部署回执、#310/#166 及其余长期包仍未关闭。
- OCI 角色存在权威文档与现行同步回执冲突：AGENTS/index 已退役，旧 HLD/ADR 0007
  仍记载镜像。已请求用户确认未来正式镜像范围；确认前不改部署拓扑或现有同步。
  此问题不阻塞第一批本地工程实现，但阻塞长期方案的最终镜像验收口径。

## 5. 后续放行步骤

保持 PR 未合并、#424/#425 开放；取得生产兼容 Linux 证据及可用管理通道后，
重新评审该批 diff 和验收结果，再按单节点灰度、回执、回滚、第二节点的顺序上线。
新发现的源语义差异单独治理，不能附带修改正式 5m 定义。

技术机制参考：
[Python subprocess 文档](https://docs.python.org/3.10/library/subprocess.html)、
[Python os.killpg 文档](https://docs.python.org/3.10/library/os.html#os.killpg)、
[systemd 官方 kill 配置源码](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml)。
这些说明接口机制，不替代本仓测试或现场生产证据。
