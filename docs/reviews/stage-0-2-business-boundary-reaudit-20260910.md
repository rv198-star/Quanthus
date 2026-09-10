# Stage 0/2 业务边界与参考机制复审

| 字段 | 值 |
| --- | --- |
| Audit date | 2026-09-10 |
| Audit ID | STAGE-004-REAUDIT-20260910 |
| Related work item | P3-STAGE-004，原实验保持关闭 |
| Design status | draft |
| Delivery gate | not-applicable |
| Authority | 非治理性审计记录和后继设计建议；不改变冻结合同，不授予新运行权限 |
| Parent | [原实验设计](../design/stage-0-2-order-proposition.md) |
| Reviewed baseline | `72b83e59b4763e03404d7598657a72b7472e9a64`；审计开始时与远端 main 一致 |
| Original execution revision | `1878c625ad04b23793507d92bf0e3008052a5a85` |
| Provenance | 单一助手的代码审计、实际合成路径探针和量化/短线/基金/数仓四视角交叉分析；不是四位独立专家会审 |
| Independent AI review | 首轮及第二轮重试均为 HTTP 401、退出码 1；无独立评审回答。第二轮依用户明确授权完成四角色模拟复审，见第9节 |
| Market-data / Job activity | 未启动新 Job、未补样、未调参、未读取 RX6800 真实行情或逐案例产物 |

## 1. 结论

**保留原实验的 INCONCLUSIVE_REFERENCE_SUFFICIENCY；不直接补样，不因此放弃 0/2。先解决业务命题、观察尺度、参考判断和候选可见信息之间的一致性。**

本轮完成了可复核的源码审计和四视角分析。独立 AI 评审入口仍不可用，因此不能宣称已经取得独立专家共识。审计发现不等于 Stage 资格验收或新业务定义获批。

当前证据显示的是实验设计与新复审需求之间的缺口，而不是已经确认的“代码没有按冻结设计实现”。原工程执行完成、原研究证据不足、正式业务使用未获准，是三个不同结论。

## 2. 基线与旧结果的证据边界

审计开始时工作区干净，HEAD 与 `git ls-remote origin refs/heads/main` 均为上述审计基线。当前 runner 和两个冻结开发/参考配置相对原执行版本的 `git diff --name-only` 为空。上一轮尝试写入的四视角预审文件不存在；本文件是本次新写的复审记录，不冒称恢复了已落盘的旧报告。

[原验收记录](../acceptance/stage-0-2-order-proposition-result-20260910.md)及[交付合同](../../config/stage-order-proposition-delivery.v1.json)绑定：

- 唯一技术重试：`stage_87d166eb3f4a4f7f858f9b4f3e238462`；执行 succeeded，约 660.726405 秒；
- 224,346 条可用观察，160 个参考上下文，四个抽样桶各 40 个；
- 参考标签：Stage 0=69，Stage 2=9，ambiguous=65，neither=17；
- Stage-2 最低参考数 20 是两组搜索的共同前置条件；两个候选参数及候选对重叠数均为 null；
- 接受范围仅为执行完成和保守无候选收尾，不是正式标签或完整逐 trial 独立审计。

上述运行数字来自本次读取的已提交记录，并非本轮重新读取 RX6800 得到的终态回执。本轮未核验远端完整目录、每条原参考或全量 trial 数组。

独立评审可用性检查使用只读 sandbox、approval=never、ephemeral，不允许模型调用工具、读文件或改动数据。配置显示 gpt-5.6-sol/openai，但请求未认证成功，不能将其记录为实际模型评审。未更改凭据、服务或权限；本轮不判断认证故障的配置根因。

## 3. 审计发现

### R01：参考与候选的可见信息不匹配——已用通过准入的合成价格路径复现

**类别：实验表示/目标一致性问题；不是已证明的实现违约。**

[runner](../../src/quanthus/stage_learning/order_proposition_development.py)中：

- `_bucket` 按 20 日信息抽取 order_like；
- `_predict_stage2` 与 `_predict_stage0` 只消费四类 20 日几何量；
- `_review_one` 的参考判断还使用 10 日宽度变化与中心移动。

这一区别是[冻结设计](../design/stage-0-2-order-proposition.md)明确允许的，不能把它追认成实现错误。但参考依据候选不可见的信息改变标签时，必须验证这是否符合被检验的业务命题，而不能假定多补几个样本就能解决。

