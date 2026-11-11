# #628 A 原生输入资格与薄适配自审

本记录是批准首小时工作的验收证据。结论：**A调查与原型可交付，完整资金消费资格BLOCKED，停在B前**。
治理设计为[分段资金回放计划](../design/research-capital-replay-plan-v1.md)；面向负责人的效果见[价值简报](../work-items/research-capital-qualification-a-value-brief.md)。

## 固定输入及执行身份

- 请求`config/research/research-capital-qual/request.v1.json`；闭包`closure.v1.json`：126成员/192,098,985B。
- HF来源`rv192/quanthus-base25-sealed@1923c874b43d47e69190a6dc9546e6fd8f3d1ca3`；manifest SHA256 `8cd966be87fce02168c54e77c23e05f491c4afb204c4cbace42a2024eee1cdef`。
- 内容digest `1d0e4bd5fbf40bf2876883faf82b98fd81e95a93a82b9d23d248759a38b3f692`；资格请求digest `sha256:475bdda78b01f8286fe510fcd23f69b4c59627587b1f9ec3149134644d03f61a`。
- 市场2020-01-02..2021-12-31、2019仅日历；latest-sealed回顾用途，禁止2022+市场与策略/PnL计算。
- 真正源census Git `2d85c14954702689914d30b31ed63de66d2b0608`；census digest `sha256:cb58c2a52e5e7844eed2e78f6b1ef1181d89d698863400612568ce9cf2ca7c7b`。
- 关系补核代码Git `e7913ef23a45016fce1493467e5340409886b8c9`；最终原型与完整检查代码Git `eae99b25172d4958d44eb7aff6b5e34ff984e742`。补核请求`relations.v1.json`先提交/分发，再执行一次；结果digest `sha256:42c793a469edcf93735c0b6a138a070002c3ea89958e22f547da6181412c4913`。
- CN02 `agentdock@quanthus-cn02`；隔离根`/srv/agentdock/quanthus-ops/research-capital-qual-a-20261112`；Python3.10.12，18项研究依赖按`config/research/research-r1/runtime.v2.lock`逐项核对。Owner本任务SSH例外已授予；代码均经GitHub固定提交分发。

## 实际资格与消费合同

| 坐标 | 实际观察 | 当前消费处置 |
| --- | --- | --- |
| 身份/单位 | 4,717 listing/security；CNY、lot100、common_equity；12表必需列/声明键重复/缺业务或知识时间均为0 | 这些结构坐标合格，后继继续按有效区间绑定身份 |
| 交易关系 | bar2,014,392；state2,031,358；bar缺state、state缺listing/calendar、开市日缺时刻均0 | 复用源日历，拒绝编造交易时刻 |
| 状态缺口 | TRADABLE_OBSERVATION_MISSING18,405条中1,457条有正open/close；观察状态2,780条缺正上下界；state无bar16,966条 | 按键关闭相应新入场；P10区分合法无界规则与证据缺口；缺失不触发强制卖出 |
| 行动单位 | action12,649，IMPLEMENTED11,530；11,485条单行通过原生金额/比例检查 | 每股金额原样，比例按显式分子/分母；单位检查与事件归并分别负责 |
| 行动隔离 | 24业务日期未知、19必需条款未满足、2特殊term；共45条IMPLEMENTED被hold | 持仓遇不合格行动停止受影响资金臂；不把未知填0 |
| 事件身份 | 3,074个证券/ex_date多记录键共8,808条；2,662组数值相同、412组现金/股数不同 | P10 #556交付版本化组合/替代/副本规则；不求和或按源投票 |
| 时钟 | 所有被核源表知识时间晚于业务日期；合格单位行动11,485条也均为晚源时钟 | 原始header/term知识时间都保留；模拟时钟须另订回顾合同，历史cutoff检查保持拒绝 |
| 质量例外 | 12条；其中6条行动条款SOURCE_INFORMATION_UNAVAILABLE，6条其他身份例外 | 保留原逐案resolution/disposition；不通过本线清零未解四案 |

