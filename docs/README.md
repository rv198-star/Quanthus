# Documentation Map

This file is the level-zero router for people and AI sessions. It names the current delivery
branch and the smallest context set for common tasks; detailed rules and design stay in child
nodes.

| Field | Value |
| --- | --- |
| Current phase | P6 implementation open; P5 optional research deferred |
| Gate source | `config/p3-gates.v1.json` |

## Current branch

- P1 is accepted with the versioned C1 fail-closed condition.
- P2 is conditionally accepted for historical and paper research under the frozen
  `close-to-next-open-v1` profile. Its matcher, unit accounting, audit persistence, decision and
  shadow records, diagnostic replay, P1 execution-state adapter, and golden round trip pass.
- P3 rule-only historical capability acceptance is closed under `config/p3-gates.v1.json`; the P4
  event-library/outcome/report slice is accepted through deterministic production A/B evidence under
  the same accepted daily profile.
- The strategy-neutral daily replay substrate is accepted under `P3-REPLAY-001`, including strict
  typed P1-frame adaptation, carried-sell company actions, immutable replay packaging, the lazy
  continuous PIT frame reader, full 2012-2025 input audit, and a real-corpus round-trip smoke.
- The research-only `P3-FEAT-002`/`P3-STRAT-001` increment adds a vectorized PIT daily-indicator
  panel and a three-strategy one-unit baseline runner. Moderated catalogue v2 is the CLI default;
  v1 remains a replayable but rejected extreme-control record. The command is locked to 2012-2021
  development data and does not promote a target P3 policy or open arbitration/blind partitions.
  Its report now requires an offline checksum-bound `sh000300` CSI 300 curve and includes benchmark
  excess, time in market, average invested capital, and top-three closed-trade robustness metrics.
  Runner 2.2 retains that full-capital opportunity-cost comparison and adds a prior-close-weighted
  capital-matched diagnostic for selection/timing analysis.
- Targeted backtest sanity diagnostics passed a same-pipeline single-security buy-and-hold sentinel,
  reproduced v1 near-total losses with every cost set to zero, and reconciled daily-return extremes
  to raw bars. This narrows the v1 failure to strategy construction without making the sentinel a
  market benchmark or proving every engine path.
- The mixed-unit corporate-action normalization contract and a real-corpus audit are implemented
  under `P1-DATA-004`. Exact implementation-plan authority resolves the former factor/unit
  ambiguity, and all 272 retained rows now have a versioned per-security fail-closed disposition.
  Snapshot `p1-historical-replay-20260729-v1` publishes the 35,157 normalized actions, that ledger,
  the authoritative calendar, and accepted daily execution inputs together. The completeness,
  checksum, frame, and exception gates accept it for research replay without granting execution
  authority.
- The additive 2026 research backfill uses snapshot
  `p1-historical-replay-2026-through-20260727-v1`, corporate-action policy v3, and research-bundle
  policy v2. It covers decisions through 2026-07-24 with next-open execution context on 2026-07-27,
  remains readable only as `development_research`, and does not rewrite the original three bundles
  or any mutable pointer. Formal publication and 134-frame replay evidence are accepted in
  `acceptance/issue-45-2026-research-ads-backfill-20260730.md`.
- Its v3 successor uses snapshot `p1-historical-replay-2026-through-20260730-v3`, the mixed-source
  CSI 300 tail, and the versioned fail-closed ST missing-source rule. Formal bundle
  `strategy-replay-recent-research-7c99724-v3` contains 137 decision frames through 2026-07-29 and
  T+1 context through 2026-07-30; publication and independent audit evidence are accepted in
  `acceptance/issue-45-2026-research-ads-refresh-20260730.md`.
- The frozen P3 capability fixture has a versioned card, concrete feature/label/expert/
  action chain, strict static-industry input, five fixed-capital-unit replay, P2-backed fills and
  settlement, four-source exits, carried orders, cooldown, benchmark metrics, and populated
  opportunity-funnel production. Its first frozen 2012-2021 development run passed all three
  strategy gates and the drawdown warning at 230 closed trades, 31.91% total return, 22.49%
  capital-matched excess, 19.11% return after removing the top three closed-trade contributions,
  and 4.75% maximum drawdown. An independent full invocation reproduced the feature panel,
  result, and scoreboard byte-identically, closing the P3 capability acceptance. It is not the
  first member of a formal strategy family, is ineligible for tuning or promotion, remains
  research-only, and has -83.01% full-capital excess versus CSI 300.
