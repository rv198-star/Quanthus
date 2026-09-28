# 9/24 1m 难票覆盖能力检查与替代样本规范

关联：[#440](https://github.com/rv198-star/Quanthus/issues/440)、
[#456](https://github.com/rv198-star/Quanthus/issues/456)、草稿
[PR #467](https://github.com/rv198-star/Quanthus/pull/467)。
性质：Owner授权的隔离诊断/来源能力否证，不是独立技术审批、部署或DWD验收。

## 本次结论

**NO_GO_CAPABILITY（未通过覆盖验证），NO_GO_DATA / NO_GO_PRODUCTION。**
现有两源不能据当前证据保证补齐；不再因此启动全市场重拉。这个结论不是声称接口
全部不可达，也不是断言未来永远不会修订。替代渠道先提供难票样本，未验证不能承诺
全市场成功。每日/lane/maturity timers继续disabled，t-1 heartbeat继续PAUSED。

24只旧硬缺口已按同一关闭strict账本重新核验SHA、日期、参照和实际数据：腾讯均有
未返回分钟；中转22只现行标签/竞价解释下日量不符，000755/300501则without-auction
日量相等但首5m冲突。不能把两票称为日量缺失，也没有证据能把缺行一律认定为零成交。
这里只聚焦无合格整源的24只，**不代表既有2672双源冲突已经解决**。

9/29 **03:50:59–03:51:15 CST**，代码
`bad3115d37b03f54922fada4e2dd370923cf9250`在CN01空闲隔离checkout执行一次窄复测。
先离线重验24，再按单worker有界取数。前三票仍不合格，第四票中转202触发停止；
剩余20票未请求，不把它们标为新失败或假装24票完整网络验收。

| 票 | 新腾讯9/24实际缺口 | 新中转 | 新raw/current规则结论 |
| --- | --- | --- | --- |
| 000404 | 13:42 | HTTP200，raw SHA与旧相同 | exact和小误差候选均拒绝；旧日量差-12884股仍未改善 |
| 000726 | 13:24 | HTTP200，raw SHA与旧相同 | exact和小误差候选均拒绝；旧日量差-7100股仍未改善 |
| 000755 | 13:12、13:29 | HTTP200，raw SHA与旧相同 | exact和小误差候选均拒绝；竞价/首桶联合条件仍未解决 |
| 002917 | 11:21 | HTTP202待供数 | 当次没有新中转候选；腾讯仍缺行，停止后续请求 |

腾讯总raw SHA变化包含其他日期内容，不自动解释为9/24质量修订；上述原始缺口仍在。
当前小误差候选要求amount非空；本次exact OHLCV路径也没有通过，因此此处的否证不
依赖于amount豁免是否最终批准。没有拼接、补差股数、造零量行或改第一桶规则。

回执位于CN01：
`/root/work/astock/data/quarantine/minute-capability/2026-09-24-hard-gaps-20260929T0355`。
目录名不是实际启动时刻，以job.started_at为准。summary为
`capability_evaluated_not_published`，processed4/24，all_hard_gaps_qualified=false，
stop=`relay_acquisition_stop_source_pending`。

| 内容 | SHA256 |
| --- | --- |
| summary.json | `af8126665d10eda89a1fd07f923d97fb1096f6ea17e808ca2b18a5cf564cfce2` |
| offline-records.jsonl | `b3522d797c9aaf5ae5da65f50254378e66c4aaccac2a8cce204968d4d7c974e2` |
| probe-records.jsonl | `1a8b5bc109005712a8426b353d80e814444450b0a48f620684bda176ec18c87b` |

源码通过GitHub fetch到隔离checkout，不修改生产。实测代码的本机/Linux聚焦测试各
116 passed；focused Ruff通过。make check在目标执行前仍被本机Xcode许可阻断。
后续补充的完整性错误停止反向测试不改变上述实测数据或其版本，不冒充重新取数。

## 给替代来源的实际样本规范（尚未发送给第三方）

日期：**2026-09-24，Asia/Shanghai，不复权、历史1分钟、完整原始会话**。
先试000404（缺分钟/量差）、000755（竞价歧义）、301203（多分钟缺口），通过后扩到
下面24只；即使这24只通过，也不是全市场/多交易日稳定性已验收。

```text
000404.SZ 000726.SZ 000755.SZ 002917.SZ 002959.SZ 003028.SZ
300187.SZ 300190.SZ 300501.SZ 300899.SZ 300908.SZ 300994.SZ
301009.SZ 301049.SZ 301097.SZ 301100.SZ 301103.SZ 301173.SZ
301203.SZ 301221.SZ 301288.SZ 301296.SZ 301491.SZ 301502.SZ
```

需提供实际响应/SDK原始返回、查询参数、时间和source身份；SDK返回不是wire bytes时
明确标注，不伪造网络原始身份。字段至少code/time/open/high/low/close/volume/amount，
说明量单位（股/手）、额单位、时间是开始还是结束标签，以及09:30竞价、午后首价、
14:57–15:00的处理方式。禁用客户端填补缺行；源显式零成交行与未知缺行分开。
客户代码不得分摊成交量、推算金额、补OHLC或将未知分钟当零成交。

样本先保存不可变响应及SHA，再按明确的来源合同核日期、真实分钟格、非空字段、
实际量价和首桶解释。小误差按当前显式候选比较，不舍入修改实际值；源间整票冲突
仍需解释/审查，不因换名字或优先级自动消失。只有所有难票完整覆盖后才谈下一次全量
冷拉和DWD验收；单日期通过后还需晚间/T+1、非整百股、休市、失败续跑、通知回归。

## 文档能力核对（不是实际供数批准）

| 候选 | 官方支持依据 | 仍缺的实际证据/准入 |
| --- | --- | --- |
| Tushare官方直连 | [stk_mins](https://tushare.pro/document/2?doc_id=370)明确历史1min、股/元字段，分钟权限单独开通；示例含实际零量14:58/59行 | 官方分钟权限与官方token，难票样本、成本及覆盖验收；现有中转卡号不能当官方token |
| 通达信官方tqs/TdxAiData | [跨平台后台接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html)列明Windows/macOS/Linux、不依赖客户端、历史分钟与数据服务Key；[K线函数](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html)有复权/填补开关 | 不是此前mootdx公共7709接口；需要可运行SDK、受保护认证、当前积分/权限和真实难票返回；禁用fill_data，不能凭产品名保证精度 |
| RQData | [get_price](https://www.ricequant.com/doc/rqdata/python/generic-api)支持历史1m、adjust_type=none；文档警告默认skip_suspended=false会用停牌前数据补齐 | 分钟权限/凭据、真实难票样本与报价；取样关闭停牌填补，需说明volume/total_turnover与会话口径 |

本次只读核对官方文档，未开新账户、付费购买、联系供应商或调用新计费源。现有受保护
relay凭据仅经stdin用于上述四票请求，未进入源码/日志/issue；没有读取其他账户Key。
现阶段缺的是**可供实际验收的替代样本或可运行的正式分钟权限**，不是继续猜一个API。
上游a-stock-data#57本次读取仍只有我们自己的补充，没有维护者答复。
