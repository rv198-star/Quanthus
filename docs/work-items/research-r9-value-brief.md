# R9：真实局部学习与 S02 版本选择完成

本轮完成了真实局部拟合、原条件更新分支、独立评价与冷重建，最终决定 **retain-v0**。更新状态为 **NO_UPDATE**。

源分母 1,048，843 例合格，固定 64 例；训练复用原有 24 例（5 正19负，两个2020月份），新 guard/update/evaluation 均为互斥的2021公司。所有参考原票、未知、排除、失败及旧窗口保留。


[本轮验收、成本与恢复](../acceptance/research-r9-local-learning-20261114.md) · [PR #670](https://github.com/rv198-star/Quanthus/pull/670)

原条件更新状态NO_UPDATE，消费者决定retain-v0。独立cold与旧v0恢复通过；累计288研究/通道CLI，原失败/窗口/成本保留。本轮R9有限交付收口，付费扩样停放；完整MVP继续按#540/#546原合同汇总。