- The Expert Strategy Layer design is approved as a bounded post-P3 product increment. Trusted
  DWD/P2 remains the factual referee; expert assessments and policy mappings are independently
  versioned and pass separate assessment-quality and complete-strategy economic-value gates. The
  additive v2 protocol must preserve every accepted v1 Run. The implementation-readiness review
  freezes the first formal topology at exactly two active strategy experts, assigns the second
  expert and executable topology to `P3-STRAT-002`, and makes `P4-LABEL-001` target-card reuse a
  predecessor of the first target/resolver. Its result-bearing DWD evaluation also waits for
  `P3-EXPERT-002`. The additive v2 protocol and deterministic PostgreSQL Worker pipeline are
  accepted under `P3-EXPERT-001` and `P3-EXPERT-002`. `P4-LABEL-001` is now accepted and
  `P3-EXPERT-003` is accepted on a production 2020-2021 Worker Run and byte-identical independent
  reproduction. `P3-STRAT-002` is accepted under
  `acceptance/p3-strat-first-formal-topology-production-20260822.md`: its exact approved two-expert
  topology completed a production January 2020 Worker Run and an isolated PostgreSQL/Run-root
  reproduction with all seven artifacts byte-identical. Additive lifecycle `1.1.0` and formal
  strategy `candidate.p4-trend-risk@1.0.1` are accepted under
  `acceptance/p3-pos-trial-reduction-compatibility-production-20260822.md`; the legacy lifecycle
  and card remain immutable. `P3-ATTR-001` is accepted after persisted outcome-blind
  preregistration, complete-P2 counterfactual execution, and byte-identical independent
  reproduction. `P3-EXPERT-004` then accepted the digest-bound read-only candidate-detail view,
  and `P3-EXPERT-005` closed the MVP with an `inconclusive` lifecycle decision and disabled
  forward-Shadow disposition. No sealed data, notification, promotion, or execution authority was
  opened.
- `P3-STRUCT-001` is accepted as a bounded Structure Intelligence technical MVP. Two independent
  real-DWD Event Worker Jobs reproduced the same 174-row thin Structure Research ADM, coarse state
  counts, decision counts, and five P2 execution probes/fills; a 10-case outcome-blind manual
  semantic review found no obvious T-close contradiction. This closes only the DWD -> ADM ->
  structure -> expert -> aggregator -> P2 engineering chain. Economic value, the draft stock-stage
  vocabulary, model training, Shadow, paper, and live authority remain unopened. Evidence:
  `acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md`.
- `P3-STRUCT-002` is accepted and closed after an owner-authorized same-revision A/B complete-P2
  reproduction. C0 reproduced the accepted native baseline; C1 continuous structure finished at
  6.433900% return / 6.168277% maximum drawdown and C2 coarse at 5.718376% / 7.360706%. The primary
  C2-C1 comparison was material because C2 drawdown was 1.192429 percentage points higher while
  return was 0.715524 percentage points lower. The accepted bounded disposition is
  `prefer_continuous_structure_facts`; bootstrap intervals cross zero and no forward-value claim is
  made. Evidence: `acceptance/structure-intelligence-economic-final-acceptance-20260906.md`.
- `P3-STRUCT-003` is accepted and closed. Continuous Structure Facts v1 makes prior-20 anchors,
  signed margins, independent volume, and frozen breakout episode identity/progress first-class
  research facts. Episode creation is price-only and coarse states are explanation-only
  projections. Two independent Real-DWD Runs reproduced the exact 174-row fact package, consumer
  counts, renderer counts, and five P2 compatibility probes/fills; the preregistered 12-row
  outcome-blind review passed 12/12. No economic, production-expert, thirteen-state, model,
  Shadow, paper, or live authority is opened. Evidence:
  `acceptance/continuous-structure-facts-v1-real-dwd-acceptance-20260906.md`.
- `P3-STRUCT-004` is accepted and closed for the fixed B1-B5 development-retrospective Facts v2
  vertical. B1/B1-R own the semantic namespace and time/audit boundaries; B2 qualifies the bounded
  real-DWD derivative lane; B3/B4 add target-qualified 20/60 references and same-anchor
  cross/loss/reclaim relations; B5 closes the append-only process, direct consumer/audit and P2
  compatibility path. Result-bearing revision `723f22f` completed two independent cold Jobs with
  identical input-readiness, Structure, ADM and result identities: 174/174 admitted report rows,
  861 measurements, 9 interactions, 168 episode observations, 168 consumer/audit events, and 5/5
  P2 probes/fills. The preregistered outcome-blind sample passed 12/12. Physical audit/runtime hashes
  differ by design because actual generation/use clocks are not Fact identity. Earlier resource and
  canonical-receipt failures remain retained pre-result evidence rather than hidden retries.
  Acceptance: `acceptance/structure-facts-v2-b1-b5-final-acceptance-20260907.md`.
  This does not close B6/B7, parallel disclosure/fundamental/market domains, a formal Structure
  Expert/economic gate, thirteen-state/ML work, or Shadow/paper/live execution. Catalog work #215
  is a later independent read-only discovery surface and does not retroactively expand this scope.
  Route: `design/structure-facts-v2.md`; coverage: `config/structure-facts-v2-blueprint.v1.json`.
- `P3-STRUCT-005` is accepted and closed for the owner-approved Route-A **演化支撑事实族 / Support Evolution Fact Family** follow-up (`F9` is retained only as the historical planning code). It does **not** reopen Stage tuning. The fact family is a narrow B3/B4/B5 extension: support identity/binding, same-support break/reclaim events, unreclaimed process state, signed support distance and post-break rebound are different dimensions inside one family rather than peer labels. Stage 10/11 remain research-only projections, with Stage
  11 consuming an immutable prior Stage-10 confirmation rather than recomputing current Stage-10
  depth. Revision `44571610` completed two independent cold real-DWD Jobs with exact semantic
  reproduction: 174/174 admitted rows, 6 support bindings, 3 breaks, 3 reclaims, 25 unreclaimed
  rebound observations, 2 Stage-10 confirmation refs and 6 Stage-11 active research observations.
  The preregistered 12-row outcome-blind review found no obvious contradiction, including a real
  Stage-11 case whose current support distance had recovered inside 2% while its prior Stage-10 ref
  remained valid. B6/B7, official Stage labels, economic tuning, sealed data and trading stay
  closed. Acceptance: `acceptance/structure-f9-real-dwd-acceptance-20260910.md`; route:
  `design/structure-support-facts.md`; plan: `config/structure-f9-delivery.v1.json`.
