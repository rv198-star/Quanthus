# R9：执行器故障已复现、CN02工程检查通过，真实参考待接续

2026-11-14恢复002已锁定12例，并启动24个双模型CLI槽位。执行通道未返回任何有效意见：23空事件流、1未完成事件流，独立QA全部排除。因此本轮为0正、0负、12个技术UNKNOWN，fit/update仍为0。原A4的0正8负及此前56次启动保留；累计80个CLI启动，未重试。

本次可复用资产是带源/选择/协议绑定的校准入口、完整分母账本、排除式QA和同一Mission恢复合同。原始执行代码5e2f1387与后继修复候选dabb9c3c分开：修复为空事件停队列、定点PIPE限额、退出码/信号与超时清理，不重放失败试验。69项聚焦回归及范围lint/type通过；随后按Owner指定切换SSH，CN02/Python3.10.12完整make check通过：9319通过、35跳过，规划检查通过。旧Mac中断记录保留。

SSH隔离回环对照已复现根因：移除全局文件限制时12.143秒完整输出；加256KiB限制时0.501秒空退出，SQLite WAL触及限额。研究CLI启动80与本次2个离线fixture启动分开计账，真实模型请求新增0。当前已完成工程故障定位与完整检查，双类语义锚仍待建立。下一笔最小动作是另行分配最多2次、最多20分钟的通道验证，先取得完整双读者回执，再决定更大校准投入。该提案尚未激活，R9、#662/#665、学习及消费者验收均保持开放。

[本轮验收与精确断点](../acceptance/research-r9-calibration-recovery-20261114.md) · [PR #669](https://github.com/rv198-star/Quanthus/pull/669) · [后继有限通道提案](../../config/planning/next-delivery-request.v1.json)

历史源与canonical参考的交付继续按[原验收](../acceptance/research-r9-reference-closeout-20261113.md)及[原分段合同](../design/research-r9-reference-calibration-v1.md)复用。当前12例属于已曝光开发校准面板，后继学习仍须独立冻结样本、支持度、隔离和消费者用途。
