# P3-STAGE-001-A 首组语义与试验边界冻结

| 字段 | 值 |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Work item | P3-STAGE-001-A / #238 |
| Governing design | `../design/stock-stage-historical-learning.md` |
| Machine contract | `../../config/stage-learning/experiment.v1.json` |
| Scope | 只冻结首组3/4语义、有限参数搜索、参考/分区/权限边界；不训练分类器、不访问未来目标、不打开仲裁/永久盲测 |

## 1. 冻结内容

第一组只研究 `3 试探突破` 与 `4 突破接受`，并把 `2 整理`、`5 上升推进`作为相邻上下文，不把十二状态独立优化后直接拼接。20/60 anchor 作为不同评价 profile，不作为优化器随意选择的参数。

`3` 的最低语义骨架是：同一事前冻结高点发生首次收盘上穿、episode 当期开始、anchor 只来自T以前、完整标签所需活动输入可用。`4` 必须引用同一 anchor 的既有 probe episode，不能与 `3` 在同一时点确认，并要求事前冻结的保持时长、signed distance 和活动证据。二者都不承诺未来上涨、盈利或后续结构一定成功。

现有 F1/F2/F3 可以复用，但完整3/4仍要求 F7 episode-conditioned activity；F6仅用于4的回测子型。允许先实现 price-only engineering predicate 做数据/过程验证，但不得把该子集冒充正式状态3/4。

## 2. 有限学习预算

R0 是固定专家规则基线。R1 每个 anchor profile 最多18个明确候选组合：probe `volume_ratio20` 仅试 1.0/1.25/1.5；acceptance hold 仅试1/2/3个交易时段；signed-distance margin仅试0/0.0025；acceptance窗口量比先固定1.0。数字是有限候选，不是市场规律。

R2 只有R1出现可归因表达不足时才启用，最多12个预登记配置，只允许正则化逻辑回归和一个受限浅树家族；不打开深度学习。所有扩大搜索空间的行为都视为新学习预算，不能在同一确认性结果上无限试错。

## 3. 参考与数据边界

可行性试标为120个 episode-context，其中80随机、40边界/稀有反例；只用于开发可辨识性和预算估计，不复用为确认性测试。reference_kind 显式区分 definite / coexistence / ambiguous_set / unjudgeable。

P0数据使用分区保持：2012-2021 development、2022-2024 arbitration、2025 permanent blind。RX6800训练节点经负责人明确选择保存了截至2026-07-31的完整 Base v2.1，因此2022-2025字节已经物理存在于训练节点；这不修改P0的数据使用期，但**该节点不再具备“保留期字节从未暴露给节点管理员”的物理盲测保证**。开发代码必须继续硬拒绝2022+读取，任何后续外样本资格还须单独说明该事实，必要时换用重新物理隔离的判卷环境。

## 4. B阶段授权边界

负责人在2026-09-08明确同意继续推进后，P3-STAGE-001-B只获准进行 CPU-first 环境初始化与容量 benchmark。真实DWD基准只允许读取2020-2021日线，输出只允许行数、耗时、RSS、scratch bytes、吞吐和环境身份；禁止输出市场收益、标签分数、未来结果或策略结果。

分类器训练、F7/F6业务实现、人工参考标注、2022-2025结果读取、每日调度、GPU云租用和策略消费仍未获授权。B的通过不等于标签训练开始。

## 5. 验收结论

A 的语义、允许参数空间、相邻维度、参考预算、P0使用分区与物理镜像claim ceiling均已有唯一机器契约。后续B可以开始容量资格；C必须等待B完成，且完整3/4实现仍须补齐最小活动事实契约。
