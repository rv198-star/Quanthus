# R9：真实局部学习与 S02 版本选择完成

本轮完成了真实局部拟合、原条件更新分支、独立评价与冷重建，最终决定 **retain-v0**。更新状态为 **NO_UPDATE**。

源分母 1,048，843 例合格，固定 64 例；训练复用原有 24 例（5 正19负，两个2020月份），新 guard/update/evaluation 均为互斥的2021公司。所有参考原票、未知、排除、失败及旧窗口保留。

| 同一明确评价集合 | TP | TN | FP | FN |
|---|---:|---:|---:|---:|
| r0 | 7 | 12 | 4 | 1 |
| v0 | 3 | 16 | 0 | 5 |
| v1 | 未生成 | 未生成 | 未生成 | 未生成 |

评价明确参考 24/24，未知 0。理由：loss trigger absent。

R0为4个误报、1个漏报；v0为0个误报、5个漏报。误报减少4个，同时多漏掉4个正例；本轮研究基线保留v0，业务R0不替换。更新参考仅2例明确FALSE、6例UNKNOWN：4份有效意见、2份到点超时、10个未运行槽位全部保留。

更新诊断损失 0.22516247，原触发值 0.69314718。v0及条件候选、retention选择和全64预测在评价标签解封前封存；独立冷重建从126源成员重新构造特征/R0并拟合，checkpoint/预测/决定精确匹配。旧v0读回后64例预测也逐值匹配。

实际用途变化：S02研究消费者获得了可保存、恢复、比较的真实拟合版本，以及同案例误报/漏报和翻转报告。原March与R8用途保留；报告每行probability=null、专家状态pending/ABSTAIN。

投入判断：累计 288 次研究/通道CLI，另有2次离线fixture；本阶段新增 70 次，上限80。服务端可见token用量 {'input_tokens': 4142578, 'cached_input_tokens': 2158720, 'output_tokens': 384237}；旧root/审阅与精确账单未计量。已取得可复用参考资产与真实拟合/复现交付，投入较高，后续付费扩样停放，按具体未满足消费者问题再决策。

完整学习MVP仍保留实际更新及其他模块的原验收；本轮条件更新按原门保留NO_UPDATE。

源码生产者：9fcdf96a37f74106f656d0014e9e608e78688853；完整CN02/Python3.10检查9,333通过、35跳过、63警告（pytest952.45秒），log SHA13c7e2001476edc1f20f0265d2a832c1c157d06103cce0d18cad3be88ded70fe。其后仅文档/账本变动，执行源码/研究合同/测试闭包保持字节一致，并重跑受影响治理检查。

私有HF固定revision `084437145701d9d6fd4e9b049deb6cb723924f53`，前缀 `runs/RESEARCH-R9-CALIBRATION-001/RESEARCH-R9-CALIBRATION-001-T1/SSH-REFERENCE-ASSETS-S02-001`；1047文件、33,713,027字节，全部size/SHA回读。manifest SHA `d54cafeb0a8365e85e1d7a9a8ff6fa85c5071610d119c4bf8949fe51172b35c7`。

旧首个64例研究单独停止：train为1正19负4未知，未达到原正类4；112次调用及16次未用额度保留，未滚存；其update参考/fit/compare/cold均未运行。后继完整复用其合格evaluation24作已曝光训练资产，原角色和票不改，永久退出未来评价/guard/update。

[PR #670](https://github.com/rv198-star/Quanthus/pull/670) · [S02消费者报告（私有发布证据内）](https://huggingface.co/datasets/rv192/quanthus-research-r1/tree/084437145701d9d6fd4e9b049deb6cb723924f53/runs/RESEARCH-R9-CALIBRATION-001/RESEARCH-R9-CALIBRATION-001-T1/SSH-REFERENCE-ASSETS-S02-001)

主交付已普通合入main `21642245fed63481081d5763103d6f9b2ca30963`（PR #670，02:19:05Z）；#662按completed关闭，原A3/A4固定试验#665按not_planned停止。#540/#543/#544/#608保持开放并更新当前简报；#544实际fit验收项已具证据，实际更新KR继续开放。

科学运行与HF主体发布在总截止02:18Z前完成；GitHub收口超过该交付目标，未延长参考窗口或追加实验。行政尾项和独立验收、元数据/分支清理成本仍计入原Mission。

预测封存后释放的5条非决定性评价注记已单独保存于私有HF `a260edfdb1fe379c9ce02f4facdd13e5c40e1ead`，文件SHA `5de2a2235b0ac9d63c362bb76c59ef321bcfa4b8c95e0f4e40fb2388807eb167`，1766字节全回读；原1047文件证据主体继续固定08443714，原意见/标签/QA不改。

独立最终验收：训练scaler与200步梯度参数差值0；64预测与旧v0读回差值0；封存早于评价解封0.528324秒；126成员/64例/2560准入冷构造一致。原生mypy保留9个未改动依赖文件的43项既有错误；隔离follow-imports=skip另有1项导入Any返回提示，未宣称类型检查通过。

R9 Milestone23已关闭；实时工单列表open=[]（里程碑计数接口短时缓存仍显示旧2，关闭依据为实际工单列表与两个PATCH闭合回执）。父级学习MVP保持开放。