本轮构造了两条各 80 个会话的合成 HLC 序列，全部通过现有 `admit_daily`，经正式 Trend/Order Fact builder 与 `build_order_observations` 各生成一条末日观察。没有直接伪造 OrderObservation 特征。

| 项目 | A：近期收缩 | B：近期扩张 |
| --- | ---: | ---: |
| 合成输入准入 | 80/80 | 80/80 |
| 20 日绝对方向效率 | 0 | 0 |
| 20 日区间宽度 | 0.12 | 0.12 |
| 20 日相对前 20 日宽度变化 | -0.25 | -0.25 |
| 20 日绝对中心移动 | 0 | 0 |
| 10 日相对前 10 日宽度变化 | -0.333333… | +0.5 |
| 10 日绝对中心移动 | 0 | 0 |
| 抽样桶 | order_like | order_like |
| 原 R0 Stage-2 候选 | true | true |
| 原 R0 Stage-0 候选 | false | false |
| 三套参考 policy | 全部 stage_2 | 全部 stage_0 |

**可支持的结论：仅以这四个 20 日量为输入的确定性候选，不能在这两个合法输入构造上完全复现该参考的不同标签。** 这是由相同候选输入与不同参考目标直接推出的；在相同输入表示中调整阈值不会区分这两个样例。

**不可支持的结论：** 这不证明原 160 例中存在这两个样例，不给出该现象在真实数据中的频率，不证明所有候选都无法达到原有统计门槛，也不证明 Stage 0/2 在业务上不可辨识。

DailyInput 在此包含 HLC 而非 open；日期、来源身份和会话日历均为测试夹具，不是实际交易所会话或真实证券案例。证明的是现有输入合同下可实现的合成价格路径，不是历史市场实证。

### R02：所谓三专家参考是三套确定性 policy，不是独立业务真值

**类别：参考方法与可声明结论的边界。**

[Panel 配置](../../config/stage-learning/order-proposition-synthetic-panel.v1.json)中的三个名称映射到固定阈值。`resolve_references` 调用 `_review_one`，没有调用 AI 或真人。

因此，这个实验可以测量候选与一套预设规则参考的一致性。它不是完全相同函数给自己打分，因为参考有额外 10 日信息；但也没有建立独立业务语义验证。角色命名、互相隐藏和候选参数不进入参考，都不能独自证明参考有效。

旧 schema 名 `SYNTHETIC_EXPERT_CONSENSUS` 和旧证据不回写。后继呈现应直接说明“确定性规则参考”；只有实际发生独立 AI 或真人评审，才记录对应来源。独立 AI 也不自动等于人类专家金标准。

后继参考可以检查更丰富的同一 T 时点证据，但必须明确被判断的目标，以及候选是否保留完成该判断所需的信息。不能为了获得一致性，反过来让参考复制候选规则。

### R03：原结论中的样本不足成立，但补到 20 只是必要条件

**类别：参考可行性与统计解释。**

本轮对三个参考 policy 的四条 20 日上界逐一断言：每套 policy 能输出 Stage 2 的输入，都满足 order_like 桶条件。裁决器也只能输出某套 policy 的标签。因此，在冻结实现与已记录汇总一致的前提下，9 个 Stage-2 正例全部来自已抽到的 40 个 order_like。

可由此推得 **9/40=22.5% 是本次所抽 order_like 案例转为明确 Stage-2 参考的比例**，不是总体市场发生率，不是候选准确率，也不是随机市场样本的正例率。这是代码蕴含关系加已记录汇总的推导，不是逐案例远端读回。

原 `9<20` 共同门槛继续有效。即使另一个实验得到 20 个正例，仍须满足精度、覆盖、反例、歧义和候选对一致性等门槛；不能用达到最低数量代替资格验证。也不能仅因 Stage 0 有 69 个正例，就在原合同下单独补发 Stage-0 通过结论。

新设计宜把参考可行性与候选选择分开，在搜索前确认参考是否足以回答问题。旧合同要求保留 81+27 个 trial，不能追认其遵守留痕要求为运行错误；本轮没有测量各阶段耗时，不声称搜索占了多少运行成本。

### R04：标准产物没有逐案例参考账本；不能默认“直接读回160例”