- `P3-STAGE-002` is accepted and closed as the formal development Stage-10/11 Projection Contract
  over the accepted **演化支撑事实族 / Support Evolution Fact Family**. Stage 10 is a persistent
  structural-failure context proposition and Stage 11 is a nested rebound proposition. Candidate set
  `{10,11}` is intentionally legal at the projection/evidence layer while `primary_stage` remains
  null. The one qualification Job `job_5e2d82adb2a8492488725fcda379424f` reread no market data: it
  consumed only the two accepted immutable F9 cold artifacts, reproduced 241 projection observations,
  2 Stage-10 confirmations, 10 Stage-10 contexts and 6 Stage-11 conditions, matched every retained
  research predicate and produced byte-identical A/B formal projection files. Active implementation
  and qualification authority is now closed. Primary-stage arbitration, external validation, official
  LabelCard, daily schedule, strategy and trading remain separate future scope. Acceptance:
  `acceptance/stage-10-11-projection-qualification-20260910.md`; route:
  `design/stage-10-11-projection.md`; plan: `config/stage-10-11-projection-delivery.v1.json`.
- `P3-STAGE-003` is accepted and closed after one frozen 512-security 2020-2021 development-only
  morphology profile (`stage_313d48cfc06948e0a7fc7a3bd77b2657`, 225,246 profiled observations).
  The post-run review returns `PASS_WITH_DIMENSIONAL_RECLASSIFICATION`: `0/2` remain candidate
  vocabulary but require a Structure Order Fact Family before any search; `1/12` are retained as a
  layered downside context + deceleration/stabilization condition and prioritize a Trend Evolution
  Fact Family; `8/9` remain traceable codes but are reclassified as high-side modifier hypotheses,
  with observable meanings `upside acceleration / extension` and `high-level price-volume divergence`.
  No active third-group profile/search/fact authority remains. Route:
  `design/stock-stage-third-group-profile.md`; result:
  `acceptance/stock-stage-third-group-profile-result-20260910.md`; plan:
  `config/stage-third-group-delivery.v1.json`.
- `P3-STRUCT-006` is accepted and closed as **趋势演化事实族 / Trend Evolution Fact Family**. It
  formalizes threshold-free 5/20/60 return, 5/20 directional efficiency and return velocity,
  5-vs-20 velocity delta, plus 60-session range position/high-low distance. Qualification Job
  `job_bb986ee743994981ab61267e70f6f7f7` reused only the two accepted immutable F9 source artifacts:
  174 complete observations produced 1,914 typed measurements, A/B fact payloads were identical,
  and `market_data_reads=0` / `dwd_provider_opens=0`. Positive velocity delta remains a mathematical
  fact rather than an unconditional acceleration label. All active implementation/qualification
  authority is closed; Stage 1/12 and 8/9 search remains separate. The next gated Structure route is
  **结构秩序事实族 / Structure Order Fact Family**. Acceptance:
  `acceptance/structure-trend-evolution-result-20260910.md`; route:
  `design/structure-trend-evolution-facts.md`; plan:
  `config/structure-trend-evolution-delivery.v1.json`.
- `P3-STRUCT-007` is accepted and closed as **结构秩序事实族 / Structure Order Fact Family**. It
  reuses accepted directional efficiency and formalizes midpoint-normalized 10/20/60 range width,
  immediately prior non-overlapping width change, and range-center shift. Qualification Job
  `job_87fe531cec73477fbcc92df65a43a137` reused only the same two accepted immutable F9 source
  artifacts: 174 complete observations produced 1,218 typed measurements, A/B fact payloads were
  identical, and `market_data_reads=0` / `dwd_provider_opens=0`. Range contraction or center
  stability remains geometry rather than Stage 2 or accumulation. All active implementation and
  qualification authority is closed. The third-group price-structure substrate is now sufficient
  for a separately gated Stage-0/2 proposition experiment. Acceptance:
  `acceptance/structure-order-result-20260910.md`; route: `design/structure-order-facts.md`; plan:
  `config/structure-order-delivery.v1.json`.
- `P3-STAGE-004` is closed as `INCONCLUSIVE_REFERENCE_SUFFICIENCY`, not accepted Stage-0/2
  candidates. After the original technical timeout, the one runtime-only retry
  `stage_87d166eb3f4a4f7f858f9b4f3e238462` succeeded in 660.726405 seconds without changing the
  frozen experiment or its 2700-second limit. Its terminal result reports 224,346 observations and
  160 reference contexts: Stage 0=69, Stage 2=9, ambiguous=65, neither=17. The frozen minimum of 20
  Stage-2 references is a shared gate for both searches; both selections and pair overlap are null.
  This proves reference insufficiency in the selected sample, not population-wide scarcity or
  inseparability. The full trial arrays were not separately read; no trial-specific accuracy is
  claimed. Continuous Facts and the 0/2 vocabulary remain unchanged. No further retry, supplement,
  search, primary-stage, official LabelCard, holdout, daily or strategy authority opens. Route:
  `design/stage-0-2-order-proposition.md`; plan: `config/stage-order-proposition-delivery.v1.json`;
  result: `acceptance/stage-0-2-order-proposition-result-20260910.md`; review and execution history:
  `reviews/stage-0-2-order-proposition-result-review-20260910.md`.
  The non-governing business-boundary re-audit is
  [STAGE-004-REAUDIT-20260910](reviews/stage-0-2-business-boundary-reaudit-20260910.md).
  It records synthetic admitted-path evidence and two rounds of single-assistant four-perspective
  review. The owner-authorized simulated second round is complete despite the independent-provider
  401; section 9 records its scoped design recommendation. No experiment or successor run is opened.
