# P3-STAGE-005：分尺度参考机制工程验收

| 字段 | 值 |
| --- | --- |
| Work item | P3-STAGE-005，A #279 / B #280 / C #281 / parent #282 |
| Governing design | [分尺度参考协议](../design/stage-order-reference-protocol.md) |
| Delivery plan | `../../config/stage-order-reference-delivery.v1.json` |
| Protocol identity | `sha256:927edd8451c9db9adb0ac06e24dcbc55d11e1ac82aac87123a4d9627557eaf1e` |
| Baseline | `72b83e59b4763e03404d7598657a72b7472e9a64` |
| Status | PASS_ENGINEERING_REFERENCE_MECHANISM；A/B/C工程闭环完成，真实语义资格未产生 |
| Acceptance ceiling | 合成工程机制，不是0/2真实参考可辨识性、独立专家验收或市场资格 |

## 1. 本轮实际交付

本轮把用户批准的[第二轮模拟评审结论](../reviews/stage-0-2-business-boundary-reaudit-20260910.md#9-第二轮四角色模拟复审业务路线收敛)
落实为治理性设计、冻结协议、库实现及验收，不再次等待独立模型401恢复。

- 20会话主尺度与10会话近期条件分别记录；保留61会话上下文和上市/坐标/Fact修订身份。
- 一次批量准入、Trend/Order各一次构建；案例间复用共享源行，评审视图不会再计算Facts。
- 每个案例原始short_term/fund意见分别保留，明确实际source kind、origin、session。
  量化和数仓职责落在协议/证据审核，不被伪造成额外两票语义真值。
- 同尺度冲突、主评歧义、都不成立、输入不可用分别记账；近期意见分歧不暗改主尺度。
- 合成、规则、AI或human来源申报均不自动产生独立性、真人或市场资格。
- 不可变包保留输入、protocol、每例证据、原始意见、解析和分母；独立读回从保留输入重建，
  不仅比较调用者自己重算的文件哈希。生成时钟不进入案例或语义产物身份。
- 按stratum分别记录参考数量，保留unspecified，不假称代表性抽样或总体precision。
- 可选进度回调输出分阶段/总耗时、行数、bytes与built_once；不进入评审输入和语义身份。

实现：`src/quanthus/stage_learning/order_reference_protocol.py`。命令行只有已有包验证：

```text
PYTHONPATH=src python -m quanthus.stage_learning.order_reference_protocol /absolute/reference-bundle
```

该入口不提供数据获取、抽样、搜索、候选发布、强制仲裁或Worker提交。构建/意见解析/
发布是明确的库API；没有将研究脚本伪装成可投产的每日标签服务。

## 2. 工程证据与反例

| 边界 | 已执行的验证 |
| --- | --- |
| 一次共享前提 | 同批两个案例、每例两次reviewer_view，Trend/Order各仅调用一次 |
| T-only视图 | 同批早期/晚期案例；早期视图不含晚期行情，且与只构建早期前缀时的案例完全相同 |
| 时钟边界 | 修改调用方较晚的cutoff不改变语义证据；修改generation clock只改变物理manifest |
| 10/20分层 | 两条已准入80会话合成路径的主尺度值相同，近期宽度变化一负一正；并存不会被压成全局唯一阶段 |
| 可用性 | 缺失价格、历史不足、上市/坐标切换保持不可用/重预热，不产生Stage 0 |
| 参考分歧 | 相反主评保留AMBIGUOUS；单份意见同时确认0与2标为RULE_CONFLICT；双FALSE为NEITHER |
| 来源诚实 | 同origin两角色及自填human等来源均不能将independent/human/market validated改为true |
| 严格输入 | 缺评、重评、双角色同reviewer、陌生证据、错误尺度、候选/未来/置信度夹带字段被拒绝 |
| 保留证据 | 原始分歧意见逐字保留；input、case、review、resolved、summary均在包内可回查 |
| 独立读回 | 篡改summary后重新生成所有文件哈希仍不能通过重建；伪造case值并重算case_id同样被拒绝 |
| 物理封存 | 覆盖、遗漏READY、多余文件、损坏文件和symlink包拒绝；读回有固定总大小上限 |
| 旧实验不动 | 原runner、开发/参考配置及交付结果保持原版本；旧候选未补发通过 |

合成意见是协议测试的手工夹具，不是本助手已经完成真实行情标注。测试中用于身份检查
的`aaaaaaaa...`是显式夹具revision，不是实际部署或模型执行版本。本记录绑定实现基线与
工作区检查；最终Git提交由后续工具回执绑定，不伪造执行产物的commit。

包内源行与代码版本是可追溯的调用方绑定，不自动认证上游外部来源或作者身份。
`external_source_verified=false`、`human_expert_validated=false`与
`independent_validation=false`始终保留。schema能禁止结构性夹带字段，不能证明评审者
没有在别处看到未来，也不能判断自然语言理由是否正确。

## 3. 实施后的四职责符合性复核

这是同一助手的实现复核，不是四个独立模型重审。

| 职责 | 复核结论 |
| --- | --- |
| 量化 | 参考意见不由候选产生，未新增市场阈值或结果依赖补样；stratum和来源分母可见。规则一致不冒称市场准确率。 |
| 短线 | 主尺度和近期变化均可表达；正面0/2、未知、都不成立、冲突不混用。近期反证可通过原始理由/反证引用记录，不能机械投票改判。 |
| 基金 | 没有重跑旧实验、扩参、回滚连续Facts或创建强制标签入口；只补上一轮识别出的参考机制缺口。 |
| 数仓 | 源行只存一次、视图按T截断、源码/协议/输入绑定，源接收metadata与实际生成时钟不伪装历史可见性。独立重建是显式验收，不是每个消费者的重复前提。 |

publisher执行一次显式重建审核，verify另执行独立读回；这两种验证不是业务消费者内的
重复物化，不宣称它们是不同实现或不同人完成的独立金融验证。库本身不打开DWD/provider。

## 4. 测试账本

初次新机制测试：43 passed。补足时钟、分层、来源、原始意见留存等测试后，组合原实验/
Facts/文档检查：133 passed，8.05秒。期间Ruff发现局部导入/行长/嵌套with问题，按新文件
范围修正；没有扩范围修复仓库原有153条lint问题。

最终核查：

- 首次全仓：1904 passed、11 failed、7 skipped，243.08秒。11项失败共同源于新工单缺少
  LLD工单表映射，非算法失败。补齐治理表及关闭状态后，相关109项检查通过（2.89秒），
  并新增专门的机器计划/LLD/索引状态绑定回归，防止同类遗漏。
- 最终 `make check PYTHON=/home/ubuntu/GitHub/Quanthus/repo/.venv/bin/python`：doctor、compile、
  pytest、planning-check全部完成；**1916 passed、7 skipped，pytest耗时244.22秒**。
  相对基线新增55项测试。2个跳过项依赖未挂载的生产专家产物，5个依赖未配置PostgreSQL
  测试DSN，不声称它们已经运行。
- 新增module、issue同步脚本、两个测试文件的聚焦Ruff通过；全仓历史153条问题没有在
  本轮重跑或修复，不声称全仓lint通过。
- `git diff --check`通过。原order_proposition_development.py、原开发/参考配置及
  stage-order-proposition-delivery.v1.json的差异为空。没有更改旧实验的范围、阈值或结果。
- 源行内容哈希在每批只计算一次，再被案例引用；逐评审视图不触发Facts重建。封存审核
  和独立读回的重建保持显式验收用途。

最终Git提交、推送和工单实际状态以随后工具回执为准，不能把文件落盘等同远端发布。

## 5. 下一阶段与不变限制

P3-STAGE-005接受的是可执行参考机制，不是Stage 0/2候选合格。下一项研究工作应当是
**真实T-only参考可行性**，先确认获准使用的既有证据材料和事前限定的样本/成本/停止
方案，再谈候选选择。当前没有足够证据承诺旧160例可以直接读回；若必须重新构建，必须
明确记为新开发计算，不能伪装历史读回或新盲测。

本轮没有RX6800 Job、真实行情/参考读取或新标注，没有声明解决原9/20的逐例根因。
2025不动、2022—2024不重读；原v1继续INCONCLUSIVE且封存。正式LabelCard、primary-stage、
日更、策略消费和交易权限不变。后继设计不再卡在401或重复模拟评审，但市场资格仍需
真实证据，不能由工程测试或本验收文档替代。