**类别：已批准摘要式产物与新复审需求的能力缺口。**

[开发配置](../../config/stage-learning/order-proposition-development.v1.json)明确 `summary_only_plus_candidate=true`、`observation_rows_published=false`。runner 只发布 `summary.json`、`candidate.json`，以及 manifest/READY。

summary 保存参考汇总和完整 trial 评分，但不保存 contexts、各个参考标签、两个主评的独立意见或裁决理由。源 manifest 的 digest 不是源 manifest 正文，更不是每个案例的原始证据。

这与冻结的摘要式发布设计一致，不能叫作已经证明的漏写 bug。即使适配器能够读完整 summary，也不等于原 160 例明细就在那里。

本轮没有清点 RX6800 其他旁路文件，因此不能断言整个节点不存在可恢复材料。后续应先检查获准读取的既有不可变证据；没有就记录缺口。不得重新读取 DWD、重抽案例或改标签后，将新产物包装为旧明细的“读回”。

未来真正产生参考时，最低限度应保留 T-only 证据引用、判断目标、评审身份/版本、逐例意见、反证/未知项、分歧和裁决。运行证据留在受治理产物，不把行情数据提交 Git，也不为了复审另造整套永久 ADM。

## 4. 四视角交叉评审——单一助手分析，不是独立投票

| 视角 | 核心意见 | 对其他视角的约束 |
| --- | --- | --- |
| 量化 | 先区分参考不足、信息表示不足、候选性能不足。当前不能把三者合并成“0/2不行”。 | 不可因样本少就删10日条件、降低数量门槛或选择诊断排名第一的候选；也不能把新参考重新包装成独立测试集。 |
| 短线 | 20日整体整理与最近10日扩张可以同时成立，合成路径已证明几何上可实现。业务必须说明是在描述哪个尺度，还是复合条件。 | 不能凭整理描述推断吸筹、未来突破或买入时机；不能以短期扩张不满足双尺度收缩为由，未经验证地宣布所有中期结构都无序。 |
| 基金 | 不为了凑齐编号继续投入无界实验。连续 Facts 保留为限定研究范围的基线；离散标签需要说明怎样帮助具体研究分层或专家组织。 | 描述有用不等于交易有用，旧粗状态实验也不等于所有离散标签无价值。0/2不能成为整个专家体系唯一入口，但这不自动开放新消费权限。 |
| 数仓 | 事实值、参考判断、候选输出和未来结算必须可区分、可绑定。业务时点和运行时间分别归属。 | 哈希不能替代明细保留；不能把缺失数据、未实现或无法判断归为Stage 0；也不能为方便审计把临时产物升级永久资产。 |

### 交叉质询后的取舍

**量化对短线：** 不能看到 R01 就马上删除 10 日条件。处理：旧版本不改；后继明确是单尺度命题还是双尺度复合命题，再决定什么信息有决定权。

**短线对量化：** 可计算、可复现不意味着命名已经表达了业务需要。处理：先冻结可观察描述和反例，再检验规则；收缩是否是“有序”的必要条件、仅是子型还是另一个条件，不能由结果好不好来决定。

**基金对继续投入：** 为什么不直接转向 1/12？处理：不永久放弃0/2，也不立即追加真实运行。先解决可复用的参考方法缺口，避免把同一套不充分的验证方式平移到1/12。

**数仓对逐例复核：** 案例证据是否真的存在？处理：先确定证据可用性，不承诺一定能恢复160例；不得暗中重建。

## 5. 后继路线建议——不是新增执行授权

建议路线为 **先完成“0/2 业务定义与参考协议”，再决定是否进入有界新开发验证**。不直接按原办法补样，也不要求用户替算法选择任意阈值。

本节保留首轮建议。第二轮在第9节重新质询并限定其含义：20日优先不是合成反例证明的业务真理；选择双层描述是本项目的有界设计建议，不能通过让参考复制候选或删除难例获得通过。

推荐的首选设计方向：保持“给定尺度的结构描述”，以现有20日候选观察口径为主命题，将10日近期变化作为单独可观察条件记录。参考评审可以看到两者，但应明确自己在评价哪个对象，不以隐含的短期条件改变中期标签。