- `P3-STAGE-005` implements the owner-approved successor reference mechanism, not a rerun of 004.
  It separates 20-session primary propositions from 10-session recent conditions, retains T-only
  case evidence and raw opinions, and verifies sealed packages by independent reconstruction.
  Role/source declarations do not confer independent or human validation. No market sampling,
  search, official labels or data-access authority opens. Route:
  [scale-explicit reference protocol](design/stage-order-reference-protocol.md); plan:
  `config/stage-order-reference-delivery.v1.json`; engineering evidence:
  [reference mechanism acceptance](acceptance/stage-order-reference-engineering-20260910.md).
- `P3-STAGE-006` is accepted and closed for real-input reference collection feasibility. Two exact
  Jobs on revision `bf5d8b1` reused the accepted immutable F9 sources: 357 retained input rows,
  twelve date-selected cases and twenty-four same-origin simulated opinions. Cold readback binds
  the original source, case selection and raw opinions. References resolve to Stage 0=4, Stage 2=1,
  neither=3 and ambiguous=4, with one separate recent-condition disagreement. Collection passes
  and finite descriptive coverage is observed; independent semantic qualification remains absent.
  No candidate, third Job, search, DWD, sealed partition, daily or trading authority opens.
  Route: [reference feasibility](design/stage-order-reference-feasibility.md); machine plan:
  `config/stage-order-reference-feasibility-delivery.v1.json`; evidence:
  `acceptance/stage-order-reference-feasibility-20260911.md`.
- The stock-stage vocabulary remains a draft. Its new non-governing qualification and field-dependency
  proposals are `design/stock-stage-qualification.md` and `design/stock-stage-data-dependencies.md`;
  proposed work is `work-items/stock-stage-qualification-proposal.md`. These documents do not activate
  twelve-state labels, B6/B7 implementation, new result-bearing research, or daily/strategy Shadow.
  The concrete CPU-first historical-learning plan is `design/stock-stage-historical-learning.md`,
  with device assessment in `design/stock-stage-training-compute.md`, three-perspective review in
  `reviews/stock-stage-learning-review-20260908.md`, and issue-managed steps in
  `work-items/stock-stage-historical-learning.md` (P3-STAGE-001 A-J). A-F and I are closed. D/E v2
  completed the non-overlapping 120-context supplement and hard-gated 18-trial R1 development
  selection; `development-candidate.v1.json` freezes probe=1.0 / hold=2 / signed-distance=0 /
  activity=1.0 and retains the signed-distance non-identifiability claim ceiling. F then consumed
  its single preregistered read-only 2022-2024 arbitration budget in Job
  `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7`, but closed **INCONCLUSIVE** before panel review or scoring:
  the final `qualification_mature_held` stratum had only 58 additional unique episodes versus the
  frozen requirement of 80. No SHADOW/QUALIFIED artifact was published and that partition cannot be
  rerun as a fresh holdout. I freezes `qualification-protocol.v2.json`: the original targets remain
  target caps, realized scarcity is retained without cross-stratum backfill, and unchanged semantic
  hard gates own sufficiency. J is still blocked for real holdout execution; its development-only
  capacity census found 128 as the first size meeting all target caps and froze future cohort size=512.
  P3-STAGE-001 itself now has no ready child: H is mixed-closeout/blocked, G/J remain blocked. The
  third-group result step has now closed under `P3-STAGE-003`; no active Stage result/search authority
  remains. The historical 512-security
  `second-group-profile.v1.json` completed successfully on 2020-2021 development with
  3,425 accepted Stage-4 episode starts and 186,221 path observations. It supports a bounded 5/6
  development search frozen by `second-group-experiment.v1.json`. That contract now explicitly keeps
  5/6/7/10/11 as distinct stage identities. `second-group-repair-profile.v1.json` then completed
  successfully and separated 7 damage→repair, 10 support-role/persistence/depth, and 11 unreclaimed
  rebound. The result demotes the frequently crossed acceptance anchor to a local acceptance boundary
  and freezes the deeper initial-low support as the primary Stage-10 development candidate. The next
  contract `second-group-repair-experiment.v1.json` freezes sequential development learning 10→7→11:
  Stage 10 uses 3/5/8-session persistence × 2/4/6% depth, Stage 7 uses 12/16/20% damage × 25/40/50%
  T-only repair fraction, and Stage 11 later uses 3/5/8% unreclaimed rebound after one Stage-10
  candidate is frozen. The first `second-group-development.v1.json` Run completed successfully but
  returned `INCONCLUSIVE`: Stage 10 froze at 5 unreclaimed sessions + 2% depth, Stage 6/11 exposed
  reference-sufficiency shortfalls, and Stage 7 was genuinely inconclusive with sufficient references.
  The bounded `second-group-development-supplement.v1.json` repaired only Stage-6/11 references while
  keeping every hard gate and grid unchanged. It froze Stage 11 at 5% unreclaimed rebound but the
  Stage-5/6 joint grid still produced no eligible candidate; Stage 7 remained unchanged INCONCLUSIVE.
  `second-group-development-closeout.v1.json` therefore closes development tuning with a mixed
  disposition: Stage 10/11 development candidates frozen, Stage 5/6/7 preserved as independent
  identifiers but `INCONCLUSIVE_PARKED`, with continuous Facts retained as the required baseline.
  Quantitative, short-term-trading, and fund perspectives reviewed the result and returned
  `PASS_WITH_SCOPE_REDUCTION`; this is a single-assistant three-lens review, not human-expert signoff.
  No active second-group search, official label, holdout/future-result, daily, consumer, strategy or
  trading authority remains open; 2025 stays unopened/unselected. Evidence:
  `acceptance/stock-stage-learning-h-second-group-development-closeout-20260910.md` and
  `reviews/stock-stage-second-group-development-review-20260910.md`.
