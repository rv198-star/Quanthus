# R2：既有动量区间是否值得进一步回放

| Field | Value |
| --- | --- |
| Design status | approved within Owner-activated #613 bounded research |
| Delivery gate | accepted for the bounded resource decision after author self-audit and independent reconstruction; [evidence and value brief](../work-items/research-r2-value-brief.md) |
| Parent | [Exploration governance](exploration-governance.md) |
| Work item | P3-RESEARCH-REUSE-001 / #613; bus #608 |
| Request | `config/research/research-r2/request.v1.json` |
| Implementation | `src/quanthus/hf_dwd_research.py`, `src/quanthus/hf_dwd_research_sql.py` |
| Predecessor | [R1 accepted contract](research-r1-hf-dwd-v2.md) and [value brief](../work-items/research-r1-value-brief.md) |

本批要改变的决定是：R1 既有 20 日原始价格动量区间 [2%,12%]，是否值得投入下一笔完整
资金回放。只比较这一个固定区间，不拟合模型或搜索参数。基于已消费的 2020–2021 开发窗，
用途为 latest-sealed 回顾描述；2019 只读原日历，2022 以后行情保持原边界。

## 对照与目标

先保留 R1 的证券/上市年龄、21 个正有限收盘价、20 个收益、完整前20日成交额、1亿元
流动性、0.5 波动率和当日绝对收益 4% 条件，仅移除动量区间。有限动量落在区间内为候选，
区间外为互斥对照。两组同样经过已验收的下一开放日身份及开盘价格/状态守卫。

目标为下一开放日同一根未复权日K的 `close/open-1`。缺失/非正/非有限价格及非有限比值
排除并分别记账；来源行保持原样。该目标用于信号研究资源路由；A股 T+1 条件下，它是
盘中价格响应标签，后继完整资金回放另行评价费用、持仓、卖出与策略收益。

每个决策日、交易所、组内证券等权，算候选均值减互斥对照均值；两交易所均有两组样本
才形成配对日，两所差值等权后再对日期等权。这样同日市场涨跌和交易所样本量的差异不会
直接变成组间总体均值差。仍保留区间内外样本及分布差异作为这一描述用途的适用范围。

主要指标为配对日增量均值。辅助报告正响应比例和收盘较开盘跌超1%的频率，不用辅助指标
挑选新门槛。两组在实际配对日内各要求 >=5,000 机会、每年 >=500、>=100 listing、最大
单 listing <=5%、前10 <=20%；每年配对日期 >=200。缺失配对的日期和原因单独交付。

## 事前分支

按年份分层，在配对日上做20交易日循环移动块 bootstrap，固定 seed=20261112、2,000次，
保持各年日期数，以 nearest-rank 分位数报告95%区间。此段只量化该已消费窗口的描述不确定性。

- 样本不足：inconclusive，停放本次比较。
- 增量均值 >=5bp、两年各自 >0、区间下界 >0：作为后续带费用完整回放的候选。
- 区间上界 <5bp：本次最小增量路由门槛 not_met，停放固定区间的这一用途。
- 其他情况：inconclusive，停放；新问题或实质新证据出现后再评估。

5bp 是是否值得另投一笔回放成本的事前研究门槛；不把它解释为净收益门槛。
缺失输入不修补，不移动截止、改变阈值或追加区间来救结果。所有旧曝光和成本继续保留。

## 执行与验收

复用 R1 的56成员、18项环境锁、Guardian、封包和 HF 回读；不把接入更多 Worker 作为
本批前置。CN02 2线程、DuckDB3GiB、RSS6GiB、scratch2GiB，计算180秒，全部 Job 30分钟；
正式计算最多一次。必要独立复算一次，在隔离输入副本上用 pandas/numpy 重建连接与特征，
逐日期/交易所核对整数及指标（绝对误差 <=1e-10），分歧停止采用。

冻结请求及代码后依次进行合成语义/负例、自审、完整本地检查，再执行正式计算和独立核对。
结果沿用现有私有临时研究库，以 `runs/RESEARCH-R2-MOMENTUM-001/.../A1` 明确区分，
检查全文件回读与网络重试复用。原 R1 请求、结果和接受记录保持原版本。

#613 首笔主动工作上限1小时，机器检查/运行另记；原总线缺失的完整工时、Token和账单
保留 unknown_not_zero。若必要接缝超出本段，交付准确差集和后继成本/价值，不扩大平台。
交付应先讲清实际增量、选择、项目位置、投入评价和下一笔，再附技术证据。
