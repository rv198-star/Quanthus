# 资金回放接缝核对与计划交付

日期：2026-11-12；工作项：P3-RESEARCH-REPLAY-001 / #627。
前驱：[R2价值简报](../work-items/research-r2-value-brief.md)。
交付：[完整后继计划](../design/research-capital-replay-plan-v1.md)，后继[#628](https://github.com/rv198-star/Quanthus/issues/628)。

## 结果与用途

批准的30分钟接缝段完成了代码、固定元数据和旧契约核对。P2成交/费用/资金账本可复用；
Base2.5.1原生数据到P2的身份、目录、双向状态、公司行动和时钟用途适配尚待交付。
推荐下一笔最多1小时输入资格与薄适配原型，在检查点决定后续预算；完整后继主动工作规划3–5小时。
本段的产出是准确的接缝、预登记计划和可独立交接工单；资本结果留给后继关口。

## 证据身份及观察

代码检查基线：`bfb16e25a101f855f941255d89d8cbc8f1b9944a`。

- `direct_dwd_provider.py::_direct_dwd_authority`读取旧`package_id/through_date`和必需correction，并校验benchmark/industry引用。
- 同文件`_ProjectionConnections.open`发现旧领域目录和replacement文件；`_materialize`明确拒绝买卖方向状态不相等。
- `base24_semantic_projection.py::_queries`的原生行动投影保留bonus=NULL、knowledge_from及用途未资格异常，符合source-only边界。
- `historical_frames.py::_snapshot`生成T日15:00模拟snapshot；`_validate`的行动契约同时要求完整现金/送转、known_at及来源字段。新回顾消费必须区分源时钟与模拟时钟。
- `baseline_research.py`还包含MA等其他条件及独立特征约定，不能直接代替R2筛选。
- `engine.py`复用冻结P2价格、佣金、税费、过户费、滑点、订单和资金分位量化；现实现买方过户费为0。

公开HF元信息下载自固定revision：
`rv192/quanthus-base25-sealed@1923c874b43d47e69190a6dc9546e6fd8f3d1ca3/DWD/manifest.json`。
实际17,229,591B，SHA256
`8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef`已校验。
manifest声明`kind=quanthus_dwd_base_bundle_v1`、`version=2.5.1`、`business_through=2026-10-31`，
使用`payload/<table>/...`，consumer_admission为NOT_GRANTED。原生action/term文件分别817/105，
元信息存在不建立它们在本次持仓回放的单位/有效日/知识时钟资格。

基于原R2闭包和2020/2021原生行动家族得到候选126成员/192,098,985B；factor交叉检查候补8,467,044B。
它是资格入口清单。最终读取request需经过后继A关口冻结；本次市场行读取为0。
源码各文件摘要及完整候选清单保存在本会话`work/capital-seam-20261112/evidence/`，
可由上述固定Git和HF manifest重新构建。

## 设计自审

预登记明确T决策、T+1尝试买入、T+1收盘退出信号和最早T+2卖出；排名只用T字段，
后续执行拒绝不触发基于未来状态重新挑选。费用、资金、单仓、对照、尾部估值和右删失均为显式规则。
候选和对照共享一个Job内前驱，独立重建不复用正式面板。

计划把身份/时钟/行动冲突、执行拒绝、携带卖单、末期持仓、超预算、独立账本分歧、HF暂态和半包路径逐项列出。
A未资格停止相应资本比较，缺失保留未知并按实际欠账交P10；没有开启新采集或Worker建设。

## 检查与成本

适用的blueprint/exploration专项59项通过。首次本地调用缺PYTHONPATH发生收集错误，已保留并修正；
Python3.12和pytest通过本地uv隔离运行。新work-item贡献字段首次登记缺失被验证器拒绝，补齐后preflight通过。

本段只修改文档和计划账本；源码、运行配置、测试、依赖与既有完整检查闭包保持相同。
全仓检查复用R2固定执行6dd072ac的8937 passed / 35 skipped；已有4条类型债务及可选依赖跳过保持原范围。
收口另查追加历史、生成视图、生命周期及变更后的计划记录。

旧R1/R2尝试、返工、审阅、曝光与缺失账单持续累计；本段30分钟为主动工作上限，完整人时/Token/账单未完整计量。
后继3–5小时是规划区间，A建议先拨最多1小时。当前已批准接缝段与后继拨款分别记录。