- `P3-DICT-001` is accepted and closed as the Quanthus Data & Indicator Catalog. Children #223-#227
  close the authority/schema, deterministic snapshot, GET-only Platform API, first-class `/catalog`
  Web surface and drift/maintenance gates. Its original audited production used checkout `d47abf8`,
  API `8d78073` and Web `d47abf8`; current productization is recorded under P3-DICT-002 below.
  That baseline's production-edge smoke confirmed
  `/catalog`, OpenAPI, readiness and authentication
  enforcement, while exact-image PostgreSQL smoke confirms successful authenticated Catalog access.
  That foundation projection contained 664 entries and 10 explicit gaps after the post-acceptance
  semantic audit; the current 748-entry successor is described under P3-DICT-003 below. It is rebuilt from an explicit
  source manifest and checked against a small digest/count snapshot manifest rather than maintained
  as a second formula authority. Catalog search annotations cannot create entries or equivalence.
  Design: `design/data-indicator-catalog.md`; acceptance:
  `acceptance/data-indicator-catalog-final-acceptance-20260907.md`; resolved deployment incident:
  `acceptance/data-indicator-catalog-production-deploy-blocker-20260907.md`; post-acceptance semantic
  audit: `acceptance/data-indicator-catalog-post-acceptance-audit-20260907.md`; final audited
  production closeout: `acceptance/data-indicator-catalog-post-acceptance-production-closeout-20260907.md`.
- `P3-DICT-002` / #232 and children #228–#231 are accepted after field-focus and node-identity
  requalification. Every lineage node now exposes a business label plus exact technical ID. Dataset-
  level source context connects source directly to the DWD target table instead of inserting generic
  normalization boxes; meaningful field/definition transformations still retain process nodes. The
  adjustment-factor table therefore visibly distinguishes `adjustment_factors_pit`,
  `adjustment_evidence_delta`, `factor_evidence_delta`, `adjustment_evidence`, and target
  `dwd_adjustment_factor`. Field proof remains explicit: 10/524 DWD fields are `field_proven`,
  514/524 are `table_context_only`, and none lack parent-table context. The simplified graph has 259
  registered processing edges; this reduction removes redundant boxes, not source evidence. Lineage
  canvases now use a fixed-height *infinite viewport* (520px desktop / 420px narrow) with an independent
  `{x,y,zoom}` world transform instead of DOM scroll offsets. Initial fit truly centers graph bounds;
  background drag is unclamped, wheel zoom is pointer-anchored, two-pointer pinch is supported, and
  floating controls plus a clickable/drag-pannable MiniMap provide standard map-style navigation.
  These controls are browser-only and do not change graph semantics. `table_context_only` fields now
  use one parent-table context canvas with a compact non-field-proof notice instead of an isolated
  field canvas plus a second context section; scope/limits/evidence are collapsed below the canvas.
  Production checkout/Web are `d4e7158`; API remains `0c671f9` because the latest release is Web-only.
  Health/ready and public auth smoke pass. Full repository gate: 1590 passed / 7 existing environment
  skips; Web 18 passed; desktop/mobile/anonymous Chromium 3 passed. Route:
  `design/data-indicator-catalog-productization.md`; latest closeout:
  `acceptance/catalog-lineage-single-canvas-production-closeout-20260907.md`.
- `P3-DICT-003` / #237 and children #233-#236 are accepted in repository and production. Catalog
  v1.3 projects 748 entries, including 4 governed Research ADM datasets and 76 ADM fields. Main Data
  Lineage contains 94 formal ODS/DWD/ADM assets and 80 asset relationships; all 33 DWD tables and all
  4 ADM datasets participate. Process/projection/Feature/Label/semantic/consumer identities are
  excluded from the main asset graph. Definition Dependency and Usage Dependency remain separate
  views. Data Lineage defaults to full registered ancestor/descendant closure with optional
  1/2/3/5-hop focus. Full repository gate is 1602 passed / 7 existing environment skips; Web is
  18/18 and desktop/mobile/anonymous Chromium is 3/3. The latest Web-only navigation refinement keeps
  graph position stable while switching nodes; production checkout/Web are `c25638a` while API remains
  `3aa4837`. Health/ready pass and anonymous Catalog API remains fail-closed at 401. The first `17d5329`
  deployment attempt failed the lean API image evidence lookup and was automatically rolled back;
  the final repair preserved the Platform image boundary and then deployed successfully. Design:
  `design/data-catalog-lineage-v2.md`; implementation audit:
  `acceptance/catalog-lineage-v2-implementation-audit-20260907.md`; production closeout:
  `acceptance/catalog-lineage-v2-production-closeout-20260907.md`; latest Web navigation closeout:
  `acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md`.
