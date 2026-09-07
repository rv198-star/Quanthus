# 个股阶段历史学习：方案与工单准备收尾

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Scope | 仅本次文档/规划/只读硬件核查的收尾；不接受标签、训练、性能或交易效果 |
| Parent | `../work-items/stock-stage-historical-learning.md` |
| Starting revision | `9c805903fcd0b6a0deaf445f0c5a5900fdc8b445` |
| Governing design | `../design/stock-stage-historical-learning.md`（仍draft，训练gate仍blocked） |

## 交付

已形成历史规则学习方案、CPU/Win11/RX6800/云设备选择方案，以及算法/量化/短线业务三视角评审。评审明确为单一助手的分角色审查，不是真人或独立Agent签字；12项风险有修订落点和后续执行验收项。

GitHub实际创建并逐项回读：A #238、B #239、C #240、D #241、E #242、F #243、G #244、H #245，父工单#246。九项均open；仅A的规划状态为ready，其余blocked。未改旧Facts、Catalog、P6等工单。

每项工单有inputs、deliverables、depends_on、acceptance、blocked_on和evidence字段。当前evidence为空，不能将本次文档收尾当成A-H已经完成。分区2012-2021/2022-2024/2025与P0相同，所有执行、数据结果、密封数据、日更及租机授权仍false。

## 核查证据

OCI在2026-09-07T16:08:50Z的只读资源快照为4核ARM Neoverse-N1、23GiB总内存/约16GiB available、文件系统146GiB/约53GiB可用；DRM设备查为virtio-pci虚拟显示，不是训练GPU。现有工程环境没有numpy/sklearn。本次未安装训练依赖、未跑容量或模型基准。

用户Win11设备只确认本轮申报的RX6800及1TB SSD；CPU、系统RAM、实际空闲空间仍未知。官方GPU后端资料见compute方案，不以能编译SDK替代本机训练支持。建议首轮CPU，不立即租GPU。

## 已执行检查

- 文档与新计划契约专项：48 passed。
- `make check`：1617 passed / 7既有环境skip，耗时220.67秒；skip为PostgreSQL测试DSN未配置或生产专家产物未挂载。
- 确定性Catalog：748条目/10既有gap，digest仍为`sha256:d253b6baf4a2c53aaad8eeb52709804071bcce2c7431cbedf1383a7f2e8aa037`。
- 规划校验：九个稳定ID、文档路径、依赖顺序、禁止权限提升、冻结分区一致性通过；负例覆盖权限打开、分区改动、循环依赖、空验收、提前关闭和训练任务错误ready。
- `git diff --check`：通过。

这些检查不证明某状态准确率或完整历史训练速度。没有更改生产src、标签Registry、DWD、数据分区policy、日更调度或服务镜像；不需要生产部署。下一步是A的执行契约准备，B的基准及以后的实施均须按各自前提获得授权。