这不是认定单尺度一定比双尺度更好：如果业务定义确实要求双尺度同步成立，就应将其明确命名为复合命题，并使候选获得相关10日信息，而不是仍让20日表示承担全部判断。两条路线都不能直接覆盖旧定义或旧结果。

后继设计最低交付物只需三项：

| 交付物 | 必须回答的问题 |
| --- | --- |
| 一份业务边界/反例表 | 描述对象、时点、尺度和参照是什么？收缩与稳定的角色是什么？哪些是明确0、明确2、可并存的跨尺度条件、无法判断或输入不可用？ |
| 一份参考协议 | 谁实际判断，看到什么T-only证据，怎样与候选实现保持方法独立，怎样保存分歧，怎样先证明参考可行性？ |
| 一份有界验证与证据保留方案 | 可以复用哪些已存材料，缺哪些证据，新数据活动是否需要单独范围，如何停止，以及继续/暂缓的判据是什么？ |

未完成这些设计前不提交新Job。若后继需要真实参考试点，应另行明确样本、费用、输入和停止边界，结果允许继续为INCONCLUSIVE；不得以本审计报告作为自动执行许可。

Stage 0仍须正面无序证据，不能是剩余类别；Stage 2不蕴含吸筹、向上预期或买点；两者可都不成立。连续Facts不回滚。2025完全不动，2022–2024 arbitration不重读，已看开发材料不冒充新独立验证集。正式标签、primary-stage、日更、策略和交易权限不变。

## 6. 核查与验证账本

| 检查 | 本轮结果 |
| --- | --- |
| 工作区与远端main身份 | PASS，基线一致，初始干净 |
| 执行版本至审计基线的runner/两配置差异 | 空；没有语义修订 |
| 独立AI最小可用性探针 | FAIL：HTTP401、退出1；没有独立评审内容 |
| 两条合成价格路径准入与正式Facts构建 | PASS：各80/80准入，各1条末日观察，候选20日特征逐值相同而参考相反 |
| 三套Stage-2参考蕴含order_like桶的约束检查 | PASS：3×4项数值上界检查 |
| 聚焦测试 | 79 passed，5.38秒 |
| 首次聚焦命令 | 误写issue-plan测试为contract目录，pytest退出4、0项执行；改为实际unit目录后得到上述79 passed |
| 全仓make check | PASS：doctor、compile、pytest和planning-check完成；1861 passed、7 skipped，pytest耗时235.18秒 |
| 跳过项 | 2项依赖未挂载的生产专家产物，5项依赖未配置的PostgreSQL测试DSN；不声称已执行 |
| 聚焦Ruff | PASS：本次涉及的runner、Trend/Order源码及6个聚焦测试文件，共9个文件 |
| 全仓Ruff | FAIL：153项，涉及15个文件；E501=146、I001=1、UP035=4、E741=1、F401=1。逐一核对15个文件和pyproject.toml的Git blob均与HEAD完全一致，属于当前基线问题，本轮不扩范围修复 |
| git diff --check与落盘读回 | PASS；已读回本报告，正文中的合成复现代码重新执行通过，17个本地证据链接均存在 |

聚焦命令：

```text
PYTHONPATH=src /home/ubuntu/GitHub/Quanthus/repo/.venv/bin/python -m pytest -q \
  tests/unit/test_stage_order_proposition_development.py \
  tests/unit/test_stage_order_runtime_cost.py \
  tests/unit/test_structure_order_facts.py \
  tests/unit/test_structure_trend_evolution.py \
  tests/unit/test_stage_order_proposition_issue_plan.py \
  tests/contract/test_document_traceability.py
```

测试通过证明已测试工程行为，不证明金融有效性、独立业务语义一致性或新的研究资格。本轮只新增审计记录及入口导航，不修复业务源码、配置、阈值或旧验收。

## 7. 合成价格路径复现方法

这是固定的两个反例构造，不是对真实行情重采样或参数选择。下列主体使用仓库既有合成row/policy夹具和生产函数；两个构造唯一差别是最近20个会话内前后10个会话的高低范围交换。