上述多记录键来自通过单行检查的子集；它们可能是多来源、修订或组合表示，当前发现是**资本消费者缺用途归并合同**。局部状态、特殊条款和完整经济事件资格各自留有准确范围，不扩展为全库不可用。

## 原型与审查范围

`native_capital_adapter.py`只实现单条行动单位/日期/时钟载体及买卖方向的P2 MarketSession映射；真正FrameSource、候选排名、双时钟消费编排和资本实验属于B/C。

自审检查了：现金不猜除10；bonus/transfer显式比例；表头及每条term时钟分别保留，P2 known_at取共同可用时间；未知/特殊term、外来条款、错日期、重复term拒绝；买卖方向不合并；缺状态观测拒绝；保留涨跌停类型；复用既有纯action_terms函数，未继承P6用途资格。

CN02最终专项18通过，含真实DuckDB合成完整census与时区边界。关系补核独立写SQL/键关系，重用已校验物理源；它是资格补核，资本独立冷建留在C。补核由180秒墙钟、240秒CPU及4GiB虚拟内存上限约束，实际13.61秒进程墙钟、退出0。

## 失败与成本留存

首次请求的工程Git和真实源census Git分别记录。遗漏PYTHONPATH、把quality_exception的detected_at_year误当保护行情年份的启动错误发生在真实源读取前；修正后首次且唯一完整census成功。原始失败记录与后续准确绑定同时留存。质量治理年份2026被允许，业务行仍按affected_effective_at的2020–2021筛选。

一次远端FETCH_HEAD未在worktree内解析，checkout失败后改用本地自动取得的完整Git SHA，随后核对成功；没有由失败触发重复源census。

工程自审的投入、完整检查、发布/留存实际身份追加在下方交付记录。A Mission收口与完整#628的后继拨款分别管理。

## 工程检查口径

最终代码在CN02专项18通过（1.09秒）；修改范围Ruff通过。两份新增生产模块严格类型检查（follow-imports=silent）通过。
首次展开依赖的类型检查另报告272条未改动依赖/缺stub诊断；本线新增的6条类型收窄诊断已修正，完整原始日志保留。
旧代码上的完整检查中途停止，最终代码重新完整检查，分别记账；没有重做源census或关系补核。
受影响blueprint/exploration元数据、精确PR base的追加历史检查均已通过。最终代码完整`make check`通过：8,955 passed / 35 skipped / 62 warnings，pytest790.02秒、整条make794.88秒；可选sklearn/torch等跳过在原始日志逐项保留。HF/CN02固定回读另有交付收据。

## 固定发布与交付

最终代码完整检查8,955通过/35跳过/62warnings（pytest790.02秒）。资格包一次原子发布至私有HF `rv192/quanthus-research-r1@011f8e929bbbca7076cb9377612441e93bf20e06`，目录`runs/RESEARCH-CAPITAL-QUAL-001/RESEARCH-CAPITAL-QUAL-001-T1/A1`。26文件/2,037,141B从固定提交逐文件回读一致；manifest SHA256 `dae7fd7d003c9886044fa769a7a67717078f69ffabe5ae006ac75d2707462d07`。

CN02长期根`/srv/agentdock/quanthus-ops/research-capital-qual-a-20261112/retained/qualification-package`逐文件hash核对，收据为上一级`publish-readback.json`。资格包使用qualification-only schema，保留源census、关系、检查和失败记录。源码交付[PR #630](https://github.com/rv198-star/Quanthus/pull/630)，叠加于#629；最终代码2288文件闭包与文档/计划跟进逐字节一致，受影响计划检查重跑。

A段资格调查与原型交付，完整资本资格BLOCKED，#628及R4保持OPEN，B/C/D未运行。留存责任人为CN02研究维护者；资格包及未解P10键长期保留，临时源/缓存2026-12-12起复核清理，审阅或消费者恢复需要时延长。