- P3 opportunity-funnel reconciliation remains non-executing by contract. The governed portfolio
  runner now supplies complete candidate, score/veto, entry, and held-review source observations;
  the funnel describes research evidence and cannot authorize an order.
- The reusable strategy-attribution and report-viewing substrate is accepted for both bounded
  synthetic plumbing and the first formal two-expert strategy. Strategy-owned adapters feed an
  outcome-blind immutable plan, a resumable Pipeline, deterministic kernel verification, atomic
  release publication, and a checksum-gated read-only Web workbench. Recurring dashboards, human
  label review, and any enabled forward Shadow remain separately proposal- and evidence-gated.
- The operator-assisted two-window profile remains blocked by intraday-input and matching-policy
  evidence. It and forward email transport are outside the initial P6 daily-profile scope.
- P6 is now one bounded prospective-validation program: freeze entry/budget, enforce the
  post-activation DWD boundary, freeze scorecards, add one composite Replay Worker cycle, maintain
  separate Shadow and fixed-unit paper lanes, project read-only evidence, activate explicitly, and
  wait for the real-calendar budget before review. Only `P6-GOV-001` is initially ready; P5 remains
  optional deferred research and is not a predecessor.
- ADR 0007 defines the current data operating path. CN01 and Hermes each run one 18:30
  `Asia/Shanghai` four-lane ODS controller, gate DWD on four verified READY releases, and emit
  node-specific success or failure notifications. OCI mirrors CN01's verified immutable DWD
  increment. The former epoch arbitration, paired DWD/DWS publication, old operational ADS, and
  release-set path are retained only as ADR 0005 migration evidence.
- `config/releases/v2-build202607.json` remains the build identity authority. The installed
  read-only authority is `ods-base-202607-v2` and immutable `dwd-base-202607-v2.1` through
  2026-07-31; the effective DWD view also applies the Base-bound
  `dwd-base-202607-v2.1-correction-20260813-001`. The DWD Base contains all 33
  ADR 0006 objects, 455,817,962 rows, and 14,447,400,032 payload bytes. Daily and weekly processing
  writes immutable increments or corrections and never mutates either Base.
- Issue #78 is closed by the independently audited R18 successor. The pre-rebuild findings,
  remediation scope, build checkpoints, five candidate audits, and final package identities remain
  immutable evidence under `docs/acceptance/`; they are no longer open delivery gates.
- Rebuild, audit, and atomic promotion are production repository capabilities. R18 happened to run
  on GPU-P100, but script validity and package acceptance depend on exact inputs, hashes, tests,
  filesystem semantics, and bounded resources rather than a named node or approval phrase.
- Generic DWS is deferred until a concrete cross-domain consumer needs the retained ten-dimension,
  thirteen-fact constellation. P4 is not blocked by DWS: it consumes an exact DWD snapshot and
  produces a purpose-specific, manifest-bound `event_library` projection.
- Platform Kernel is an orthogonal engineering milestone under
  `config/platform-kernel-gates.v1.json`. PK1 Minimal Boot through PK4 Operator Web are accepted
  after real PostgreSQL/Gunicorn, production-image, desktop/mobile browser, tailnet HTTPS, and
  public Traefik-edge acceptance. Optional Authoring is explicitly not applicable to the first
  cutover. PK6 Cutover and Recovery is accepted after a self-verifying recovery package, isolated
  restore, deferred-cache restoration drill, production rollback/forward cycle, public verification,
  and old-path shutdown. This does not change the P4 domain gate. Quanthus retains
  sole authority for data, PIT, replay, strategy governance, decisions, and execution, and the
  first Web path remains a Quanthus-owned shell. The boundary freeze and PK1-PK3 acceptance are
  recorded in `acceptance/platform-kernel-pk0-boundary-freeze-20260812.md`,
  `acceptance/platform-kernel-pk1-pk3-acceptance-20260812.md`, and
  `acceptance/platform-kernel-pk4-operator-web-acceptance-20260812.md`; the later public edge is
  recorded separately in `acceptance/platform-kernel-public-edge-acceptance-20260812.md`.
- The candidate workbench now projects replay, evidence review, governance, and execution as four
  independent axes. All five current versions start replay-succeeded, pending review, governance
  not submitted, and execution not authorized. Review and governance events are digest-bound and
  append-only; governance admission cannot authorize execution. Acceptance is recorded in
  `acceptance/platform-candidate-approval-workflow-20260813.md`.
- ADR 0012 opens the post-PK4 lean Worker milestone for a personal strategy experiment platform.
  Web/API remain the control plane and candidate replay is the first independently executed
  PostgreSQL-queued Job. Production starts with one Replay Worker at concurrency one; registry,
  event stream, notification wakeups, and speculative Worker families remain deferred. Existing
  CN01/Hermes/OCI data schedules retain node-local `systemd`/CLI ownership. Redis, Celery, and
  RabbitMQ remain absent.