```python
import json
from decimal import Decimal as D
from pathlib import Path

from tests.unit.test_structure_v2_inputs import policy, row
from quanthus.structure_v2_inputs import admit_daily
from quanthus.stage_learning.order_proposition_development import (
    build_order_observations, _bucket, _review_one,
    _predict_stage2, _predict_stage0,
)

cfg = json.loads(Path("config/stage-learning/order-proposition-development.v1.json").read_text())
panel = json.loads(Path("config/stage-learning/order-proposition-synthetic-panel.v1.json").read_text())
views = []
for recent_contraction in (True, False):
    rows = []
    for i in range(80):
        if i < 60:
            high, low = D(108), D(92)
        elif (i < 70) == recent_contraction:
            high, low = D(106), D(94)
        else:
            high, low = D(104), D(96)
        rows.append(row(i, str(99 if i % 2 == 0 else 101), high=high, low=low))
    admissions = tuple(admit_daily(item, policy()) for item in rows)
    assert all(item.admitted for item in admissions)
    observations, _ = build_order_observations(
        admissions, report_start=rows[-1].market_date,
        report_end=rows[-1].market_date,
    )
    assert len(observations) == 1
    obs = observations[0]
    views.append(tuple(obs.feature_dict()[k] for k in (
        "efficiency20_abs", "range_width20", "width_change20", "center_shift20_abs",
    )))
    expected = "stage_2" if recent_contraction else "stage_0"
    assert {_review_one(obs, p) for p in panel["policies"].values()} == {expected}
    assert _bucket(obs, cfg["reference_sampling"]["sampling_bands"]) == "order_like"
    assert _predict_stage2(obs, cfg["r0"]["stage_2"]) is True
    assert _predict_stage0(obs, cfg["r0"]["stage_0"]) is False
assert views[0] == views[1]
```

## 8. 来源导航

- [原实验设计](../design/stage-0-2-order-proposition.md)：观察命题、10/20区别、搜索、停机规则与关闭边界。
- [原验收](../acceptance/stage-0-2-order-proposition-result-20260910.md)：运行身份、计数、验证上限；本轮不升级其证据等级。
- [交付合同](../../config/stage-order-proposition-delivery.v1.json)：execution accepted不等于candidate accepted，所有后续权限保持关闭。
- [开发配置](../../config/stage-learning/order-proposition-development.v1.json)：sampling、候选、hard gates与summary-only发布合同。
- [参考配置](../../config/stage-learning/order-proposition-synthetic-panel.v1.json)：实际参考阈值与来源。
- [runner](../../src/quanthus/stage_learning/order_proposition_development.py)：`_bucket`401行、`_review_one`474行、`resolve_references`517行、`_gate`626行、summary934行起、payloads1002行、标准清单1043行；行号绑定本审计基线。
- [Order Facts](../../src/quanthus/structure_order_facts.py)：相邻非重叠10/20窗口与正式计算。
- [Trend Facts](../../src/quanthus/structure_trend_evolution.py)：方向效率与正式计算。
- [资格提案](../design/stock-stage-qualification.md)：Q1–Q4、尺度、不确定性、独立盲审及连续Facts基线；仍需遵守其draft/blocked边界。
- [文档权威规则](../design/source-precedence.md)：审计报告不自行修改业务设计或执行权限。

## 9. 第二轮四角色模拟复审：业务路线收敛

### 9.1 来源、授权与本轮边界

用户明确允许再次尝试独立评审，或由本助手模拟量化、短线、基金、数仓四角色重新评审。本轮于 `2026-09-10T12:59:50.614072+00:00` 开始一次只读可用性探针，仍返回 HTTP 401、退出码1、ready=false。未生成独立模型意见，未更改认证、身份或服务。本轮不再反复尝试或将认证修复作为业务评审前置条件。

**本轮状态：`ROUND2_COMPLETED_SELF_SIMULATED`。** 四个角色是同一助手的四组职责和反方质询，不是四个独立智能体、真人专家、相互隐藏的判断或四票统计证据。按用户选择，这足以完成本轮设计方向讨论；不因401继续将本轮讨论标为未完成。独立语义验证、真实样本资格、收益或交易许可仍没有由此获得。

开始时HEAD及远端main均为 `72b83e59b4763e03404d7598657a72b7472e9a64`，工作区已有上一轮的两份文档改动。本轮继续同一报告，不重做原实验，不新开平行权威文件；业务源码、配置与测试保持不变。第6节是首轮验证记录，第二轮检查单列于9.8。

### 9.2 四角色第一轮立场与相互否决

