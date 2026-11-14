# R10 当前价值与决定

R10 受控负结果、反馈处置和统一入口已通过独立整体验收：**拒绝 v1，保留 B／研究 v0**。
原实际更新、三条资金路径、冷复现及 G01/G02/G03/G05 处置已接受。
统一入口独立使用复核耗时 **69.451 秒、手工 join 0 次**；原三例 108 秒验收作为历史保留。

[固定私有交付入口](https://huggingface.co/datasets/rv192/quanthus-research-r1/blob/f92055455600184cc788ceda0b7cbbe259d59003/runs/RESEARCH-R10-FINAL-DELIVERY-001/20261115/publication/DELIVERY.md)包含原 73 文件冻结包、原 ZIP 和独立审计回执，旧提交措辞按历史读取。
本机入口：`/Users/william/Documents/Codex/2026-11-14/jie-s/outputs/quanthus-r10-final-delivery/README.md`。
PR #687 已普通合入 `e81be2be19460c73bdb37207270e52ff4160be3f`。
[实际交付与工单关闭](../acceptance/research-r10-final-delivery-20261115.md)已记录；R10 总责任由审计线在本次真实交付终验后关闭。

本次新的决定是：停止用当前同标签 31 例另训窄幅头；保留 width-only 结构性停放；
对已拒绝候选终止无法改变版本选择的 13 个成交标签采购；把上下文参考边界转成
[唯一固定参考资格提案](../design/research-r10-reference-boundary-proposal-v1.md)。
它仅是最多 16 意见的非激活成对资格实验。本棒新增参考／训练／市场／资金运行均为 0。
详细的 [整体验收与处置](../acceptance/research-r10-overall-convergence-20261115.md)保留原科学结论、
全部失败／曝光／成本与时限偏差，已独立接受，最终交付待本次终验。

## 原单次受控运行的实际结果

R10已实际完成固定2021原生源资格、一次S02监督更新、B/B+v0/B+v1三条完整资金路径，以及独立冷构造。v1保留/识别/经济三门均拒绝，继续保留B和研究v0。新增可用能力是从状态分数到专家确认、分配、成交与账本的完整可追踪研究入口。

| 负责人要知道 | 实际结果 |
|---|---|
| 更新与恢复 | 直接复用原v0，实际100步update1、新fit0，原64预测恢复差0 |
| 质量取舍 | guard FP0→1；eval FP0→5、FN6→2，三UNKNOWN保留 |
| 资金结果 | 原B +0.855212%、B+v0 0%、B+v1 −3.335538%；终态全平仓，81条账本零不平衡 |
| 复现 | 154源成员物理复制、父200步/纠偏100步/三arm冷构造，37项一致，新参考0 |
| 使用 | 三类真实案例可追查改变、原门槛阻断、状态不可用；独立角色108秒完成定位，手工join0次 |
| 投入判断 | 完成实际更新与完整决策接缝有价值；误判增加且资金退化，停止采用本候选。下一笔先解释误判原因，再决定单一改进假说 |
| 当前资格 | 已知1664公司排除，剩余历史曝光UNKNOWN；受控工程回放已执行，正式独立有效性和采用待合格证据 |

[结果审计](../acceptance/research-r10-controlled-feedback-20261114.md) · [本轮批准合同](../design/research-r10-controlled-replay-v1.md) · [下一阶段细则](../design/research-r10-next-feedback-v1.md)。

新增参考64/64有效，health0、重试0。主fit进程1.89秒，完整三arm21.30秒，cold536.47秒。06:55UTC总时钟继续累计，失败、审阅、发布和收尾全计；主动/模型账单未知非零。旧R9 NO_UPDATE与288CLI+2离线fixture保留，本轮新增64次另列。最终Linux完整检查9363 passed/35 skipped，新增6模块mypy和改动Python Ruff通过；私有HF固定`9f62b74d7e8f814c245ab7e1dc40f3c084999ea0`，537文件/68,982,233B全回读。普通合入和工单收尾在最终closeout回执登记。