- The Lean V1 Replay Worker producer loop is accepted. Production runs one independently deployed
  PostgreSQL Worker at concurrency one; the successful five-candidate Job reads authoritative
  DWD/PIT directly, executes through P2, seals one immutable Run, atomically switches five active
  evidence digests, and resets each digest to pending review. Generation-fenced recovery and
  temporary two-Worker competition passed without double publication, and public desktop/mobile
  Run-history acceptance passed. No Redis, Celery, RabbitMQ, maintained per-strategy ADS, Worker
  registry, or warehouse schedule migration was added. Evidence is recorded in
  `acceptance/platform-worker-production-loop-20260813.md`; the earlier infrastructure-only state
  remains recorded in `acceptance/platform-worker-lean-v1-infrastructure-checkpoint-20260813.md`.
- `P4-EVENT-002` now supplies the first named second Worker kind allowed by ADR 0012: one static
  `event-worker`, `queue=event`, concurrency one, and `quanthus.p4-event-scan.v1` only. ADR 0013
  inserts a thin `P4 Research ADM` between DWD and Event Library so candidate projection, static
  concentration membership, and coverage evidence have one application-data boundary without
  expanding DWD or reviving ADS. ADM 1.1 resolves the 259 historical-only industry memberships via
  a checksum-bound fixed-date BaoStock/CSRC supplement while retaining DWD as the first authority.
  T+1 entry binding keeps the event's ADM snapshot identity separate from the original P1/P2
  execution-source snapshot rather than rewriting source provenance. Issue #74 is accepted after
  two real 2012-2021 Jobs reproduced 36,820 candidate identities, 36,022 executable and 798
  not-executable events, and identical Event Library content/manifest digests. `P4-LABEL-001` has
  human-approved exact outcome/structural card digests and accepted deterministic production A/B
  label-corpus evidence: 36,820 total events, 34,641 settled three-class events, identical
  Event/Label content and manifest digests across both runs. `P4-REPORT-001` is also accepted after
  the pre-result comparison-policy digest, deterministic report builder, Event Worker v3, explicit
  Event-Library-to-P3 identity bridge, and deterministic floating-point reduction order were proven
  in production. Final A/B Jobs reproduced 36,820 candidates and 34,641 settled three-class events,
  byte-identical Event/Label/Report content and manifest identities, and the same leading features:
  `risk.realized_volatility20`, `price.breakout20`, and `price.sma20_to_sma60`. Evidence is recorded
  in `acceptance/p4-event-comparison-report-production-ab-20260823.md`.
  `PERF-BATCH-001` is accepted separately after two independent production Jobs each built one
  Direct-DWD projection and two scope-bound TMP Feature Panels, reproduced all files in the three
  governed package trees byte identically, and improved matched recent production wall time by
  3.92 percent. Evidence is recorded in
  `acceptance/perf-batch-001-production-ab-20260823.md`.
  `PERF-EVENT-001` is accepted after same-revision cold A/B reproduced all three package trees byte
  identically and reduced average complete runtime from 7,434 to 2,059 seconds. The optimized scan
  remains single-process and exposes generation-fenced internal progress. Peak RSS remains about
  19.5 GiB and does not satisfy the desired 16 GiB ceiling. Evidence is recorded in
  `acceptance/perf-event-001-production-ab-20260823.md`.
  `PERF-SHARD-001` is accepted after deterministic 3 GiB shard execution, repeated production
  scaling at about 1.80x with two Workers, two bounded U16 resource runs, and byte-identical Event,
  Label, and Report reconciliation. Evidence is recorded in
  `acceptance/perf-shard-001-production-ab-20260825.md`.
  Arbitration and permanent-blind reads remain blocked.
- PK6 acceptance is recorded in `acceptance/platform-kernel-pk6-cutover-recovery-20260813.md`.
  Production runs from clean GitHub checkout `b9d459a` with commit-tagged API/Web/Replay images,
  retained PostgreSQL and candidate-Run volumes, and Traefik as the only product edge. The former
  Tailscale `:8443` Serve route and damaged repository deployment path are no longer required.
- The old Base, DWS, ADS, dual-lane, and portable research packages are historical evidence, not
  current transformation inputs or operating authority. ADR 0013 retires ADS as a current naming
  and application-data concept. Physical ADS packages are cleanup candidates once a separate audit
  proves no runtime, rollback, or exact-reproduction dependency remains.
- `design/stock-stage-state-machine.md` is a non-governing P3 draft and is not P2 context.

The current machine-readable release state is `config/p3-gates.v1.json`; P2's retained conditional
gate is `config/p2-gates.v1.json` and P1's retained gate is `config/p1-gates.v1.json`. Prose that
disagrees with any applicable
gate is a conformance defect rather than an alternate project state.

## Tree