| 模拟角色 | 独立列出的职责与初始立场（并非独立模型运行） | 对上一轮方案的主要反对意见 | 交叉质询后的保留意见 |
| --- | --- | --- | --- |
| 量化 | 区分目标定义、参考质量、输入表示与性能；保留20日候选预测更丰富参考的统计研究可能性。 | 老师看更多信息不自动构成设计错误。两个碰撞样例只证明不能完整复现，不证明原性能门槛不可达，更不证明真实9个正例的根因就是10日条件。 | 接受把主尺度与近期变化分别声明；不允许把参考删到等于候选，以换取一致性。表示是否够用仍须实证。 |
| 短线 | 描述截至T可观察的结构，保留近期变化的时效性。 | “20日为主”若只因现成代码使用20日，就倒置了业务和实现；“近期扩张”也不天然等于一切结构无序。 | 同意首个研究口径采用20日结构背景+10日局部变化，但必须保留局部不稳定/反证，不能把任何短期破坏一律忽略。 |
| 基金 | 要求离散标签有明确研究用途，控制新增复杂度与重复试验。 | 为凑齐编号再补样、扩网格或增加多层治理，不是有效进展。401也不应成为设计讨论无限等待的理由。 | 保留0/2和连续Facts；先服务解释与研究分层，不作为整个专家链的强制入口。不直接转去1/12复制同一参考缺陷。 |
| 数仓 | 要求尺度、业务时点、来源和参考判断可复核；区分数据事实与操作记录。 | “20日”不是只读20根数据：现有20-vs-20比较至少涉及前后40个会话，还要继承正式builder的预热要求。原摘要包也不能被当作160例明细。 | 复用现有Fact计算和完整输入闭包；只补必要参考账本，不另造Facts族、永久ADM或DWD标签字段。 |

四角色分析不产生“4/4专家通过”的统计或独立性主张。下列处理是本助手综合上述职责后的建议。

### 9.3 关键分歧的处理与对首轮结论的修正

**分歧一：是否应当删10日条件？** 不删。已实现的10日Facts继续保留；要先明确它们是在描述近期条件、构成复合命题，还是提供足以反证主命题的证据。反证可以有决定权，但决定权来自预先定义的同一目标，不来自评审者临时偏好的窗口。

**分歧二：是否已经证明20日是正确主尺度？** 没有。20日只是与当前研究口径衔接、减少无关改动的首个profile选择，不是持仓期限，不是最优交易周期，也不是由合成反例推出的市场真理。其他尺度不凭类比自动获得资格。

**分歧三：参考是否必须与候选只看同一批指标？** 不必。方法独立与信息删减不是同一件事。参考可以看同一截止时点、同一价格坐标的更完整路径，但必须评价同一业务问题，并保存理由。候选缺失关键证据时应记录表示局限；不能规定参考只能照着候选四个数打分来掩盖局限。

**分歧四：稳定区间但不收缩，算不算Stage 2？** 本轮选择不借参考短缺扩大原Stage-2覆盖。旧实验的Stage 2是需要收缩的窄命题；未来首个profile建议显式写为“收敛型有序整理”。稳定不收缩的区间可以保留连续描述，但不为凑正例自动改判为2，也不能因不是2就兜底为0。将更广义区间整理纳入2，需要另有定义和证据，不在本轮偷改。

**分歧五：能否直接让Stage 0单独过关？** 不能。69个正例只满足一个数量条件，不能代替完整对照或性能证据；旧成对实验的共同门槛不变。后继可分别描述单命题质量与同尺度冲突，但不是回溯放行旧候选。

**分歧六：没有独立模型，是否又必须停在评审？** 不必。用户已经选择允许模拟评审，本轮设计讨论可以完成并形成明确建议。仅将它如实标为单模型设计评审，不替代未来逐案例验证；不制造新的“必须独立模型恢复后才能写设计”门槛。

### 9.4 本轮收敛的业务边界建议

**推荐路线：给定尺度的结构背景+近期局部变化；0/2保持正面证据命题，允许都不成立。** 首个后继设计以20日背景和10日局部条件为范围，保留各自的证据，不强压为一个全局股票阶段。它是待落实的后继设计建议，不覆盖当前已关闭的v1规则或输出schema。

