# R11 日次建议生产者 v0.1（提案，未采用）

| Field | Value |
| --- | --- |
| Status | proposal — engineering shape only; no real-use qualification granted |
| Work item | P11-R11-USE-002 / #700 |
| Base | `51ef9032bf5c754d8dcc4d8cc92743988bd9af15` |
| Scope | `src/quanthus/product_daily/recommendation/` + focused tests + this document + `config/research/r11/continuation-061/daily-advice-producer.v1.json` |

## 做什么

把「一个已签收的日线批次 + 注入的账户事实 + 注入的候选信号」组装成一个符合 P12
`decision_sets` 消费者契约的日次建议包，并产出逐成员时钟审计块与发布回执。

本模块**不做**：真实信号生产、`FormalStrategyRunner` 的完整真实适配、`decide_entry`
的账户事实映射、P12 账户 `configuration_binding` 注册、真实/公网发布、真实账户。这些
仍属用途资格或 P12/主会话决定事项。

## 关键规则（按主会话收窄）

1. `knowledge_cutoff` 与 `decision_cutoff` 由 `AdvicePolicy` 事前声明；不取
   `max(available_at)`。逐成员 `available_at` 必须有具体消费者可见时点且 ≤ cutoff；
   缺证或跨截止按 `member_clock_blocked` 处理，依赖它的 decision 单独受阻。
2. `DailyFactsView` 显式携带预冻结 `required_member_ids` 与批次签收凭据
   （`intake_receipt_id` / `intake_received_at` / `intake_verified_at`）；空必需集、
   必需成员缺席于批次、或成员被标 optional 但在必需集内，`__post_init__` 即拒绝。
   空 `members` 不能产「合格无建议」。这些字段是合成输入合同的一部分，不构成真实
   准入证明。
3. 必需成员/时钟缺证 → `INPUT_NOT_READY`，producer 返回受阻诊断，`package` 为
   `None`，不产会被 P12 当正式包的 ended/empty 文件。只有全部必需成员合格、候选生产
   者给出完整运行回执（`CandidateEvaluationReceipt` 且 qualified=0）且无注入候选时
   才产单条 `ended` no-op；缺回执或回执与候选数不符均 fail-closed，不伪作真实推荐。
4. `published_at` 是注入的真实发布瞬间；policy 校验其不早于 `decision_cutoff`，不
   回填历史时钟。
5. 注入 `CandidateSignal` 必须同时带 `expert_decision_digest` 与
   `risk_decision_digest` 才可成 `entry_plan`；缺任一凭据或缺 action-use explanation
   该 decision 记 `input_blocked`，不视为可发布。
6. 包字段与 P12 `MarketDecisionSet`/`MarketDecision`/`EntryIntent`/`Explanation`
   对齐：`stage` 限 `entry_plan|waiting|holding|exit_plan|input_blocked|ended`；
   `reason_codes` 只用既有契约码（entry 用 `NEW_ENTRY_SIGNAL`；`input_blocked` 只接受
   `STALE_MARKET_INPUT` 或 `INPUT_UNAVAILABLE`，与 P12 `validate_decision_set` 一致），
   `Decision.__post_init__` 对全部 stage 强制已注册 reason、entry 必带 reason、
   blocked 不得带 entry reason；`explanations[].use` 限 `action|explanation` 且
   code 必须在 `EXPLANATION_CODES` 注册表内、component_ref 非空，非法即在构造层拒绝；
   producer 侧亦在构造前捕获非法 explanation 并 fail-closed；`entry` 仅在
   `entry_plan` 且必须输出 `invalidations`（P12 loader 直读该字段）。任何包含 `input_blocked` decision
   的包不是可发布建议日，`ProducerResult.publishable` 为 `False`；构建期
   `ProductDailyError` 一律转为 fail-closed `ProducerResult(package=None)`。
7. 逐决策审计块记 `producer_version / policy_id@version / source_version /
   candidate_rule_digest / decision_cutoff / per_member_available_at /
   admission_disposition / empty_reason / git_revision / intake_receipt_id /
   evaluation_receipt`。
8. `CandidateEvaluationReceipt` 在空与非空候选两条路径都必须存在：
   `completed_at <= decision_cutoff`，`binding_digest` 绑定 `batch_id /
   intake_receipt_id / source_id@version / policy_id@version / candidate_rule_digest /
   decision_cutoff / 排序后的 candidate opportunity_id 集合`，不一致即 fail-closed；
   `qualified_count` 必须等于实际产出的 `entry_plan` 数；`evaluated_count` 必须 >0
   （零评估范围等于没跑，fail-closed）；`completed_at` 必须 `>= intake_verified_at`
   （评估必须看到已签收批次）且 `<= decision_cutoff`。`binding_digest` 除绑定批次/
   策略/截止/候选集合外还覆盖完整候选载荷（component、security_code、security_name、
   industry_id、fraction、mark/price、reason、explanations、invalidations、
   next_observation、依赖成员），任一字段被改都使 receipt 不符。
   `CandidateSignal` 的 `expert_decision_digest` / `risk_decision_digest` 必须等于按
   同一批次+策略+规则+opportunity_id 绑定的期望 digest，未知或不匹配即
   `input_blocked`。候选携带未注册 reason code 同样 `input_blocked`。
   这些校验是结构门，不授真实资格；真实上游适配仍属主会话/P11 决定事项。
