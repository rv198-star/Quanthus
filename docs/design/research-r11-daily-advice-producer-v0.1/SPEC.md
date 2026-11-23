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
   `reason_codes` 只用既有契约码（如 `NEW_ENTRY_SIGNAL`，受阻用 `INPUT_NOT_READY`）；
   `explanations[].use` 限 `action|explanation`；`entry` 仅在 `entry_plan` 且必须
   输出 `invalidations`（P12 loader 直读该字段）。
7. 逐决策审计块记 `producer_version / policy_id@version / source_version /
   candidate_rule_digest / decision_cutoff / per_member_available_at /
   admission_disposition / empty_reason / git_revision`。
8. 物化按 `market_date` append-only：同日期重写拒绝（`idempotency_conflict`），扩展
   须递增 `package.version` 且保留旧 digest，对齐 P12 绑定规则。两种 digest 分开：
   `package_digests` 是文件内容 SHA256；`p12_package_digests` 经真实 P12 loader 转成
   `MarketDecisionSet` 后按 `dataclasses.asdict + default=str + sort_keys` 再
   `fact_contracts.digest`，与 P12 账户 `configuration_binding` 记录的身份一致。

## 文件

- `contracts.py` — `MemberEvidence / DailyFactsView / EntryPlan / Decision / AuditBlock /
  DecisionSet / DailyAdvicePackage / PublishReceipt`；schema `quanthus.r11.daily-advice.v1`。
- `admission.py` — `assess_members` 逐成员时钟准入（057 规则）。
- `producer.py` — `DailyAdviceProducer.produce(facts, account, policy, experts,
  candidates)` 返回 `ProducerResult{package, disposition, diagnostics, assessment}`。
- `publish.py` — `package_digests`、`publish_package`（写目录 + `PublishReceipt`）。

## 测试

`tests/unit/test_product_daily_recommendation.py`：entry_plan 可发布、必需成员超截止
fail-closed、可选成员只拒依赖 decision、空建议需完整评估回执、回执与候选不符
fail-closed、无专家/风险凭据的候选被阻断、同日期重写拒绝、`published_at < cutoff`
拒绝、真实 P12 `load_synthetic_fixture` 通过、P12 绑定 digest 与文件 digest 分离。

## 后继条件

真实日线接入按 057 首日 A 资格回执与主会话裁定另立包；真实 `FormalStrategyRunner`
适配、`decide_entry` 账户映射、P12 绑定注册、真实发布各自按其前置。原 U06/U07/U08
与本包无关。
