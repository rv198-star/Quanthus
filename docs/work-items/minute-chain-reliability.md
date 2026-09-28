# 1m 采集链路可靠性重构

| 项目 | 状态 |
| --- | --- |
| 工作项 | META-OPS-MINUTE-005 |
| 上级 | [META-OPS-RELIABILITY-001 / #424](ods-dwd-reliability.md) |
| 基线 | main@be96a7cb3b2b7e41937aef7e5b19ab5c5fbee9f1，2026-09-28 |
| Design status | approved；仅分阶段工程路线，不批准未决来源语义或发布契约 |
| Delivery gate | open；工程、Linux、节点部署、数据验收及观察期分别留证 |
| 授权 | Owner 要求优化整个 1m 采集链路；已有付费中转、受保护凭据和追补授权延续 |
| GitHub 跟踪 | [整链路 #456](https://github.com/rv198-star/Quanthus/issues/456)，首片 [#455](https://github.com/rv198-star/Quanthus/issues/455)，B1 [#458](https://github.com/rv198-star/Quanthus/issues/458)，B2 [#460](https://github.com/rv198-star/Quanthus/issues/460) |
| 当前生产放行 | blocked；本工作项不是 9/24 READY/DWD 完成证据 |

最新候选（2026-09-29）：`META-OPS-MINUTE-010` 显式小误差配置和同 raw 的审计/离线
重验入口已实现，尚未接入生产 collector/物化/发布。默认 exact 路径保持，生产仍
`be96a7cb`、采集与每小时跟进仍暂停。[草稿 PR #467](https://github.com/rv198-star/Quanthus/pull/467)
初版代码 `c48bda97` 已完成 Linux 分钟/文档扩大检查 `358 passed / 1 failed`；
后续配置类型/原因标签收紧后本机 `361 passed / 1 root-role skipped / 1 failed`。
唯一剩余失败是既有
`P3-STRAT-003-B-R1` 文档追踪缺 governing LLD 行；不是新规则回归。
focused Ruff/diff检查通过，`make check`仍在目标执行前被Xcode许可阻断。
本轮两份全量既有 raw 离线重验均完成；结果与仍未完成的DWD见下节。
作者测试不当独立审批或DWD成功。

### 9/29 小误差候选的全市场隔离回执

固定代码 `c48bda97da372fea48f17aa6ce767412a9a323f4`，同一份最新冷拉
`minute-cold-test/2026-09-24-20260928T2331` 的 verified market/native5m参照，
重放既有 `tencent-full-20260924-20260928T1145` 与
`paid-1m/2026-09-24-full-20260928T0025` 不可变 raw。
这不是新一轮冷采、不是两次独立采集，也没有网络请求或读取Key。

两Job输出均在CN01 `/root/work/astock/data/quarantine/minute-ledger/` 下：

| Job / 同一4992封存票 | 无冲突整票候选 | 双合格源冲突 | 无合格整源 |
| --- | ---: | ---: | ---: |
| `2026-09-24-strict-20260929T0250` / 默认exact | 2296（含7停牌） | 2672 | 24 |
| `2026-09-24-bounded-20260929T0250` / amount也要求非空 | 2817（含7停牌） | 0 | 2175 |

不能把第二行的0冲突解释为两源一致或小误差完全消除了冲突：腾讯4870实有完整交易票
因原始接口不提供amount而拒绝，另115票因推断分钟拒绝；它并未参与双合格源比较。
中转由2767增加至2810交易票候选，净增43；仍有2059日量/适用首5m联合条件拒绝、
113关键分钟不完整、2竞价/标签歧义、1其他资格拒绝，共2175。
amount豁免仍需Owner明确决定，不能通过替换价格×量推算或降级来源绕开。
113票中99票有4个、14票有26个不完整关键分钟，不能归为可容忍数值毛刺。

bounded耗时305.119416秒、strict322.773441秒，processed均4992。
两份records实际SHA与sealed summary一致；summary均
`offline_evaluation_completed_not_published`，publication/recovery授权均false。

| Job | Summary SHA256 | Records SHA256 |
| --- | --- | --- |
| bounded | `591058f8d5b4d00df58d97a9786acd202bb0b84e2d828eff9abce41f80131ec9` | `c84fed5582b34c1739a24d2bbd3ec77c6a893f9d01db92686ce54b3ddb113e3d` |
| strict | `3eed3b2cadc503a3bf6ccc61139c6c77f6cd40cf392761d961f5098707f469e4` | `2dd3f24422276f73900f5bb76b44fd152e1237a82f29c7518e2307ebe8d96780` |

旧回执中的腾讯`MISSING_MINUTE_AMOUNT`还伴随泛化的`MINUTE_GRID_OR_EVIDENCE_MISSING`标签，
并不表示4870票缺时间行；后续仅修正描述标签，保留旧回执不覆盖，资格及候选数量不改变。
测试环境先误用了无依赖overlay的venv（pyarrow/requests缺失），明确是运行环境错误；
实际回归/两Job使用只读`PYTHONPATH=src:/root/work/Quanthus/.deps`，
Python3.10.12/pandas2.3.3/pyarrow23.0.1。没有修改共享依赖。

本轮结论仍 **NO_GO_DATA / NO_GO_PRODUCTION**：没有1m READY、DWD或OCI验收，
新规则尚未接入collector/物化/DWD，#466质量错误误计网络熔断也尚未修复。
默认生产规则及停用timer/暂停heartbeat不变，不能从离线候选数自动发布或恢复。

## 1. 复盘：从补单票转为修完整链路

2026-09-29 Owner进一步确认“按1–3做”：先拆清24只硬缺口，再只验证现有渠道能否
覆盖这些难票；不具备能力就明确停止当前路线、研究替代样本，而不是反复全市场拉取。
24只均有腾讯实际未返回分钟，22只中转在现行标签/竞价解释下日量不符；000755/
300501的without-auction日量相等但首5m冲突，不能把两票说成日量缺失或取消首桶条件。
本轮能力脚本由LLD 01同节治理：关闭的strict账本及actual SHA钉住范围，默认离线，
显式单次最多24只窄复测，保留旧/新raw/hash及全部拒绝原因；质量拒绝不遮住剩余难票，
网络/pending/限流则有界停止。同一新raw同时诊断exact OHLCV与bounded含amount口径，
不因少量数值容忍而补造行/空值。只有24难票覆盖成立才进入下一次全量冷拉候选；失败
则给出可供替代渠道实际试取的24票/日期/字段样本规范。文档支持历史1m不算实际准入，
新来源权限/费用/凭据仍需明确，生产、timer和paused heartbeat均不变。

本次03:50–03:51 CST单次窄复测在第四票202时结束：前三票中转raw SHA不变、腾讯仍
缺原分钟；四票exact/bounded均无候选，余20票未请求。**现有路线没有通过能力验证**，
不启动全市场重拉/发布；实际回执与替代24票样本规范见
[来源能力检查](../reviews/minute-hard-gap-source-capability-20260929.md)。
替代文档支持不等于已供数，须拿到真实难票样本/权限才可进入新源准入。没有新采购、
费用承诺、生产部署或恢复调度，#440/#456仍未完成。

基线、事故事实和证据更新由 [#440](https://github.com/rv198-star/Quanthus/issues/440)
及 [#436](https://github.com/rv198-star/Quanthus/issues/436) 保持，不在本方案重复事故账本。
本次反复阻塞不是一个原因：供应商初步整手量、晚到修订、未返回的分钟、不同 OHLC
口径，和本地准入/物化缺陷必须分开归因。接口可达、HTTP 200、checkpoint done、
合格候选、四 lane READY、DWD 和镜像完成是不同事实。

已有反证说明“继续等、反复全扫、增加来源后投票”不足以闭环：

- 腾讯曾在代码不变时把历史分钟量从整手改为精确股数；这证明该次响应变化来自上游，
  不证明所有日期最终都会成熟，也不证明 T+1 必然够用。
- 全市场中转 HTTP 200 仍包含量差、空值和口径歧义；两票日总量相等不证明分钟分布相等。
- 当前研究 normalizer 会补推断零量分钟；正式准入漏拦截由 #453 / PR #454 单独修复。
- `_merge_write` 同目录保留旧重复键，可能让新合格证据对应旧输出。#455 的合成复现成立，
  但尚未证明已有生产数据受污染；正式 lane 的新 attempt 隔离降低了该路径的现实暴露。
- 过早同步告警、失败后待补日期漏选和休市误报已有独立修复；不把它们再次归为供数故障。

目标是：在有界成本内取得可追溯的合格整票，未合格时留下精确缺口和下一行动，
不因一只股票遮住全市场状态，也不因旧日期反复请求挤占新日期采集。
工程目标不承诺创造供应商未提供的精确分钟。

## 2. 保持的业务和发布边界

2026-09-29 Owner明确接受浮点尾差、小成交量误差和一分钱价格差异，但拒绝缺数据、空值、
错日期，并要求开始处理。新增 **META-OPS-MINUTE-010** 显式候选规则由
LLD 01 的“Owner-bounded minute quality candidate”及
`config/minute-quality/bounded-v1.json`治理；下面旧“精确/任何冲突阻断”文字仍描述
未变的默认生产口径，不覆盖新隔离候选，也不是新口径已经发布的证据。
量差同时100股/0.001%按前一轮建议进入候选；amount是否豁免未单独明确，先保守要求
源实际非空，不伪造也不隐瞒。成交额单位/真实性与完整DWD验收仍分别待验证。

当前来源仲裁仍由上级方案的“2026-09-25 逐票分钟来源恢复增量”和
`minute_source_selection.py` 治理。日期、代码、实际来源、240 格点、封存日线精确股数及
收盘价保持；缺失分钟不能由日总量反推零量。合格整源冲突仍阻断，优先级不能消除冲突。
独立 5m 是口径诊断证据，不因本方案自动成为全字段多数票裁判。

2026-09-28 19:41 CST 的最新 Owner 决策：CN01/Hermes 每日采集 timers 已停用，
分项与 maturity timers 继续 disabled。只读跟踪此前已启动的当晚 attempt，不强杀、
不重启、不途中换代码；不能手动启动新的每日采集。先修复并验收 1m DWD，再由 Owner
明确确认是否恢复，即使工程测试通过或 9/24 追补完成也不得自动 enable。OCI 同步及
weekly-corrections 没有因此改动。下文早于该决策的正常时刻/保留 timer 记载是历史证据。

禁止拼分钟、分配差股数、推算 amount、把未知状态归为自然成熟等待，或仅凭旧 receipt
放行。分钟 amount 的质量限制继续由 #436 显示。表示尾差、竞价标签和午后前收沿用
的调查不自动授权修改真实分价、OHLC 或首桶规则。

ADR 0007 的 CN01/Hermes 独立构建、OCI 镜像 CN01 的职责不变；四条同契约 READY
仍是 DWD 前置。将基础 DWD 与分钟增量解耦属于另外的架构决策，需要消费者影响评审，
不能作为本次追补的临时旁路。现有恢复 timers 保持 disabled，不启用无期限调度。

2026-09-28 21:5x CST，Owner进一步要求：本轮仍无法收敛时先停止每小时跟进，再按下列
P0/P1/P2展开工程优化。本轮仍无9/24 DWD，因此现有 `t-1` 跟进心跳已设为 **PAUSED**，
没有替代心跳、另建定时器或自动唤醒。停止的是重复跟进，不关闭 #440/#456 或丢弃 raw。
每日/lane/maturity暂停继续有效；恢复采集及恢复定时跟进都须Owner明确同意。

## 3. 选定的改造路径

### Owner确认的收敛次序与交付边界

先完成安全闭环，再讨论来源合同与数据恢复；不以每小时重试代替以下交付。
既有草稿分片是工程输入，不因本次优先级确认自动成为批准生产版本。

| 顺序 | 调整与覆盖入口 | 本阶段必须给出的验收证据 |
| --- | --- | --- |
| P0 准入闭合 | 正式采集、seed复制、付费raw重放、已验证文件复用均重新按当前契约检查实际分钟；研究推断结果不能冒充源实有完整格点 | 明示推断/缺少源完整性证据拒绝；错误旧标签不能通过；显式源零量可通过；逐入口反向测试与代码映射。#454仍是待审分片，不单凭checkpoint done或旧文件存在放行 |
| P0 物化闭合 | 整票来源选定后只写新attempt，最终文件必须重新准入并等于所选整票语义hash；不采用分钟keep-last或只验新响应 | 旧行、跨日期、ghost停牌文件、碰撞、读回损坏和写盘失败均可复现拒绝，旧证据保留。#457及#454组合、Linux文件系统和实际运行角色分别验收 |
| P1 来源合同 | 每源显式记录时间标签、竞价、午后首价、缺行/零量、数值表示和amount；先证明同义才比较 | 用已有不可变raw建立差异分母和原始反例；区分观察到的微小差/量分布差/未归因差。无统一平移、补量、自动舍入或降级来源；未决合同需明确决议，不承诺凭代码补造供应商未给的分钟 |
| P1 统一资格 | 审计、生产和离线重放共用同raw bytes、同适配/规则版本、同日线/状态/适用5m参照 | #459/#461/#463相同输入的三入口差分回归；完整参照和actual SHA交接；旧receipt不替代重验。方法一致不证明市场真值或解除双合格整源冲突 |
| P1 分类恢复 | 网络错误有界重试；精度不足低频复测；语义冲突离线处理；稳定HTTP200不反复取数 | source/date/security/reason残余清单和完整恢复身份；假时钟/请求计数验证429、5xx、pending、质量和硬完整性故障的不同停止语义；网络、适配和物化耗时独立记录 |
| P2 上线回归 | 当日晚间、非整百股量、休市、失败后续跑、旧日期不饥饿、通知和OCI等待窗口进入固定矩阵 | 本地、Linux、实际锁定运行角色、同契约四lane、日期包/manifest/哈希/通知/OCI分别留证；批准版本受控双节点验收后才开始观察。测试或9/24完成不自动恢复采集 |

P0正式准入和最终文件一致性是下一项上线前置，来源合同调查可以在隔离只读环境并行。
跨Job复用必须先补齐语义身份与失效合同，不能让诊断账本摘要或部分key代替它。每阶段
分别报告工程、部署、历史数据和运行状态；来源真值/产品依赖调整不由合成绿灯裁决。

### 工作包依赖

```text
受保护凭据 → 有界取数 → 不可变 raw + 采集回执
                         ↓ 一次 Job 内共享封存日线/状态/适用参照
                   单源适配 + 统一整票准入
                         ↓
                整票仲裁 + 分原因残余集合
                         ↓
            新 attempt 物化 + 读回一致性核验
                         ↓
      四 lane 同契约 READY → DWD → 通知 / OCI 深验
```

| 包 | 优先级 / 依赖 | 可执行交付 | 验收与停止条件 |
| --- | --- | --- | --- |
| A 准入与物化闭环 | P0，先做 | #453/PR #454 拒绝正式推断分钟；META-OPS-MINUTE-006 / #455 新 attempt 整票写入和落盘复验 | 来源显式零量可过，缺行不能过；新证据不对应旧行；拒绝旧目录不改旧 receipt；物化失败立即停止网络工作 |
| B raw 与资格分离 | P1，A | 审计、生产和重放使用同一版本适配器/准入；HTTP 状态不当资格；封存参照只读加载一次；raw 身份与候选身份分别保留 | 同一 raw/适配版本/参照在三入口给出相同资格；旧合格 receipt 漂移重新验失败；SHA 错配不复用；记录网络/适配/写入耗时 |
| C 来源语义和差异分类 | P1，离线可先做 | 逐来源说明时间标签、单位、集合竞价、午间首价、零量/缺行、收盘及 amount；离线区分表示差、可证明口径差、实质差、未归因差 | 使用已有 raw，保留原始 SHA 和版本；每条归一化规则有原始反例、参照和反向测试；未归因仍阻断，不靠哈希相等宣称源正确 |
| D 缺口队列与成本 | P1，B/C 明确类型 | source/date/security/reason 级残余集合；202 延后不遮住未请求票；429/5xx 有界退避；稳定不合格 raw 不反复重取 | 已验证 200 不重复请求；恢复身份涵盖完整语义闭包；新 raw 或策略修订可重验；假时钟证明有界、限流熔断及新日任务不饥饿 |
| E 日期调度与发布闭环 | P1，D | 采集/准入/ODS/DWD/通知/镜像分别观测；新旧日期独立；明确自然日/交易日 SLA 后版本化；预算和锁兼容预检 | 休市无假 DWD；旧失败不抹去 pending；逾期一次通知、不重标准时；active job 不换代码；OCI 只认冻结日期包和哈希 |
| F 验证与灰度 | P0/P1，贯穿 | 固定原始样本离线回放、合成故障矩阵、Linux 文件/进程验证、单节点隔离 shadow、双节点受控部署、十交易日观察 | 逐片通过才上线；真实来源可用性另验；完整分母含失败/迟到/冲突；节点×日期缺口、请求复用、成本与原因可审计 |

跨 Job 复用必须核对 node/date/universe/frequency/provider、raw SHA、参照数据与有效视图
digest、policy/config/schema/adapter/code 版本。身份不完整就是 miss/拒绝，不能为提速
缩小 key。routine 复用不能冒充独立 cold acquisition 或正式复现。共享采集/中心缓存、
自动更换付费源、取消冲突门禁以及 DWD 拆包目前均未获本方案授权。

C 包先完成一次既有全量 raw 拆解和必要的代表性窄验证，再给出来源能力/口径决议。
若仍缺精确整票或无法证明同义，不以继续轮询当作解决方案：明确列出未决合同、受影响票
和下一项需要批准的来源准入或产品依赖调整。可以评审哪些观测确为同义、哪些应保留为
不同来源产品；不得先把中转降级以绕过已有冲突。新精确来源的可用性、费用与使用授权
和技术参数分别确认。本次工程不承诺任意供应商一定能补齐 9/24。

## 4. 首片：META-OPS-MINUTE-006 / #455

治理实现和行为细节见 LLD 01 的“Qualified 1m materialization consistency”节；
实现为 `src/quanthus/providers/minute_tdx.py`，回归为
`tests/unit/test_minute_materialization.py`。

选定 **拒绝复用目录**，不在本片定义历史分区替换。理由是现行 formal lane 已使用新
attempt，保持该边界可以直接消除旧行混合，而 `keep=last` 会暗中变成分钟级拼接。
合格整票进入临时 Parquet，读回执行同一精确准入并核候选语义 digest，再以无覆盖方式
暴露目标文件。停牌只记录合格空集，不能留下旧交易文件。

此片不修正供应商时间口径，不改变来源优先级，不为旧 receipt 提供新资格，也不宣告
历史跨日期 `_merge_write` 行为已全部解决。非该 opt-in qualified 1m 入口保留原行为，
未来扩展必须先冻结它自己的替换/保留合同。

首片验收覆盖：同日期旧新、跨日期、部分/空 frame、旧目录和 receipt 保留、重复证券、
精确量价和格点回读、日总量合格但分钟语义改变、序列化读回损坏、目标文件碰撞、
物化失败停止下一请求、停牌 ghost 文件，以及旧非合格路径无隐式契约改变。

### B1：META-OPS-MINUTE-007 / #458

9/28 作者入口复盘的纯合成反例：日收盘 11.00、分钟末 close 11.01，适配器 0.011
容差报告候选合格，但生产精确 `qualify_candidate` 拒绝。这是资格声明分叉，不是已证明
9/24 来源缺口的原因。审计还在保存 `response.content` 后使用 `response.json()` 判定，
证据 bytes 与判定输入未由同一路径闭合。

有界修复由 LLD 01 的“Paid 1m shared final qualification”治理：保留现有竞价/时间解释，
仅把 adapter 结果明确为 provisional；审计和生产 raw 重放共用候选构造，单次解码已捕获
bytes；审计 `qualify_raw` 与生产整票仲裁调用同一个既有精确门禁，成功后记录 OHLCV
语义 hash。不能把适配器提前拒绝变成整票仲裁之前的硬错误去遮住合格另一源。
HTTP 200 不合格 raw 仍保留和可验证复用，不因质量不合格重取；表示尾差也不舍入放行。
amount 限制继续可见。

当前本地八分钟套件 `134 passed`；新增十六条合成用例覆盖两种标签、
真实一分价差、表示尾差仍拒绝、捕获/SDK JSON 分离、无效 root、原始/hash 保留、一次适配，
以及拒绝的中转候选不能遮住真正合格的另一整票源。focused Ruff 与 diff whitespace 通过；
文档/调度 `57 passed / 1 failed` 仍为已复现的既有研究线追踪缺口，`make check` 仍被本机
Xcode 未接受许可阻断；不自动接受许可、不称全仓绿灯。
它只是 B1，不称 B 完成：生产缺少 first-5m 参照、历史 receipt 现行离线重验及完整恢复
身份/成本队列仍需后续接口闭合。同输入一致不等于不同参照的一致，也不证明供应商真值。
代码 [PR #459](https://github.com/rv198-star/Quanthus/pull/459) 是堆叠在 PR #457 上的草稿，
不是 main 发布。GitHub code commit `ff38880adb48ca07296caea2ac845ed7e4ab804a`
在 CN01 的另一隔离 checkout 上八分钟套件 `134 passed`，未改共享 collector 环境、
未访问真实来源或读取 Key。与 PR #454 `857afb387ec36abd6ed4a09cbaac4147c254d4dd`
组成的未提交树 `8ed2c7ad4d6bc617e1fb3805ee15170b60e0e90a` 上九分钟/seed 套件
`138 passed`；组合树保留在隔离 checkout，不是合并或部署。有效 Python/依赖与本节后述
A 的 Linux 环境相同，`PYTHONPATH` 首项改为 B1 隔离 checkout 的 `src`，显式禁用插件。
GitHub CI 仍在任何 step 前遭账户 payment/spending limit 阻断，已核实 annotation，
不称代码失败或重复告警。Linux smoke、作者反向审查、运行环境资格和生产放行分别留证；
当前仅 scoped engineering 通过，参考输入闭合及生产放行仍未完成，不改今晚编排。

### B2：META-OPS-MINUTE-008 / #460

有界接口复盘确认：审计传封存首 5m，而生产 replay 始终传 None；审计首桶仅按 09:35
选择，未校验日期和重复；lane 日线/状态分别选 latest。这是输入闭合问题，不是已证明
9/24 真实缺口或来源分歧的根因。

LLD 01 的“Job-local sealed minute reference closure”拥有本片规则：同一个 verified
market release 提供日线/状态，显式付费重放才要求同日期/同调度契约 verified 原生 5m。
捕获 bytes 的输入 SHA、不可变有效视图、所选分母及装载成本在 Job 内共享；正式 handoff
带 expected artifact SHA，变动或不完整在网络/输出前拒绝。参照摘要不冒充完整跨 Job
缓存/恢复身份，5m 不增加全 48 桶真值门禁，竞价/标签和精确准入不变。

同一当前参照下重新解释已捕获的 HTTP 200，不因旧 receipt 的 unqualified 跳过可能已
合格且冲突的整票，也不把旧 qualified 当新资格。旧 raw/receipt 不改、无新网络请求。
完整性异常仍硬失败；可识别的适配质量拒绝只拒该源，不提前遮住好整票；最终质量和冲突
仍由现有仲裁所有。audit resume 的计数依旧来自旧回执时，摘要明确不是当前重验。
完整全量离线资格账本、跨 Job 恢复身份/失效与残余队列还未闭环，不称 B 或 #456 完成。

本地十一个分钟/provider 套件 `227 passed`，新增文件覆盖不可变共享、日期/分母/首桶/
SHA 故障、READY/PIT/契约 handoff，以及旧不合格标签不能掩盖当前双源冲突；运行角色
环境和节点受控发布仍待分别留证。没有读取 Key、请求真实来源、修改生产或恢复调度。
扩展文档、调度、lane 和研究 normalizer 为 `117 passed / 1 failed`，唯一失败仍是
既有 `P3-STRAT-003-B-R1 has no governing LLD work-item row`。focused Ruff 和 diff
whitespace 通过；`make check` 仍在进入目标前被 Xcode 许可阻断（exit 69），不接受许可或
修补另一研究线来粉饰完整验证。作者反向审查不是独立批准。

草稿 [PR #461](https://github.com/rv198-star/Quanthus/pull/461) 堆叠在 #459，code commit
`3434e87d4a75c439544d422b8868409a7cf5f32d` 经 GitHub fetch 到 CN01 的既有空闲隔离
checkout（保留自己的 `.test-venv`，未触碰生产 checkout）：同十一套件 `227 passed`。
扩展文档/调度/lane/normalizer/日编排六套件在 Linux 为 `142 passed / 1 failed`，仍仅
上述既有文档追踪失败；包括的日编排测试通过不授权恢复每日采集。Python/依赖及只读
依赖目录同 A 的 Linux 环境，禁用自动插件、禁止 bytecode 写回共享路径。
与 PR #454 `857afb387ec36abd6ed4a09cbaac4147c254d4dd` 的未提交组合树
`0b82001b7a176750d25af9e103e77693cc08ea57` 上十二套件 `235 passed`；树保留在隔离
checkout，是兼容性证据，不是合并或部署。#461 CI 同样在任何 step 前被已知账户
payment/spending limit 阻断（job steps=0），不能称为代码运行失败或完整 CI 通过。

另在该隔离组合树上，对已经保存的 9/24 `000001/600000/688001` 中转 raw 做三票只读
重放：verified READY 日线/状态及同契约 5m handoff、captured artifact SHA 均通过；
每票在现行精确门禁下为 240 行候选。Job 的 selected view digest 为
`ce2caacbeaf3a7a6ce9cb55d8e939627402184de685759299403bffba4c9c2f1`，本次一次性完整
装载参照耗时 `1.893884s`。没有读取 Key、网络取数、写回 raw/receipt 或物化候选。
这只验证真实文件的输入交接，不是全市场覆盖、独立采集、双源冲突解除或发布验收；
9/24 的来源缺口仍在，不能以三票或合成回归宣布 1m READY/DWD 完成。

### B3：META-OPS-MINUTE-009 / #462

LLD 01 的“Current offline minute qualification ledger”治理这个有界诊断入口。
`minute_offline_ledger` 从 B2 verified READY handoff 冻结参照，再对已有 capture 重新解释；
不是新的业务数据产品、来源判官或跨 Job 恢复授权。完整分母逐票保留原始/回执/压缩 bytes
身份、当前候选与拒绝、推断分钟、取得状态、完整性异常和整源冲突。旧 receipt 的分类及
Tencent done 不参与当前资格；P0 的实际缺行边界保持，坏候选与硬证据异常分开。

不调用来源或读取 Key，不更新旧文件，不生成候选 Parquet/READY/DWD/通知。输出为全新
TMP 目录和有限评估预算；中断、预算或未知故障不得写完成摘要。来源语义、完整恢复身份、
下一次取数授权和费用队列仍未决；不会因诊断输出完整自动恢复任何每日或追补 timer。
本片先做合成故障、本地/Linux隔离验证，再用已有9/24 raw只读重放；各项另记证据，
不把方法一致、测试通过或HTTP200当作来源正确、冲突解除或历史日期已完成。

草稿 [PR #463](https://github.com/rv198-star/Quanthus/pull/463) 堆叠在 #461，code commit
`153b54bbf515eb1eff02c3fdb0cc7f7f9d3b27a8` 的十一分钟套件本地/Linux均 `228 passed`，
含31条新账本回归。Linux复用已完成/干净的CN01隔离checkout，经GitHub fetch固定版本；
不切两个保留的未提交组合树、不动生产或共享环境。有效Python/依赖与B2相同，禁用插件及
共享目录bytecode写回；focused Ruff与diff whitespace通过。文档/账本组`68 passed / 1 failed`，
唯一失败仍为既有`P3-STRAT-003-B-R1`追踪缺口；`make check`仍在本机Xcode许可前置退出69。
CI run `36427714219` 三job均steps0，annotation仍是已知账户payment/spending阻断。
这些是作者工程/反向审查证据，不是独立批准、完整CI或生产角色验收。

随后在同一干净Linux code commit上，对CN01既有9/24全量raw做只读CLI重放，冻结paired
READY市场参照及同契约原生5m，实际bytes/SHA匹配。完整4992分母评估用时`192.688008s`，
参照装载`1.805867s`：2296整票候选无冲突（含7停牌）、2672被双合格整源冲突门禁阻断、
24无合格整源，无证据完整性错误。腾讯4877候选含7停牌，另115因推断缺行拒绝；中转2767
候选、2218拒绝、7停牌未请求。冲突计数并未全部消除表示尾差，不能都称真实价格差。
未知来源语义不被账本裁决；24中22仍日量不符，000755/300501仍竞价/首桶联合拒绝。

诊断目录为CN01 `/root/work/astock/data/quarantine/minute-ledger/2026-09-24-20260928T2120`，
records SHA `5755ace8cd5157b24ffcf2eaf40b1eae9830ef122684fa3ede62d7743b669297`，
identity SHA `f4d68d45668904414215b2e1583389ee04ca9f2e086af24da5a48f6ac2324518`。
同Job参照view为`60a2f4948901e34792b3f7e9a4b774684bf5227d63e3f8baeff5182f9f48a26f`。
没有读取Key/网络取数/物化或写回旧证据；这只是现行资格和缺口账本，不是跨Job复用、独立采集
或9/24 READY/DWD完成。生产与timers未改。B的在线/恢复闭合、C语义归因、D预算队列和
受控发布仍分别未完成；不以测试或这次诊断自动恢复每日采集。

#### B3 的 C 包只读差异视图（作者阶段）

同一账本可显式启用 `--describe-source-differences`，复用本 Job 已捕获、已按现行精确门禁
合格的整票，不另取数、读取 Key 或再次逐票加载参照。它只记录对齐 OHLCV 数值的实际差值、
字段/分钟计数、最大差与前三个差异分钟；`1e-10` 仅是描述统计分组，不是准入容差或舍入规则。
“所有价格差小于该分组线”不能证明浮点成因；量分布差异无容差，两种差异都保持原冲突阻断。
未知或未对齐输入拒绝，输出不包含修正候选，不决定真实来源、标签或竞价语义。

新增 `qualification_records_sha256` 绑定去掉差异注释后的现行资格行，供本次诊断与既有完整
账本作机械核对；不是跨 Job 恢复许可。代码/合成回归、Linux与真实既有 raw 重验另留回执。
C 的来源合同与因果归因、发布决议尚未因此完成，也不改变每日/lane/maturity暂停决策。

该视图 code commit `b42aad1006d26e3376c8a5c4bbb1346f5f962844` 的十二分钟套件本地
`243 passed` / Linux隔离 `243 passed`，focused Ruff与whitespace通过；文档/新增诊断组
`83 passed / 1 failed`，唯一失败仍是既有P3追踪缺口。`make check`仍在Xcode许可前置退出69，
完整检查及生产运行角色资格没有因此获得绿灯。

随后对同一4992封存分母、同一既有raw完成一次有界只读评估，用时`193.979063s`，
参照装载一次`1.76129s`，无网络、Key、候选物化、READY/DWD或通知。目录为CN01
`/root/work/astock/data/quarantine/minute-ledger/2026-09-24-source-differences-20260928T2212`。
records实际文件SHA与摘要一致：
`d3805700f12aee1beebd3dd14561cf9624a22363230151f566cf7b74323109f0`；identity SHA为
`9960f25086c3634180cfa960697dfcee7b90f32a463116d64ac2f2c072893aa6`。
剥离新增描述后的资格记录SHA仍为上一完整账本的
`5755ace8cd5157b24ffcf2eaf40b1eae9830ef122684fa3ede62d7743b669297`，参照view SHA仍为
`60a2f4948901e34792b3f7e9a4b774684bf5227d63e3f8baeff5182f9f48a26f`。
2296候选/2672冲突/24无合格整源的当前资格结果未改变。

2676个当前双合格整票中，4个数值完全相同；2672个均有至少一个大于等于该诊断分组线
的价格差或逐分钟股数差，没有“仅小于分组线的价格差”整票。1588票存在分钟量分布差，
2653票open、2648票high、2639票low、1763票close有差异（字段可重叠）。这证明这些阻断
不能仅靠消除微小表示尾差解决，不证明所有同名OHLC有相同供应商语义、哪方是真值或源错。
门禁未改、不舍入或平移；P1仍需时间/竞价/午后/缺行合同及证据，不能由数值分组自动发布。

## 5. 评审与交付顺序

采用上级 #424 已声明的同一助手“方案 / 反向审查 / 测试”阶段，不称独立审批。
首片结论为 `PASS_SCOPED_ENGINEERING / NO_GO_PRODUCTION`；其余包按依赖逐片冻结。

| 反向问题 | 当前处置 |
| --- | --- |
| 多源日总量都正确就可投票吗？ | 不可，时间与 OHLC 语义未闭环；当前冲突保持阻断。 |
| 加哈希可证明没有造分钟吗？ | 不可，哈希只证明一致；原始来源完整性需 PR #454 等准入闭环。 |
| 同目录 keep=last 可快速修复吗？ | 不可，拒绝复用，新 attempt 保留整票来源所有权。 |
| 读回增加成本或要求文件系统能力吗？ | 需量测并做 Linux no-replace link 验证；不假设所有文件系统支持；不支持应失败而非退化为覆盖。 |
| 代码修好就能补齐 9/24 吗？ | 不能；仍需实际来源覆盖、冲突可归因及完整日期发布证据。 |
| 未决口径会迫使每小时全扫吗？ | 不会；已有 raw 优先，窄 canary 和显式下一行动，遵守有限预算。 |

首片、B1、B2 的 GitHub 草稿、本地/Linux 回归及 PR #454 组合 smoke 已留证；
运行角色资格、完整跨 Job 恢复身份和静默窗口内逐节点发布评审仍未完成。
9/28暂停前已启动的同一日编排均已自然终止：CN01 21:15:05、Hermes 21:53:58 CST
均exit2；没有强杀、重启或途中替换。以后每日触发及小时跟进均已按Owner暂停。
部署保存原 HEAD、unit/timer 状态，GitHub fast-forward、同版本测试和回执；已有 disabled
timers 不因代码发布自动开启。回滚不重写 Base、READY 或失败证据。

十交易日观察从批准版本双节点验收后开始；保留所有节点×交易日分母和时间，不剔除
供应商故障、迟到或手工恢复。交付必须分开回答代码、部署、9/24 数据和新日期自动运行，
不以其中一项完成关闭其他项。优先级 P0 表示安全闭环，不表示已解决外部数据可用性。

## 6. 当前工程检查点

### Owner 授权的 9/24 冷拉验收（2026-09-28）

Owner 要求先验收、全新获取 2026-09-24 数据并尝试产出 DWD，成功后才讨论发布部署。
`ops/bin/run-minute-cold-acceptance` 是一次性、最长八小时的隔离测试入口，不是新调度、
生产部署或通知入口。组合 #454/#457/#459/#461/#463 后，在 CN01 的隔离 GitHub checkout
与全新 `quarantine/minute-cold-test/<job>` 运行；每日、lane、maturity 与小时跟进均保持暂停。

全新请求日线/交易状态、原生 5m、付费中转 1m、腾讯 1m；禁用旧分钟 raw、seed、collector
override、旧 READY 和生产 env 的复用。证券主数据、调整因子及事件快照捕获实际 SHA 后
只读输入；现有 `ods_snapshot` 不做供应商新采集，不能把这些输入或 Base 称为冷拉。
实有格点、精确量价、整源冲突、四 lane 与 DWD 规则不变。付费源单 worker、每秒最多一次、
显式有界全市场；202/429/5xx 不无限重试。受保护 key 仅打开 root-only 描述符交给 audit stdin，
不读取到父进程变量或写副本。超时只清理本测试自己创建的进程组，原生产失败服务不重启。

仅四 lane 与完整双源冷拉通过时，现有 DWD builder 在测试目录生成并深验候选包；
测试目录内的 CURRENT 不等于生产发布。失败保留分阶段日志和 summary，不删除旧证据。
成功也不自动合并、部署、发送生产邮件、同步 OCI 或恢复 timers。
组合单元测试不代替运行角色或来源语义验收；尤其旧 seed 仅验计数和文件存在的漏洞
仍未完整闭合，本次禁用该入口，不把其测试通过记成 P0 所有入口验收完成。

后续准入审查确认：1m seed 当前没有 raw/参照/最终文件的完整现行身份 verifier。
按 LLD 的跨 Job 复用边界，在独立分片直接拒绝该旧入口，连明示零推断计数也不能放行，
而不是临时创造一个未批准的 seed 格式。旧 seed 与 receipts 原样保留；5m 行为不扩大。
当时执行的冷拉固定于 `84170737a246f9e82547528f6398fa8f75978569`，没有 seed，不能
途中换 checkout。此后续分片的测试与冷拉固定版本须分开记录，不能冒充已在生产部署。

验收中另发现付费客户端默认跟随重定向，可能向非授权地址转发自定义 Key header。
在进入 paid 阶段和读取 Key 前，测试 `2026-09-24-20260928T2255` 已受控停止：新日线
4992 票 READY；原生 5m 最后 checkpoint 为 2500 done/error0，未完成、未生成 DWD。
旧 summary/status 的 running 不是仍有进程；已核原 PID 全部退出，transient unit 卸载。
这是操作者安全停止，不是自然来源质量或网络故障，不把 systemd not-found 默认 success
字段称为成功。全部原始/失败证据保留。修复为显式 TLS 校验且拒绝任何重定向，反向测试
HTTP 降级和跨 HTTPS 主机均只能收到一次请求；新固定候选从另一个全新目录冷拉，不复用
这份中断测试的分钟或 READY，不改生产、通知或 timers。

同一候选亦将既有 cninfo 公告入口及 Referer 改为同主机 HTTPS、显式校验证书并不跟随
重定向，没有改变日期/代码/分页语义。CN01 单票无凭据只读 HTTPS 预检返回 200 JSON；
不是全市场公告可用性验收。冷拉的付费 Key 仅发给授权 HTTPS 中转，不存在明文 HTTP
备用或证书绕过。

### 全新冷拉的拒绝回执（2026-09-29 01:24 CST）

新的固定候选 `9f04c9bb3a892310ed927f926dadf36c9c2c4599` 已在 CN01 完成
400 项相关回归；这不是独立审批、完整锁定角色资格或数据验收通过。
仅通过 GitHub fetch 固定隔离 checkout；没有途中替换代码。
新 job 为 `/root/work/astock/data/quarantine/minute-cold-test/2026-09-24-20260928T2331`，
从 9/28 23:31:31 CST 运行至 9/29 01:24:34 CST，真实终态
`cold_test_rejected_not_published`。不复用前一个安全中止 job 的日线、分钟或 READY。

| 阶段 | 实际结果与边界 |
| --- | --- |
| 新日线/交易状态 | exit0，隔离 market release READY；不把只读静态输入称为新供应商采集。 |
| 新原生 5m | 4992/4992、error0、exit0，隔离 release READY。 |
| 新中转 1m | 扫描 4760/4985；4745 个 HTTP200、7 个 HTTP202、8 个 HTTP502，尾段502触发熔断，225票未请求。HTTP200中2735个现行单源候选、2010个质量拒绝，不是全市场兜底成功。 |
| 新腾讯/整票仲裁 | 仅扫描325/4992：148 done、174双合格源冲突、3无合格整源；连续20个语义错误被共用的提供者熔断计数截断，4667票未扫描。collector exit2、lane exit4。 |
| 事件与 DWD | 未到达，测试 DWD 目录不存在；没有1m READY、DWD包、生产通知或OCI验收。 |

3个无合格整源为000404/000726/000755：腾讯未观察到的分钟被拒绝；中转前两票日量
仍分别差-12884/-7100股，000755 without-auction日量精确但首5m差-2000股，with-auction
日量多6600股。这不是取消首桶条件、填零或补差的许可。
174个冲突不是腾讯接口不可达：新raw只读重放000001/000002/000006，两源各自通过
240实有格点、精确日量和close，仍分别出现最大0.01/0.02/0.03价格差。000001的11个
不同分钟没有仅微小表示尾差；三票不证明所有冲突的机制或任何来源是真值。
000001新capture SHA：腾讯 `b000de06521da9c838bdedeebb88089184472af7b044ca949bb191d2c697f156`，
中转 `d656479a0ab3026f7e57d519f3e61f3c58a64278230de1ccba88558cd9169f1b`。

控制流缺陷已拆为 [#466](https://github.com/rv198-star/Quanthus/issues/466)：语义冲突必须
保留逐票/发布阻断，但不应计作网络熔断并遮住剩余分母。需增加21个连续冲突加末尾好票
的回归，真实提供者故障仍有界熔断；完整性/身份/存储故障仍硬阻断。不能将这一修复
误称为已解决真实来源差异，也不盲目重拉本 job 已捕获的4745个HTTP200。

封闭证据：summary SHA
`364c8f6b631f06f967ee0cf1a6eb4827379353fb48e23414e4baa40e7a7d23a5`；
1m checkpoint SHA
`dd9a8bede985849a691028ffbe2a88e57e01d4574caf0ec2fa8bb12091a4c205`。
主进程及所有已记录子进程均已退出。事后复核 CN01/Hermes 生产 HEAD 均仍 `be96a7cb`，
六类timer逐节点均inactive/disabled/无下一触发，unit文件与生产CURRENT SHA均与测试前
一致。PR #464/#465继续草稿、未合入或部署；不恢复定时任务。

结论是 `NO_GO_DATA / NO_GO_PRODUCTION`：工程回归通过不替代真实DWD产出。
本次冷拉已结束，但9/24数据修复和#456整链路优化仍未完成；来源语义/缺行、类型化
故障恢复、完整恢复身份及后续端到端验收分别保留，不能据此关闭主任务。

2026-09-28 首片分钟相关七个套件本地 `118 passed`，focused Ruff 与 diff whitespace 检查通过。
文档/调度三个套件 `57 passed / 1 failed`；唯一失败为既有
`P3-STRAT-003-B-R1 has no governing LLD work-item row`，在未改文档的 PR #454 checkout
上以相同测试复现，相关文档、脚本和测试与本片基线相同。不修补另一研究线来粉饰全仓绿灯。
`make check` 在进入目标前被本机 Xcode 未接受许可阻断（exit 69）；完整检查未运行，
不自动接受 SDK 许可。扩大到 provider、lane runner、日编排、成熟度、catalog、文档和
调度八个套件后为 `167 passed / 8 failed`；其中七个 shell 编排测试同样被 Xcode
许可阻断，一个是上述既有文档追踪缺口。在基线相同的 PR #454 checkout 上，日编排
套件复现相同七项失败（`18 passed / 7 failed`）。这些不是新增改动的回归，也不等于
完整验证已经通过。子进程测试显式使用绝对 `PYTHONPATH`，避免切换工作目录造成导入误报。

同日 CN01 仅通过 GitHub fetch 建立隔离 checkout，对
`429b28044153f7e53f1e68311633f69860a0d71c` 做 Linux smoke：分钟七套件
`118 passed`，上述扩大八套件 `174 passed / 1 failed`（仅既有文档追踪缺口）。
运行环境为 Linux `5.15.0-106-generic`，文件系统报告 `ext2/ext3`，Python `3.10.12`、
pandas `2.3.3`、pyarrow `23.0.1`、pytest `8.4.2`。测试 venv 单独创建，pandas/pyarrow
只读使用节点 `/root/work/Quanthus/.deps`；`PYTHONPATH` 首项为隔离 checkout 的 `src`，
显式禁用 pytest 自动插件，没有修改共享 collector 环境、访问真实来源或读取 Key。
这验证了该 Linux/文件系统上的暂存读回和 no-replace 行为，不代替 #431 的完整锁定角色环境
与部署资格，也不证明断电耐久性或多进程整个目录的安全。

另一个隔离 checkout 仅组合 GitHub 的本片 commit 与 PR #454
`857afb387ec36abd6ed4a09cbaac4147c254d4dd`，未提交的组合树
`e46319db1da52bf7436998618409c0e55afe9d9d` 上八个分钟/seed 套件 `122 passed`。
它是兼容性测试，不是合并、独立评审或生产放行。草稿 [PR #457](https://github.com/rv198-star/Quanthus/pull/457)
CI 在任何 step 前仍因账户 payment/spending limit 被阻断，annotation 已核实；不把它称为代码回归。
测试后两采集节点生产 HEAD 均仍为基线，CN01 晚间编排在运行，Hermes 原 timer 保留；
恢复 timers 仍 disabled。没有改生产代码、服务或 timer。
GitHub/逐节点放行和运行观察尚待执行；上述计数不是 9/24 来源资格、READY 或数据发布验收。