| Branch | Authority and purpose | Child index |
| --- | --- | --- |
| `product/` | Product goals, requirements, non-goals, and release criteria | `product/prd.md` |
| `design/` | Approved current-state HLD, execution profiles, and module LLDs | `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md` |
| `design/strategies/` | Formal strategy proposals and frozen capability fixtures | `design/strategies/README.md` |
| `architecture/` | Accepted decisions and stable topic contracts | `architecture/README.md` |
| `references/` | Non-governing external research references and Quanthus applicability notes | `references/README.md` |
| `work-items/` | Stable ids joining design, code, tests, issues, and evidence | `work-items/index.md` |
| `operations/` | Deployment, recurring operation, incident, migration, and rollback | Runbook selected by the active component |
| `acceptance/` | Point-in-time acceptance and conformance evidence | Load only for the claim being audited |
| `hermes/` | Historical audit and handoff artifacts | Not part of default design context |

## Progressive load routes

| Task | Required context | Add only when needed |
| --- | --- | --- |
| P2 backtest implementation | root `AGENTS.md`, this map, `design/hld.md`, `design/execution-notification-profiles.md`, `design/lld/README.md`, `design/lld/02-backtest-engine.md` | LLD 01 for inputs, LLD 10 for logs, LLD 05 for boundary checks |
| P1/P2 data input | this map, HLD, LLD 01 | Linked architecture topic, ADR 0002/0003/0004/0005, relevant acceptance file |
| Product-bounded DWD modeling or coverage | this map, HLD, ADR 0006/0007, LLD 01, `design/lld/01-data-foundation/dwd-domain-model.md`, `design/lld/01-data-foundation/ods-dwd-data-dictionary.md`, and `design/lld/01-data-foundation/ods-to-dwd-lineage.md` | `config/releases/v2-build202607.json`, exact Base manifest, machine model contract, and focused tests |
| ODS or daily operations | this map, architecture index, ADR 0003/0004/0007, selected runbook | Exact config, service, and contract tests |
| Historical dual-lane or ADS evidence | root `AGENTS.md`, this map, ADR 0005 and the exact acceptance record | Treat packages as retained historical evidence, not current operating authority |
| Product or phase decision | PRD, HLD, LLD 09, work-item index | Governing gate, ADR, or acceptance evidence |
| Platform Kernel extraction or restoration | root `AGENTS.md`, this map, HLD, ADR 0010, LLD 11, `config/platform-kernel-gates.v1.json` | QuantDinger audit record, exact upstream source path, selected Web ownership/source evidence, and adapter contract |
| P3 state-model discussion | HLD, LLD 03, state-machine draft | Never load as P2 implementation authority |
| Structure semantics, time, names or delivery gaps | HLD, LLD 03, `design/structure-facts-v2.md`, ADR 0014 | Structure semantic/coverage JSON; Catalog only when discovery/compatibility is relevant |
| Data, indicator, Feature, Label or semantic-definition discovery | this map, LLD 03, `design/data-indicator-catalog.md`, `design/data-indicator-catalog-productization.md`, `work-items/catalog-productization.md` | Explicit Catalog source/snapshot manifests; LLD 01 when inspecting ODS/DWD authority, LLD 11 for API/Web transport |
| P3 strategy-semantics discussion | HLD, `design/p3-strategy-semantics-checklist.md` | Load LLD 03/04/05 only for the decision group being discussed; the checklist is non-governing |
| P3 rule-only implementation | root `AGENTS.md`, this map, HLD, LLD 03/04/05, accepted P2 interfaces | Exact P1 input node and P2 evidence for the path under test |
| P3 strategy historical replay | root `AGENTS.md`, this map, HLD, LLD 02 and its `strategy-replay.md` child, LLD 01 `strategy-replay-ads.md` | Exact strategy, authorized ADS bundle, and P2 execution evidence; load the legacy P1 snapshot only for frozen P3 reproduction |
| P3 governed strategy implementation | root `AGENTS.md`, this map, selected `design/strategies/` card, LLD 03/04/05, accepted P2 interfaces | LLD 10 for decision/funnel evidence; exact feature, label, policy, and industry-map inputs |
| Expert Strategy Layer design or MVP implementation | root `AGENTS.md`, this map, HLD, LLD 04, its `04-experts-aggregator/expert-strategy-layer.md` child, that child's implementation `README.md`, and the work-item index | Select only the active work-item child; add LLD 02/05 for exact replay/position interfaces, LLD 10's formal-strategy-attribution child for `P3-ATTR-001`, and LLD 11 only for the admitted Worker/platform port |
| Strategy attribution or report viewing | root `AGENTS.md`, this map, HLD, `architecture/strategy-attribution-publication.md`, LLD 10 and its `strategy-attribution.md` child | Exact adapter, method registry, Pipeline/publication code, Web contract tests, gate, and acceptance evidence for the claim under review |
| P4 event-library implementation | root `AGENTS.md`, this map, HLD, LLD 06, `config/p3-gates.v1.json` | ADR 0005 and LLD 01 only when producing/consuming offline research bundles; exact frozen candidate source, label cards, split policy, and P3 replay evidence |

## Authority and conflicts

`design/source-precedence.md` defines repository and external-source authority. Within the
repository, executable policy and approved design must agree. Contract-test success does not make
an undocumented behavior architectural truth, and prose does not override a frozen executable
contract.

## Change discipline

Every behavior-changing change identifies the requirement or work-item id, governing design node,
affected code/config paths, contract or golden tests, and acceptance evidence. Accepted ADRs update
current-state HLD/LLD text in the same change. Drafts, discussions, runbooks, and evidence files do
not silently promote themselves into the design baseline.