9. `intake_received_at` 与 `intake_verified_at` 都必须 `<= decision_cutoff`
   （且 `verified >= received`，`__post_init__` 已保证）；迟于截止的签收使整批
   `INPUT_NOT_READY`。
10. 预校验 P12 边界（`DecisionSet.__post_init__`）：`knowledge_cutoff.date() ==
    market_date`；`published_at >= knowledge_cutoff`；`next_session > market_date`；
    空 `decisions` / 空 `decision_sets` 拒绝；`opportunity_id` 唯一；非 ended 决策
    `security_code` 唯一（producer 侧亦拒重复 live security）；entry 的
    `eligible_at > knowledge_cutoff` 且 `eligible_at`/`expires_at` 同日且都落在
    `next_session`；`expires_at > eligible_at` 非零窗口。同一 `DailyAdvicePackage`
    内重复 `market_date` 显式拒绝。`EntryPlan.__post_init__` 直接校验会话窗口：
    `eligible_at`/`expires_at` 先转 Asia/Shanghai 再校验 09:30–15:00 且同日
    （与 P12 `execution_window_issue` 一致），`AdvicePolicy` 对注入窗口做同等校验。
11. 物化按 `market_date` append-only：同 `package_id` 下任一已写文件若覆盖相同
    `market_date` 且内容不同，拒绝（`append_only_conflict`，对齐 P12 绑定规则——
    同日替换不是 append）；同 `(id, version)` 重写拒绝（`idempotency_conflict`）。
    `publish_package` 的 `published_at` 必须等于包内每个 `decision_set.published_at`；
    含任何 `input_blocked` decision 的包一律拒绝落盘（无论是否同时含 live decision）；
    `p12_loader` 提供的 binding digest 在写文件前完成全部校验，loader 失败目录不残留；
    同 lineage 已写文件损坏（JSON 不可读）fail-closed 不静默跳过。三种 digest 分开命名：`file_sha256` 是
    落盘完整 JSON 字节 SHA256；`decision_set_digests` 是各 decision_set 规范 JSON
    SHA256（旧 `package_digests` 保留为别名）；`p12_package_digests` 经真实 P12
    loader 转成 `MarketDecisionSet` 后按 `dataclasses.asdict + default=str +
    sort_keys` 再 `fact_contracts.digest`，与 P12 账户 `configuration_binding`
    记录的身份一致；`publish_package` 在提供 `p12_loader` 时把该 binding digest
    一并写入 `PublishReceipt`。

## 文件

- `contracts.py` — `MemberEvidence / DailyFactsView / EntryPlan / Decision / AuditBlock /
  DecisionSet / DailyAdvicePackage / PublishReceipt`；schema `quanthus.r11.daily-advice.v1`。
- `admission.py` — `assess_members` 逐成员时钟准入（057 规则）。
- `producer.py` — `DailyAdviceProducer.produce(facts, account, policy, experts,
  candidates, evaluation_receipt)` 返回 `ProducerResult{package, disposition,
  diagnostics, assessment}`；`publishable` 只在无 `input_blocked` 时为真。
- `publish.py` — `decision_set_digests` / `file_sha256` / `p12_package_digests` /
  `publish_package`（写目录 + `PublishReceipt`）。

## 测试

`tests/unit/test_product_daily_recommendation.py`：entry_plan 可发布、必需成员超截止
fail-closed、可选成员只拒依赖 decision 且 publishable=False、空建议需绑定回执、
回执 binding/completed_at/qualified_count 不符 fail-closed、intake verified/received
超截止 fail-closed、资格证据不符或未注册 reason 的候选被阻断为合法 `input_blocked`、
同日期不同内容重写拒绝、receipt 时间不符拒绝、包内重复 market_date 拒绝、真实 P12
`load_synthetic_fixture` 通过、P12 binding digest 真实消费 raw 且篡改后 digest 改变。

## 后继条件

真实日线接入按 057 首日 A 资格回执与主会话裁定另立包；真实 `FormalStrategyRunner`
适配、`decide_entry` 账户映射、P12 绑定注册、真实发布各自按其前置。原 U06/U07/U08
与本包无关。