| 观察情形（业务示例，不是新增金标准标签） | 后继设计应表达什么 | 明确不做什么 |
| --- | --- | --- |
| 20日呈收敛整理，10日也收缩稳定 | 可以同时表达主尺度整理证据和近期收缩证据；是否确认各命题仍由版本化规则与参考验证决定。 | 不推断吸筹、未来向上突破或买点。 |
| 20日仍符合整体整理描述，10日开始扩张 | 保留主尺度描述，并显式标出近期扩张/不稳定；如果它已满足主尺度反证，再否定主尺度命题。 | 不因任何10日扩张就机械把全局从2翻成0，也不因主尺度稳定就隐藏近期风险。 |
| 区间与中心稳定，但没有收缩 | 描述为稳定区间、没有收敛证据；窄的0/2命题可以都不确认。 | 不为了补满20个正例将它自动算2；不把未覆盖当0。 |
| 同一声明尺度上有显著扩张或中心漂移，同时满足低方向效率等正面条件 | 可以形成该尺度的结构不稳定候选，保留导致判断的证据与反例。 | 不把清晰的单向趋势、其他标签未成立或模型低置信度统称无序。 |
| 数据有效但证据不足、或同尺度结论冲突 | 保留未知/歧义/冲突及具体原因；冲突不得通过多数票隐藏。 | 不强制top-1，不把支持分数当经校准概率。 |
| 必需数据缺失、坐标/历史不满足准入 | 标明输入不可用，并保留仍有效的其他Facts。 | 不产生“无序”市场判断，更不以缺数据自动要求卖出。 |

因此，本轮不是“挑10日还是20日谁对”，而是**明确各自在描述什么，以及什么证据能反证哪个命题**。若以后业务确实需要双尺度同步条件，应以明确的组合条件表达，并让候选具有相应输入，而不是继续把复合标签当成仅凭20日摘要必然可判。

### 9.5 最小参考与验证协议建议

本节把后继设计应落实的内容具体化，不授予新数据读取、标注或参数搜索权限。

**证据与提问。** 每个案例绑定证券/上市身份、截至T时点、观察profile、价格坐标、Fact版本及完整来源引用。所有角色先分别描述主尺度背景、近期变化、支持证据、反证和未知项，再按同一业务定义给出参考结论。隐藏候选输出、候选阈值和T后价格；不能让评审看到本轮算法是否会通过再改标签。参考看完整路径不意味着允许读取未来。

**实际来源。** 确定性policy只记规则参考；本模型多角色判断只记模拟参考；实际独立会话或真人意见分别按真实来源记录。同一模型的四种口吻、四次投票或四个角色名不能成为置信度或独立性证明。本轮四角色讨论是在评审设计，并没有给真实市场案例完成新的标注。

**分歧处理。** 原始意见先留存，再质询。若分歧来自不同尺度，分槽表达；若来自同一尺度的合理不确定，保留歧义；若来自定义矛盾，修后继定义而非强造一致标签。模拟讨论无法验证的业务问题保持假设，不写成市场事实。

**采样与门槛。** 先检验参考是否可用，再启动候选选择。代表性样本与稀有/边界样本分开报告；人为富集正例后的总体precision不能冒称市场总体precision。保留样本、证券、日期/episode的分母，一证券一例不自动代表独立市场事件。旧9<20结论和原全部hard gates不变；不简单把补到20当作合格，也不循环补到通过。

**最小留痕。** 保留context身份、证据引用及可访问的源快照、profile与知识截止、评审来源/版本、逐例原始判断、理由/反证/未知项、裁决与修订。候选评分、未来结算和实际运行时间分开记录。保留引用必须能解析到仍受保留策略保护的证据，不能只留一个无法解引用的哈希。不存在的旧明细不能通过静默重建来补成历史真值。

**成本和停止。** 先复用已接受Facts和已存在且获准读取的证据；必要重建属于新计算，必须提前声明而非称“读回”。未来如需真实参考试点，先限定目标、样本范围、计算/评审预算和停止条件。参考仍不足就给出原因；同一目标仍不能区分就保留连续描述，不扩大状态表或无限搜索。这里没有冻结新的样本数、阈值或访问权限。

### 9.6 本轮新增的固定合成检查

为检查“稳定区间是否已被原规则当作2”，构造80会话的固定HLC路径：每个会话high=106、low=94，close在99与101之间交替；其他字段采用现有unit-test夹具。全部80条通过准入，经正式Fact builder得到末日一条观察：方向效率=0，20日宽度=0.12，10/20宽度变化均0，10/20中心移动均0。

实际执行结果：`order_like`桶；原R0的Stage 0=false、Stage 2=false；三套参考policy均为`ambiguous`。这是原窄收缩语义的函数行为证据，不是证明该路径在交易业务上一定应标为2或0。本轮没有把该样例加入真实参考集，也未修改任何规则。

固定复现主体如下；本轮检查不进行真实行情采样或参数选择：

```python
import json
from decimal import Decimal as D
from pathlib import Path
from tests.unit.test_structure_v2_inputs import policy, row
from quanthus.structure_v2_inputs import admit_daily
from quanthus.stage_learning.order_proposition_development import (
    build_order_observations, _bucket, _review_one, _predict_stage0, _predict_stage2,
)
cfg = json.loads(Path("config/stage-learning/order-proposition-development.v1.json").read_text())
panel = json.loads(Path("config/stage-learning/order-proposition-synthetic-panel.v1.json").read_text())
rows = [row(i, str(99 if i % 2 == 0 else 101), high=D(106), low=D(94)) for i in range(80)]
admissions = tuple(admit_daily(item, policy()) for item in rows)
assert all(item.admitted for item in admissions)
observations, _ = build_order_observations(
    admissions, report_start=rows[-1].market_date, report_end=rows[-1].market_date,
)
assert len(observations) == 1
obs = observations[0]
assert _bucket(obs, cfg["reference_sampling"]["sampling_bands"]) == "order_like"
assert not _predict_stage0(obs, cfg["r0"]["stage_0"])
assert not _predict_stage2(obs, cfg["r0"]["stage_2"])
assert {_review_one(obs, p) for p in panel["policies"].values()} == {"ambiguous"}
```

### 9.7 明确的处置，而不是再让用户选择技术参数

**设计讨论处置：支持上述限定用途、分尺度的后继设计方向；完成本轮模拟复审，不等待401恢复，也不再追加一轮同内容的预审。** 原实验继续保留参考不足结论；不直接补样，不放弃0/2，不回滚连续Facts，不强求所有编号完整。

业务用途收敛为结构解释与研究分层，优先落实20日背景/10日局部条件及可追溯参考机制。20日的选择是第一个可执行profile的工程与范围决策，不宣称为市场最优。具体阈值和性能必须在后继方案冻结后用证据检验，不让用户按直觉选择。

这关闭的是用户要求的本轮设计评审讨论，不是给v1补发通过证书，也不是正式批准新实验。剩余是把已收敛方向落实为一个后继版本的可执行契约与有限验证任务；没有理由继续只因缺少独立模型而卡在同一轮设计讨论。

正式标签、主阶段仲裁、日更、专家生产消费、策略与交易权限不变。2025不动，2022–2024 arbitration不重读，已看的2020–2021材料不能冒充新的盲测。本轮没有新行情读取、远端160例复审或Stage Worker提交。

### 9.8 第二轮验证记录

- Provider probe：HTTP401、退出1；只尝试一次入口，无独立评审意见。
- 固定稳定区间合成路径：80/80准入、1条末日观察；结果与9.6一致。
- 本轮 `make check PYTHON=/home/ubuntu/GitHub/Quanthus/repo/.venv/bin/python`：doctor、compile、pytest、planning-check全部完成；1861 passed、7 skipped，pytest耗时239.84秒。跳过项仍是2项生产专家产物未挂载及5项PostgreSQL测试DSN未配置，不声称已运行。
- 本轮 `git diff --check`：PASS；`git diff --name-only -- src config tests`为空；最终变更范围仍为本审计报告及docs/README.md，不含源码、配置或测试。
- 第6节的79项聚焦测试和全仓Ruff153项基线问题是首轮记录，不冒称本轮重新独立执行的结果；本轮全仓pytest已经覆盖相应测试。
- 尝试从Markdown提取并执行两段复现主体的附加检查被工具安全检查拦截，未获得执行结果，未重试该执行方式。本轮9.6固定探针此前已通过显式命令独立运行；不将被拦截的附加检查记为PASS。
- 文档已实际写入，最终读回与Git状态另有工具回执；尚未提交或推送。

