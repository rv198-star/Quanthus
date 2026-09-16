# Work-Item Traceability Index

Stable ids are the join key between requirements, designs, code, tests, and GitHub issues.
The GitHub issue title must begin with the id in brackets. Closing an issue requires links to
acceptance evidence; closing an epic requires all blocking child ids to close.

## Meta

| ID | Title | State |
| --- | --- | --- |
| META-DOC-001 | Maintain approved PRD/HLD/LLD baseline | active |
| META-TRACE-001 | Maintain requirement-to-issue traceability | active |
| META-GATE-001 | Automate release gate reporting | planned |
| META-DATA-PIPE-001 | Operate Hermes ODS releases and OCI T+5h publication | superseded by Base V2 node-local ODS-to-DWD path |
| META-DATA-PIPE-002 | Retire the legacy weekly monolith and split Hermes ODS schedules | maintenance observation; weekly DWD correction still open |
| META-DATA-PIPE-003 | Build dual-lane DWD/DWS candidates, OCI canonical release sets, and offline research bundles | superseded as current topology; evidence retained |
| META-DATA-BASE-001 | Build the Hermes-authoritative ODS July Base and checksum-identical CN01 mirror | accepted; renamed `ods-base-202607-v2` |
| META-DATA-BASE-002 | Build authoritative DWD/DWS July trial Base (Issue #48) | accepted historical trial slice |
| META-DATA-BASE-003 | Build OCI-authoritative operational ADS July Base (Issue #46) | accepted historical trial slice |
| META-DATA-DWD-002 | Build the product-bounded seven-domain DWD Base V2.1 | accepted Release; 33 DWD objects; daily maintenance active; correction-001 accepted; recurring weekly corrections open; generic DWS deferred |
| META-DATA-MAINT-001 | Reconcile post-V2.1 daily ODS/DWD operational hardening | active under Issue #143; post-2026-07-31 maintenance only; does not reopen Issue #78 or P4 gates |

ADR 0007 is the current topology authority. `config/releases/v2-build202607.json` binds immutable
`ods-base-202607-v2` and `dwd-base-202607-v2.1`. CN01 and Hermes run the same READY-gated ODS-to-DWD
daily path; OCI mirrors CN01's verified DWD increments. The Base packages are read-only. Weekly
DWD corrections remain maintenance work until separately accepted. `META-DATA-MAINT-001` owns the
post-V2.1 Daily recovery, synchronization, August promotion, provider-retry, and schedule-hardening
changes that accumulated after Issue #78's Base remediation scope was complete.

ADR 0005 and Issues #39, #45-#48 retain the old pull, arbitration, paired DWD/DWS, ADS, and
research-bundle acceptance history. Their artifacts are not deleted, but their incomplete future
gates no longer define current delivery. The final trial packages remain evidence of their own
bounded scopes and must not be confused with the product-bounded DWD Base V2.

`META-DATA-DWD-002` remains governed semantically by ADR 0006. The accepted DWD Base V2 contains
all 33 objects across seven business domains and binds the exact ODS Base V2 source identity. The
ten-dimension, thirteen-fact DWS design remains available but is deferred until a concrete
cross-domain consumer exists. P4 consumes DWD directly through a purpose-specific, manifest-bound
event-library projection.

## P1 data foundation

| ID | Title | Depends on | Disposition |
| --- | --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and dataset manifests | Bootstrap | |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 | |
| C2 | Classify every security-calendar gap | S4, security master | |
| S1 | Measure and complete delisted-security coverage | Security master | |
| S2 | Derive historical ST state from announcements | Landing data | |
| S3 | Derive board-specific price limits | S2, security master | |
| S4 | Build authoritative trading calendar | Source ingestion | |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars | |
| P1-SNAPSHOT-001 | Implement atomic point-in-time snapshots | Canonical datasets | |
| P1-ACCEPT-001 | Execute Chapter 01 acceptance suite | All P1 blockers | |
| P1-DATA-002 | Restore incomplete none histories detected by S3 replay | S3 coverage audit | |
| P1-DATA-003 | Restore truncated active none histories detected by daily coverage | P1-DATA-002 | |
| P1-DATA-004 | Normalize mixed-unit corporate actions for continuous historical replay | C1, S4, P1-SNAPSHOT-001 | closed by `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |

## P2 backtest and observability

All listed P2 work items are closed under the accepted daily-profile scope. The authoritative
status and retained target-profile activation blockers are in `config/p2-gates.v1.json` and
`docs/acceptance/p2-conformance-acceptance-20260728.md`.

| ID | Title | Depends on |
| --- | --- | --- |
| P2-ENG-001 | Define profile-aware event schemas and deterministic event loop | P1 acceptance |
| P2-POLICY-001 | Freeze fees, slippage, lot, matching, and execution-profile parameters | P1 acceptance |
| P2-MATCH-001 | Implement profile-aware conservative matching and tradability handling | P2-ENG-001 |
| P2-LEDGER-001 | Implement unit and external-pool ledger | P2-ENG-001 |
| P2-ORDER-001 | Implement cancel-buy and carry-sell lifecycle | P2-MATCH-001 |
| P2-AUDIT-001 | Implement run manifest and decision/notification audit persistence | P2-ENG-001 |
| P2-METRIC-001 | Implement fixed-unit performance reports | P2-LEDGER-001 |
| P2-OBS-001 | Define decision, shadow, and notification log schema | P2-ENG-001 |
| P2-HYP-001 | Implement diagnostic hypothetical ledger | P2-MATCH-001 |
| P2-ACCEPT-001 | Execute daily and synthetic two-window golden suite | All P2 components |

## P3 rule-only vertical slice

P3 is accepted under `config/p3-gates.v1.json`. The phase gate closes the reusable rule-only
historical capability slice and opens P4. The governed V1 is frozen as a composition/regression
fixture, not a formal strategy lineage. `P3-LABEL-004`, `P3-ATTR-001`, and `P3-DASH-001` are
explicitly deferred until a formal label or strategy proposal supplies their semantic inputs; they
do not authorize fixture tuning and do not block the P3 release criterion.

The deferred disposition does not mean the attribution/report stack is absent. Issues #50 through
#53 implemented and accepted the bounded synthetic infrastructure: adapter and method registry,
outcome-blind immutable planning, checkpointed local Pipeline execution, receipt-backed kernel
evaluation, atomic verified publication/catalog discovery, and checksum-gated read-only Web
rendering. That reusable capability is evidence for implementation readiness only. It produced no
formal strategy claim or recurring report, does not satisfy either resume condition in the gate,
and does not change the `deferred` rows below.

`P3-FEAT-001` and `P3-LABEL-001` have bounded accepted implementations in
`docs/acceptance/p3-feature-registry-slice-20260728.md` and
`docs/acceptance/p3-label-card-contract-slice-20260728.md`. They establish registry, PIT lineage,
LabelCard, and evaluator-boundary contracts only. Candidate-v1 concrete feature/label definitions
are implemented separately under `P3-LABEL-002`; broader production vocabularies are outside the
P3 fixture scope and require a later formal proposal.

`P3-LABEL-003` has a bounded accepted frequency/conflict audit in
`docs/acceptance/p3-label-frequency-conflict-audit-slice-20260728.md`. It audits registered results
and card-declared ranges/relationships only; it does not define or validate a concrete market
label. Candidate-v1's concrete labels and development evidence are recorded separately below.

`P3-EXP-001` has a bounded accepted typed-protocol implementation in
`docs/acceptance/p3-expert-protocol-slice-20260728.md`. It defines expert opinions, abstention, PIT
context, and evidence validation only; concrete experts are supplied only by a strategy or fixture,
while accepted governance aggregation is recorded separately below. The fixture experts close the bounded
`P3-EXP-002` implementation path without creating a general expert library.

`P3-AGG-001` has a bounded accepted governance implementation in
`docs/acceptance/p3-governance-aggregation-slice-20260728.md`. Fixed veto precedence and constraint
intersection are implemented. The fixture adds versioned scoring/action mapping under
`P3-AGG-002`; concrete shadow production waits for a genuine alternative proposal.

`P3-POS-001` has a bounded accepted lifecycle implementation in
`docs/acceptance/p3-position-lifecycle-slice-20260728.md`. It freezes only legal
empty/trial/dynamic/exiting/closed transitions reconciled to P2 `UnitState`. Candidate-v1 now
supplies the frozen five-unit sizing, review, cooldown, and P2 execution composition separately.

`P3-RISK-001` has a bounded accepted entry-capacity implementation in
`docs/acceptance/p3-concentration-capacity-slice-20260728.md`. It enforces the frozen P0 static
industry and daily new-unit caps. The follow-on accepted reservation-competition slice in
`docs/acceptance/p3-capital-allocation-slice-20260728.md` deterministically assigns already-governed
candidates to explicitly supplied empty units and counts same-batch reservations against both caps,
without sizing cash or creating execution artifacts. Candidate-v1's policy now supplies the five-
unit maximum and Trial sizing while the allocator itself remains non-executing.

`P3-EXIT-001` has a bounded accepted four-source arbitration implementation in
`docs/acceptance/p3-four-source-exit-arbitration-slice-20260728.md`. It combines already-evaluated
frozen exit sources and treats unavailable evidence as alerting hold, without defining evaluator
thresholds or creating an exit Signal/Order. Candidate-v1 now supplies the frozen evaluators and
routes an accepted exit request through P2.

`P3-SHADOW-001` has a bounded accepted comparison contract in
`docs/acceptance/p3-shadow-divergence-contract-slice-20260728.md`. It compares already-produced P2
`DecisionEvent` records without execution authority; concrete shadow decision production remains
open until the expert and score/action slices exist.

`P3-REPLAY-001` has a bounded accepted strategy-neutral daily replay in
`docs/acceptance/p3-strategy-replay-slice-20260728.md`. It executes consecutive PIT decisions
through P2, preserves one fixed-unit state, and reports deterministic multi-trade performance. It
now also has an accepted integration increment in
`docs/acceptance/p3-strategy-replay-integration-slice-20260728.md` for strict typed P1 adaptation,
carried-sell company actions, and immutable replay packaging. It does not define a concrete
strategy or enable add/reduce behavior. Candidate-v1 now completes the first multi-year governed
run on top of this substrate. The first
real-corpus bridge under `P1-DATA-004` now audits authoritative mixed-unit corporate actions,
versions every retained exception, and publishes the accepted calendar/action/ledger bundle in
`p1-historical-replay-20260729-v1`. The continuous reader, security/date exception enforcement,
full-range frame audit, and real-corpus round-trip smoke are accepted in
`docs/acceptance/p3-historical-frame-reader-slice-20260729.md`.
The accepted bundle gate in
`docs/acceptance/p3-historical-replay-bundle-gate-slice-20260728.md` prevents bypassing those inputs
or composing datasets across snapshots implicitly.

`P3-FUNNEL-001` has a bounded accepted reconciliation contract in
`docs/acceptance/p3-opportunity-funnel-slice-20260728.md`. It proves complete fixed-stage source
event accounting and outcome-link classification for already-produced evidence only. Candidate-v1
now supplies concrete decisions and a populated 2012-2021 funnel; constrained attribution remains
separate work.

`P3-FEAT-002` and `P3-STRAT-001` add the first concrete research controls without promoting a P3
production policy. The former materializes eight PIT daily indicators from unadjusted prices and
normalized actions; the latter runs moving-average trend, 20-session breakout, and oversold-
rebound rules through the accepted one-unit P2 replay. Both are restricted to the 2012-2021
development partition. The v1 acceptance record preserves the rejected full-capital/extreme
control; moderated v2 is the current CLI default and publishes a combined activity/performance
scoreboard. Evidence: `docs/acceptance/p3-baseline-strategy-suite-20260729.md` and
`docs/acceptance/p3-baseline-strategy-suite-v2-20260729.md`. The follow-up same-pipeline
buy-and-hold, zero-cost v1, and raw-extreme diagnostics are recorded in
`docs/acceptance/p3-backtest-sanity-diagnostics-20260729.md`. Runner 2.1 adds the checksum-bound
`sh000300` comparison, time-in-market, average-invested-capital, excess-return, and static top-three
robustness report without changing strategy behavior. Evidence:
`docs/acceptance/p3-baseline-benchmark-metrics-20260729.md`. Runner 2.2 additionally publishes a
prior-close-weighted, checksum-bound capital-matched benchmark while preserving full-capital CSI
300 as the opportunity-cost control. Evidence:
`docs/acceptance/p3-capital-matched-benchmark-20260729.md`.

The frozen governed capability fixture closes the bounded implementation paths for `P3-LABEL-002`,
`P3-EXP-002`, `P3-AGG-002`, `P3-POLICY-001`, `P3-POS-002`, and `P3-POS-003`. Its frozen initial
2012-2021 run passed all declared performance gates with 230 closed trades, and its 101,549-source-
event funnel plus five P2 ledgers reconcile. An independent full invocation reproduced the feature
panel, result, and scoreboard byte-identically. The bounded conformance review then accepted
`P3-ACCEPT-001` without opening later partitions or promoting the fixture. Evidence:
`docs/acceptance/p3-governed-strategy-development-20260729.md` and
`docs/acceptance/p3-conformance-acceptance-20260729.md`.

The approved Expert Strategy Layer MVP is a separate post-P3 product increment governed by
`docs/design/lld/04-experts-aggregator/expert-strategy-layer.md`. It does not reopen the accepted P3
fixture or alter the current P4 gate. The historical `p3-gates.v1.json` snapshot retains its
original deferred disposition; the post-P3 `P3-ATTR-001` resume is accepted separately by
`docs/acceptance/p3-attr-formal-production-20260823.md`.

The [stock-stage qualification work proposal](stock-stage-qualification-proposal.md) is a draft
review packet, not an active addition to the table below or an instruction to reopen the accepted
label fixture. Its internal proposal steps require separate authorization before implementation.

| ID | Title | Depends on | Disposition |
| --- | --- | --- | --- |
| P3-REPLAY-001 | Implement strategy-neutral daily historical replay | P2-ACCEPT-001 | closed |
| P3-FEAT-001 | Implement feature registry and lineage | P2 snapshots | closed |
| P3-FEAT-002 | Implement research baseline daily feature evaluator | P3-FEAT-001, P3-REPLAY-001 | closed |
| P3-STRAT-001 | Implement three one-unit research baseline strategies | P3-FEAT-002, P3-REPLAY-001 | closed |
| P3-LABEL-001 | Implement label-card schema and evaluator | P3-FEAT-001 | closed |
| P3-LABEL-002 | Implement vertical-slice labels | P3-LABEL-001 | closed for fixture scope |
| P3-LABEL-003 | Implement label frequency/conflict audit | P3-LABEL-001 | closed |
| P3-LABEL-004 | Build human label review corpus | P3-LABEL-002 | deferred pending formal label family |
| P3-STRUCT-001 | Prove DWD -> thin Structure Research ADM -> accepted trend semantics adapter -> aggregator -> P2 on a bounded development slice | P3-FEAT-002, P3-EXP-001, P3-AGG-002, P4-EVENT-002 | closed by `docs/acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md`; technical MVP only, economic value not claimed |
| P3-STRUCT-002 | Test continuous versus coarse Structure Intelligence economic value through the accepted formal complete-P2 path | P3-STRUCT-001, P3-ATTR-001, P3-STRAT-002 | closed by `docs/acceptance/structure-intelligence-economic-final-acceptance-20260906.md`; accepted disposition `prefer_continuous_structure_facts`, development evidence only |
| P3-STRUCT-003 | Build Continuous Structure Facts v1 as the auditable Structure Intelligence fact-product layer | P3-STRUCT-002 | closed by `docs/acceptance/continuous-structure-facts-v1-real-dwd-acceptance-20260906.md`; Real-DWD A/B exact reproduction and preregistered 12-row outcome-blind review PASS |
| P3-STRUCT-004 | Deliver the bounded Facts v2 B1-B5 vertical slice | P3-STRUCT-003 | closed; #216; all six children accepted under `docs/acceptance/structure-facts-v2-b1-b5-final-acceptance-20260907.md`; bounded retrospective engineering only, B6-B11 remain separate |
| P3-STRUCT-004-B1 | Freeze Facts v2 domains, semantic naming and coverage blueprint | P3-STRUCT-003 | closed; #217; retained `structure-facts-v2-contract-checkpoint-20260906.md` and design-entry review |
| P3-STRUCT-004-B1-R | Separate business clocks, production receipts and consumption audit | P3-STRUCT-004-B1 | closed; #218; retained `structure-facts-v2-temporal-ownership-checkpoint-20260906.md` and design-entry review |
| P3-STRUCT-004-B2 | Qualify bounded Facts v2 input, time view, identity and coordinates | P3-STRUCT-004-B1-R | closed; #219; `docs/acceptance/structure-facts-v2-b2-input-readiness-20260907.md`; 174/174 report attempts admitted, explicit retrospective view |
| P3-STRUCT-004-B3 | Build prior-20/60 references with qualified target identity | P3-STRUCT-004-B2 | closed; #220; `docs/acceptance/structure-facts-v2-b3-b4-real-object-interaction-20260907.md`; target-qualified 20/60 objects reproduced |
| P3-STRUCT-004-B4 | Record same-anchor close crossings, loss and reclaim | P3-STRUCT-004-B3 | closed; #221; `docs/acceptance/structure-facts-v2-b3-b4-real-object-interaction-20260907.md`; nine report interactions reproduced with frozen-target evidence |
| P3-STRUCT-004-B5 | Qualify minimal episodes, audited consumer and P2 through cold A/B | P3-STRUCT-004-B4 | closed; #222; `docs/acceptance/structure-facts-v2-b5-cold-ab-review-20260907.md`; same-revision A/B, 168 audit records, 5/5 P2 and 12/12 review per defined scope |
| P3-STRUCT-005 | Formalize 演化支撑事实族 / Support Evolution Fact Family（historical F9） | P3-STRUCT-004, P3-STAGE-001-H | closed; #253; Route-A narrow B3/B4/B5 extension accepted on two independent cold real-DWD Jobs; no B6/B7 or official Stage labels |
| P3-STRUCT-005-A | Freeze Support Evolution object/event/process/measurement semantics | P3-STRUCT-004, P3-STAGE-001-H | closed; #249; exact rolling-low role, break/reclaim, persistence, rebound and prior-10-ref semantics frozen in `docs/design/structure-support-facts.md` |
| P3-STRUCT-005-B | Implement formal Support Evolution Facts, deep verifier and governed runner | P3-STRUCT-005-A | closed; #250; historical technical code remains F9; focused counterexamples and fixed Event Worker route; `docs/acceptance/structure-f9-release-ready-20260910.md` |
| P3-STRUCT-005-C | Qualify Support Evolution Facts with two independent cold real-DWD Jobs | P3-STRUCT-005-B | closed; #251; exact A/B semantic reproduction, provider build once/no cross-job cache and 12/12 outcome-blind sample; `docs/acceptance/structure-f9-real-dwd-acceptance-20260910.md` |
| P3-STRUCT-005-D | Freeze Support Evolution runtime bindings and route Stage 10/11 to projections | P3-STRUCT-005-C | closed; #252; technical capability ID `B5-F9` remains stable, Stage 5/6/7 parked and official labels/consumers/trading remain closed |
| P3-STRUCT-006 | Formalize Trend Evolution Fact Family / 趋势演化事实族 | P3-STAGE-003, P3-STRUCT-005 | closed; #268; Job `job_bb986ee743994981ab61267e70f6f7f7` accepted 174 observations / 1,914 measurements from two immutable cold-source artifacts with zero DWD/provider access |
| P3-STRUCT-006-A | Freeze Trend Evolution Fact Family semantics | P3-STAGE-003, P3-STRUCT-005 | closed; #264; `docs/design/structure-trend-evolution-facts.md`; seven threshold-free concepts and math-only velocity-delta semantics frozen |
| P3-STRUCT-006-B | Implement Trend Evolution Facts, deep verifier and fixed Worker route | P3-STRUCT-006-A | closed; #265; `docs/acceptance/structure-trend-evolution-release-ready-20260910.md`; source A/B deep reproduction route only |
| P3-STRUCT-006-C | Qualify Trend Evolution Facts from two immutable cold-source artifacts | P3-STRUCT-006-B | closed; #266; A/B fact payload digest `sha256:fa9c93f8...` exact, 174 observations / 1,914 measurements, market-data/DWD provider access 0 |
| P3-STRUCT-006-D | Freeze accepted Trend Evolution runtime bindings | P3-STRUCT-006-C | closed; #267; seven concepts bound as bounded accepted `B6-TREND`; Stage search remains separate and Structure Order is next |
| P3-STRUCT-007 | Formalize Structure Order Fact Family / 结构秩序事实族 | P3-STRUCT-006, P3-STAGE-003 | closed; #273; Job `job_87fe531cec73477fbcc92df65a43a137` accepted 174 observations / 1,218 measurements from two immutable cold-source artifacts with zero DWD/provider access |
| P3-STRUCT-007-A | Freeze Structure Order Fact Family semantics | P3-STRUCT-006, P3-STAGE-003 | closed; #269; `docs/design/structure-order-facts.md`; reuse range-width/width-change and add only range-center-shift |
| P3-STRUCT-007-B | Implement Structure Order Facts, deep verifier and fixed Worker route | P3-STRUCT-007-A | closed; #270; `docs/acceptance/structure-order-release-ready-20260910.md`; exact third-group geometry and reset semantics |
| P3-STRUCT-007-C | Qualify Structure Order Facts from two immutable cold-source artifacts | P3-STRUCT-007-B | closed; #271; exact A/B payloads, 174 observations / 1,218 measurements, market-data/DWD provider access 0 |
| P3-STRUCT-007-D | Freeze accepted Structure Order runtime bindings | P3-STRUCT-007-C | closed; #272; three concepts accepted as `B6-ORDER`; Stage 0/2 threshold search remains separate |
| P3-DICT-001 | Deliver the Quanthus Data & Indicator Catalog | P3-FEAT-001 | closed; #215; original A-E acceptance retained in `docs/acceptance/data-indicator-catalog-post-acceptance-production-closeout-20260907.md`; current productization is P3-DICT-002 |
| P3-DICT-001-A | Freeze Catalog schema, authority and relation semantics | P3-FEAT-001 | closed; #223; `docs/acceptance/data-indicator-catalog-schema-snapshot-20260907.md` |
| P3-DICT-001-B | Generate deterministic Catalog snapshot from authoritative sources | P3-DICT-001-A | closed; #224; foundation projection 664 entries / 10 explicit gaps; successor Lineage V2 current projection is tracked under P3-DICT-003 |
| P3-DICT-001-C | Expose authenticated read-only Catalog API | P3-DICT-001-B | closed; #225; GET-only summary/search/detail/relations, viewer/operator/admin `catalog:read`; `docs/acceptance/data-indicator-catalog-api-ui-20260907.md` |
| P3-DICT-001-D | Build the `/catalog` search and detail UI | P3-DICT-001-C | closed; #226; real Vinext build and SSR/source contract PASS; same API/UI acceptance evidence |
| P3-DICT-001-E | Freeze Catalog drift gates and maintenance closeout | P3-DICT-001-D | closed; #227; repository/Web/drift gates plus production API/Web deployment and Catalog authentication smoke PASS; `docs/acceptance/data-indicator-catalog-final-acceptance-20260907.md` |
| P3-DICT-002 | Productize Chinese Catalog cards and declared processing lineage | P3-DICT-001 | closed; #232 reclosed after single-canvas table-context compaction; production checkout/Web `d4e7158`, API `0c671f9`; `docs/acceptance/catalog-lineage-single-canvas-production-closeout-20260907.md` |
| P3-DICT-002-A | Separate internal diagnostics from business use limitations | P3-DICT-001 | closed; #228; `docs/acceptance/catalog-productization-implementation-audit-20260907.md` |
| P3-DICT-002-B | Cover all registered entries with Chinese business explanations | P3-DICT-002-A | closed; #229; Chinese metadata/card acceptance in the productization audit |
| P3-DICT-002-C | Add layered overview and bounded field-focused processing graphs | P3-DICT-002-B | closed; #230 single-canvas requalification PASS; `table_context_only` now renders one parent-table context canvas with compact non-field-proof warning; `docs/acceptance/catalog-lineage-single-canvas-20260907.md` |
| P3-DICT-002-D | Audit, test and release the productized Catalog | P3-DICT-002-C | closed; #231; Web-only production release `d4e7158`, health/ready/auth smoke PASS; `docs/acceptance/catalog-lineage-single-canvas-production-closeout-20260907.md` |
| P3-DICT-003 | Rebuild Catalog lineage around governed data assets | P3-DICT-002 | closed; #237; 748-entry Catalog, 94 formal assets / 80 asset edges; production checkout/Web `c25638a`, API `3aa4837`; latest navigation closeout `docs/acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md` |
| P3-DICT-003-A | Freeze asset ontology and Research ADM Registry | P3-DICT-002 | closed; #233; `docs/acceptance/catalog-lineage-v2-implementation-audit-20260907.md` |
| P3-DICT-003-B | Implement full ODS→DWD→ADM asset/field lineage | P3-DICT-003-A | closed; #234; full registered closure, exact field counterexamples and asset-only fallback accepted in implementation audit |
| P3-DICT-003-C | Separate definition and usage dependency views | P3-DICT-003-B | closed; #235; Data Lineage / Definition Dependency / Usage Dependency split accepted in implementation audit |
| P3-DICT-003-D | Audit, gate and release Catalog Lineage V2 | P3-DICT-003-C | closed; #236; full gate 1602 passed / 7 existing skips; Web navigation refinement on `c25638a`, API remains `3aa4837`; `docs/acceptance/catalog-lineage-v2-inplace-navigation-production-closeout-20260907.md` |
| P3-STAGE-001 | Learn and qualify stock-stage definitions from history | P3-STRUCT-004, P3-DICT-003 | blocked parent; A-F/I closed; H第二组development已mixed closeout并转blocked，当前无ready child；G/J remain blocked；2022-2024 fresh reuse forbidden，2025/daily/consumer/strategy remain blocked；`stock-stage-historical-learning.md` |
| P3-STAGE-001-A | Freeze first-group semantics, scope and experiment budget | P3-STRUCT-004, P3-DICT-003 | closed; #238; `docs/acceptance/stock-stage-learning-a-freeze-20260908.md`; 3/4 semantics/search/data claim ceiling frozen |
| P3-STAGE-001-B | Benchmark CPU capacity and qualify the training device | P3-STAGE-001-A | closed; #239; RX6800 WSL CPU-first accepted; `docs/acceptance/stock-stage-learning-b-capacity-20260908.md` |
| P3-STAGE-001-C | Implement minimal Facts inputs and historical harness | P3-STAGE-001-A, P3-STAGE-001-B | closed; #240; real-DWD governed harness PASS; `docs/acceptance/stock-stage-learning-c-harness-20260908.md`; classifier training remains blocked |
| P3-STAGE-001-D | Build independent T-only references and target settlements | P3-STAGE-001-C | closed; #241; v2 120-context synthetic-consensus supplement complete with 87 definite / 21 ambiguous / 12 coexistence and zero audit veto; `docs/acceptance/stock-stage-learning-de-v2-development-closeout-20260909.md` |
| P3-STAGE-001-E | Learn constrained rules and calibrate adjacent stages | P3-STAGE-001-D | closed; #242; 18 R1 trials hard-gated, 4 eligible; `config/stage-learning/development-candidate.v1.json` freezes probe=1.0 / hold=2 / distance=0 / activity=1.0; R2 not triggered |
| P3-STAGE-001-F | Evaluate held-out evidence and freeze label qualification | P3-STAGE-001-E | closed / INCONCLUSIVE; #243; Job `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7` failed before panel/prediction/scoring because final mature-held stratum had 58/80 additional unique episodes; single arbitration budget consumed, no result artifact, 2025 untouched |
| P3-STAGE-001-G | Publish observational post-DWD daily labels | P3-STAGE-001-F | blocked; as-known input and schedule qualification required |
| P3-STAGE-001-H | Expand stage groups and evaluate specific consumers | P3-STAGE-001-F | blocked; #245; 第二组development mixed closeout：Stage 10冻结initial-low + 5-session + 2%，Stage 11冻结prior-10 + same-support unreclaimed + 5% rebound；Stage 5/6/7为INCONCLUSIVE_PARKED。量化/短线/基金三视角review=`PASS_WITH_SCOPE_REDUCTION`；当前search authority关闭，无正式标签/消费者/holdout |
| P3-STAGE-001-I | Repair successor held-out sampling protocol | P3-STAGE-001-F | closed; #247; protocol-only; original target caps retained, exact-quota process failure removed, semantic hard gates unchanged, no new holdout access; `docs/acceptance/stock-stage-learning-i-protocol-repair-20260909.md` |
| P3-STAGE-001-J | Execute successor external-semantic validation on an untouched holdout | P3-STAGE-001-I | blocked for holdout; #248; 2020-2021 development census found 128 as the first size meeting all caps and freezes successor cohort size=512 using measured compute headroom; no specific securities or next partition selected, 2022-2024 fresh reuse forbidden, 2025 unopened/unselected |
| P3-STAGE-002 | Formalize Stage 10/11 Projection Contract over Support Evolution Facts | P3-STRUCT-005, P3-STAGE-001-H | closed; #258; formal development Projection Contract accepted; `{10,11}` candidate overlap is legal while `primary_stage=null`; no active qualification/LabelCard/daily/strategy authority |
| P3-STAGE-002-A | Freeze nested Stage 10/11 projection semantics | P3-STRUCT-005, P3-STAGE-001-H | closed; #254; `docs/design/stage-10-11-projection.md`; Stage 10 context + nested Stage 11 condition semantics frozen |
| P3-STAGE-002-B | Implement formal projection, deep verifier and governed qualification route | P3-STAGE-002-A | closed; #255; `docs/acceptance/stage-10-11-projection-release-ready-20260910.md` |
| P3-STAGE-002-C | Qualify projection against two immutable accepted F9 artifacts | P3-STAGE-002-B | closed; #256; Job `job_5e2d82adb2a8492488725fcda379424f` PASS; 241/2/10/6 counts, zero predicate mismatch and zero DWD/provider access |
| P3-STAGE-002-D | Freeze formal Projection Contract and successor boundary | P3-STAGE-002-C | closed; #257; `docs/acceptance/stage-10-11-projection-qualification-20260910.md`; primary-stage compression/external validation/LabelCard remain separate |
| P3-STAGE-003 | Profile third-group morphology and decide state-vs-modifier structure | P3-STAGE-002, P3-STRUCT-005 | closed; #263; one 512-security 2020-2021 profile produced 225,246 observations and `PASS_WITH_DIMENSIONAL_RECLASSIFICATION`; no active Stage search/fact authority |
| P3-STAGE-003-A | Freeze third-group semantic decomposition and morphology axes | P3-STAGE-002, P3-STRUCT-005 | closed; #259; `docs/design/stock-stage-third-group-profile.md`; 0/2 order-consolidation, 1/12 downside-lifecycle, 8/9 high-side-modifier hypotheses frozen |
| P3-STAGE-003-B | Implement governed 512-security third-group morphology profile | P3-STAGE-003-A | closed; #260; `docs/acceptance/stock-stage-third-group-profile-release-ready-20260910.md`; aggregate-only profile and fixed RX Stage Worker kind |
| P3-STAGE-003-C | Execute frozen third-group morphology profile on RX6800 | P3-STAGE-003-B | closed; #261; Job `stage_313d48cfc06948e0a7fc7a3bd77b2657` PASS, 225,246 profiled observations, no labels/search/2022-2025 access |
| P3-STAGE-003-D | Review third-group profile and freeze next facts/search route | P3-STAGE-003-C | closed; #262; `PASS_WITH_DIMENSIONAL_RECLASSIFICATION`: 1/12 prioritize Trend Evolution Facts, 0/2 require Structure Order Facts, 8/9 become modifier hypotheses |
| P3-STAGE-004 | Test Stage 0/2 structural disorder versus orderly consolidation | P3-STRUCT-006, P3-STRUCT-007, P3-STAGE-003 | closed; #278; INCONCLUSIVE_REFERENCE_SUFFICIENCY; execution complete, no Stage candidate accepted; no further search/retry |
| P3-STAGE-004-A | Freeze Stage 0/2 proposition semantics and finite search | P3-STRUCT-006, P3-STRUCT-007, P3-STAGE-003 | closed; #274; `docs/design/stage-0-2-order-proposition.md`; positive evidence, both-false allowed, 81+27 grid unchanged |
| P3-STAGE-004-B | Implement bounded Stage 0/2 development runner and fixed RX route | P3-STAGE-004-A | closed; #275; runtime-only digest reuse and progress repair retained; frozen Fact/reference/search semantics unchanged |
| P3-STAGE-004-C | Execute frozen Stage 0/2 development experiment on RX6800 | P3-STAGE-004-B | closed; #276; retry `stage_87d166eb3f4a4f7f858f9b4f3e238462` succeeded; 224,346 observations / 160 references; bounded terminal-summary evidence |
| P3-STAGE-004-D | Review and freeze Stage 0/2 development disposition | P3-STAGE-004-C | closed; #277; Stage-2 references 9/20 block both candidates; `docs/acceptance/stage-0-2-order-proposition-result-20260910.md`; no claim of semantic inseparability or full trial re-audit |
| P3-STAGE-005 | Deliver scale-explicit 0/2 reference evidence mechanism | P3-STAGE-004 | closed; #282; engineering mechanism accepted, no market qualification |
| P3-STAGE-005-A | Freeze scale-explicit business and reference contract | P3-STAGE-004 | closed; #279; `docs/design/stage-order-reference-protocol.md` |
| P3-STAGE-005-B | Implement T-only packets and traceable reviewer ledgers | P3-STAGE-005-A | closed; #280; strict source/opinion binding, retained disagreement and cold readback |
| P3-STAGE-005-C | Accept synthetic engineering closure | P3-STAGE-005-B | closed; #281; `docs/acceptance/stage-order-reference-engineering-20260910.md`; synthetic engineering only, not a market run |
| P3-STAGE-006 | Real-input reference collection feasibility | P3-STAGE-005 | closed; #287; real collection PASS, 12/24 retained; no candidate or independent semantic qualification |
| P3-STAGE-006-A | Freeze source/selection/review/stop | P3-STAGE-005 | closed; #283; docs/design/stage-order-reference-feasibility.md |
| P3-STAGE-006-B | Release bounded two-phase Worker route | P3-STAGE-006-A | closed; #284; 1932 tests passed |
| P3-STAGE-006-C | Execute twelve-case reference collection | P3-STAGE-006-B | closed; #285; prepare fec6e60e / resolve 2a380e24 succeeded, no retry |
| P3-STAGE-006-D | Freeze feasibility disposition | P3-STAGE-006-C | closed; #286; 0=4, 2=1, neither=3, ambiguous=4; docs/acceptance/stage-order-reference-feasibility-20260911.md |
| P3-STAGE-007 | Fixed-rule development screen with a business disposition | P3-STAGE-006 | closed; #292; REVISE_TARGET_OR_PARK; Stage0 definite precision 0.70 / FPR 0.146, Stage2 exploratory precision 1.00; docs/acceptance/stage-order-rule-screen-cd-closeout-20260911.md |
| P3-STAGE-007-A | Resolve business boundaries and freeze one executable seed | P3-STAGE-006 | closed; docs/design/stage-order-rule-screen.md; synthetic rule tests only |
| P3-STAGE-007-B | Prove source isolation and bind minimal execution | P3-STAGE-007-A | closed; #289; one technical retry consumed, 512 old cohort / 515 identities excluded / 160 fixed cases; docs/acceptance/stage-order-rule-screen-b-prepare-success-20260911.md |
| P3-STAGE-007-C | Execute one fixed reference and scoring batch | P3-STAGE-007-B | closed; #290; 160 cases / 320 same-origin opinions sealed before one frozen score Job; docs/acceptance/stage-order-rule-screen-cd-closeout-20260911.md |
| P3-STAGE-007-D | Attribute errors and decide continuation or parking | P3-STAGE-007-C | closed; #291; Stage0 representation/target failure plus recent-window reference-veto defect; no rerun/tuning |
| P3-STAGE-008 | Repair Stage-0 direction boundary and primary/recent reference semantics | P3-STAGE-007 | closed; `REVISE_OR_PARK_EVALUATION_FAIL`; fresh evaluation improved Stage0 but missed absolute precision/FPR gates; docs/acceptance/stage-directional-boundary-repair-d-evaluation-closeout-20260912.md |
| P3-STAGE-008-A | Freeze semantic decomposition, path Facts and four-party review | P3-STAGE-007 | closed; docs/design/stage-directional-boundary-repair.md; no runtime authority |
| P3-STAGE-008-B | Implement threshold-free path Facts and primary-first review schema | P3-STAGE-008-A | closed; engineering accepted; docs/acceptance/stage-directional-boundary-repair-b-implementation-20260912.md |
| P3-STAGE-008-C | Synthetic conformance plus exposed-007 diagnostic regression | P3-STAGE-008-B | closed; PASS_DIAGNOSTIC_CONFORMANCE_ONLY; docs/acceptance/stage-directional-boundary-repair-c-conformance-20260912.md |
| P3-STAGE-008-D | One fresh task-disjoint development screen | P3-STAGE-008-C | closed; 95 fresh evaluation cases; precision 0.5769 / FPR 0.1692 / coverage 1.0; `REVISE_OR_PARK_EVALUATION_FAIL` |
| P3-STAGE-009 | Repair the positive Stage-0 structural-instability proposition | P3-STAGE-008 | closed; `REVISE_OR_PARK_DEVELOPMENT_FAIL`; A/B/C closed, D stopped at 122-case development / 27 candidates / 0 eligible before evaluation; docs/work-items/stage-positive-instability-proposition-repair.md |
| P3-STAGE-009-A | Freeze positive-instability semantics and adversarial counterexamples | P3-STAGE-008 | closed; design approved; docs/design/stage-positive-instability-proposition-repair.md |
| P3-STAGE-009-B | Implement the approved positive-instability contract | P3-STAGE-009-A | closed; engineering accepted; docs/acceptance/stage-positive-instability-b-implementation-20260912.md |
| P3-STAGE-009-C | Synthetic plus consumed-evidence conformance diagnostics | P3-STAGE-009-B | closed; PASS_DIAGNOSTIC_CONFORMANCE_ONLY; docs/acceptance/stage-positive-instability-c-conformance-20260912.md |
| P3-STAGE-009-D | Fresh task-disjoint validation | P3-STAGE-009-C | closed; `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 2021 evaluation unread; docs/acceptance/stage-positive-instability-d-development-closeout-20260912.md |
| P3-STAGE-010 | Repair positive-instability quantitative projection topology | P3-STAGE-009 | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; D2 sealed 160-case review, scored exact 9 candidates, 0 eligible; residual blocker is a Stage0/Stage2 truth-boundary conflict, preserved 2021 unread; docs/work-items/stage-positive-instability-quantitative-projection-repair.md |
| P3-STAGE-010-A | Freeze separated channel projection topology and adversarial review | P3-STAGE-009 | closed; design approved; docs/design/stage-positive-instability-quantitative-projection-repair.md |
| P3-STAGE-010-B | Implement deterministic separated-channel projection contract | P3-STAGE-010-A | closed; engineering accepted; docs/acceptance/stage-positive-instability-quantitative-projection-b-implementation-20260912.md |
| P3-STAGE-010-C | Consumed Stage-009 failure topology conformance | P3-STAGE-010-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; no threshold learning |
| P3-STAGE-010-D | Fresh 2019 development then exact preserved 2021 evaluation | P3-STAGE-010-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 9 trials / 0 eligible; near-pass frontier is Stage2-overlap=2 vs coverage=0.6296, and post-result diagnostic identifies Stage0/Stage2 boundary conflict; 2021 unread; docs/acceptance/stage-positive-instability-quantitative-d2-development-closeout-20260913.md |
| P3-STAGE-011 | Repair Stage0/Stage2 joint boundary and orderly-consolidation projection | P3-STAGE-010 | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; D1 recovery + sealed D2 review completed, exact 27 pairs scored with 0 eligible; Stage2 repair is strong but Stage0 coverage remains binding; preserved 2021 unread; docs/work-items/stage-orderly-consolidation-boundary-repair.md |
| P3-STAGE-011-A | Freeze Stage2 topology and joint 0/2 boundary | P3-STAGE-010 | closed; design approved; docs/design/stage-orderly-consolidation-boundary-repair.md |
| P3-STAGE-011-B | Implement deterministic orderly-consolidation projection and synthetic counterexamples | P3-STAGE-011-A | closed; engineering accepted; docs/acceptance/stage-orderly-consolidation-boundary-b-implementation-20260913.md |
| P3-STAGE-011-C | Consumed-evidence topology conformance | P3-STAGE-011-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; docs/acceptance/stage-orderly-consolidation-boundary-c-conformance-20260913.md |
| P3-STAGE-011-D | Fresh joint Stage0/Stage2 development | P3-STAGE-011-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 190 cases / 380 sealed opinions / exact 27 pairs / 0 eligible; best Stage0 coverage 58/83=0.6988 vs 0.70 while balanced Stage2 is 20/21 TP at precision 0.9524; 2021 unread; docs/acceptance/stage-orderly-consolidation-joint-d2-development-closeout-20260913.md |
| P3-STAGE-012 | Atomic proposition decomposition and compact-disorder boundary | P3-STAGE-011 | closed `CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY`; sole G1 failed frozen 512-identity gate, no G2/G3/H execution or market qualification; accepted assets retained and preserved 2021 unread; docs/work-items/stage-atomic-proposition-successor.md |
| P3-STAGE-012-A | Atomic ontology and resolver audit | P3-STAGE-011 | closed `PASS_ATOMIC_PROPOSITION_FIRST`; docs/acceptance/stage-atomic-proposition-a-ontology-audit-20260913.md |
| P3-STAGE-012-B | Consumed relocation-conflict adjudication | P3-STAGE-012-A | closed `PASS_CONSUMED_SEMANTIC_ADJUDICATION`; 0 guard-overfire / 2 reference-undercall / 9 genuine-coexistence; docs/acceptance/stage-atomic-proposition-b-relocation-adjudication-20260913.md |
| P3-STAGE-012-C | Compact-disorder observational sufficiency audit | P3-STAGE-012-A | closed `INCONCLUSIVE`; current Facts/composition insufficient for a positive compact-disorder claim, new Fact not proven; docs/acceptance/stage-atomic-proposition-c-observable-audit-20260913.md |
| P3-STAGE-012-D | Expert review and route decision | P3-STAGE-012-A/B/C | closed `RECOMMEND_SYNTHETIC_ONLY_ATOMIC_AND_ENVELOPE_ROUTE`; docs/reviews/stage-atomic-proposition-d-expert-review-20260913.md |
| P3-STAGE-012-E | Atomic reference/resolver + envelope synthetic package | P3-STAGE-012-D | closed synthetic-only; E1/E2/E3 accepted `PASS_SYNTHETIC_CONFORMANCE`; docs/acceptance/stage-atomic-proposition-e-synthetic-acceptance-20260913.md |
| P3-STAGE-012-E1 | Atomic Primary review/resolver contract | P3-STAGE-012-D | closed; atomic ledger preserved before projection |
| P3-STAGE-012-E2 | Threshold-free range-envelope prototype | P3-STAGE-012-D | closed synthetic non-Fact prototype; no compact-disorder truth or threshold selected |
| P3-STAGE-012-E3 | Adversarial synthetic conformance | P3-STAGE-012-E1/E2 | closed `PASS_SYNTHETIC_CONFORMANCE` |
| P3-STAGE-012-E4 | Post-E expert review / Fact-projection route decision | P3-STAGE-012-E1/E2/E3 | closed `RECOMMEND_B6_PATH_ENVELOPE_FORMALIZATION_SYNTHETIC_ONLY`; docs/reviews/stage-atomic-proposition-e4-post-synthetic-review-20260913.md |
| P3-STAGE-012-E5 | Structure-owned B6-PATH envelope formalization design + synthetic qualification | P3-STAGE-012-E4 | closed `PASS_SYNTHETIC_QUALIFICATION`; single Structure-owned algorithm core, proposed trace remains unregistered; docs/acceptance/structure-directional-path-envelope-e5-synthetic-acceptance-20260914.md |
| P3-STAGE-012-E6 | Post-E5 registration/revision/research-only/park review | P3-STAGE-012-E5 | closed `REVISE_NEUTRAL_CONCEPT`; current `structured_path_trace` representation is not Catalog-compatible; docs/reviews/structure-directional-path-envelope-e6-registration-review-20260914.md |
| P3-STAGE-012-E7 | Catalog-facing neutral concept/result-kind revision | P3-STAGE-012-E6 | closed `PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION`; existing `measurement` kind used, E5 semantics unchanged, Catalog/B6-PATH unmodified; docs/acceptance/structure-directional-path-envelope-e7-representation-acceptance-20260914.md |
| P3-STAGE-012-E8 | Reserved registration decision review | P3-STAGE-012-E7 | closed `REGISTER_RESERVED_MEASUREMENT`; Catalog remains unchanged; docs/reviews/structure-directional-path-envelope-e8-registration-review-20260914.md |
| P3-STAGE-012-E9 | Additive reserved semantic registration + metadata/planning alignment | P3-STAGE-012-E8 | closed `REGISTERED_RESERVED_MEASUREMENT_NO_RUNTIME_BINDING`; docs/acceptance/structure-directional-path-envelope-e9-registration-acceptance-20260914.md |
| P3-STAGE-012-F | Freshness inventory + atomic-first development preregistration | P3-STAGE-012-E9 | closed `PREREGISTRATION_FROZEN`; 2016 fresh development selected before data; docs/acceptance/stage-atomic-proposition-f-preregistration-20260914.md |
| P3-STAGE-012-G | Fresh-2016 atomic development | P3-STAGE-012-F | closed `FAILED_FRESHNESS_CAPACITY_TERMINAL`; historical engineering acceptance retained, no retry or G2/G3 execution; docs/acceptance/stage-atomic-proposition-p012-capacity-closeout-20260914.md |
| P3-STAGE-013 | Era × board exposure-aware development reservoir | P3-STAGE-012 | closed `CLOSED_WITH_PRE_REVIEW_INPUT_ADMISSION_SHORTFALL`; exposure-aware model, capacity census and exact plan retained, but sole semantic D1 failed before review; P3-STAGE-014 is the admission-qualified successor; docs/work-items/stage-development-resource-model.md |
| P3-STAGE-013-A | Metadata-only era × board × exposure capacity census | P3-STAGE-013 | closed `PASS_METADATA_CAPACITY_CENSUS`; 90,067 available identity-window capacity vs 18,967 legacy whole-identity capacity; no result-bearing read; docs/work-items/stage-development-resource-model.md |
| P3-STAGE-013-B | Freeze board-era quotas and reusable-window policy | P3-STAGE-013-A | closed with additive R1 capacity-constrained temporal spread; 8 cells still each 32 reserve / 24 primary / 8 fallback and current-batch global identity uniqueness; docs/reviews/stage-development-resource-p013c-r1-four-party-review-20260914.md |
| P3-STAGE-013-C | Freeze successor result-bearing preregistration | P3-STAGE-013-B | closed `PASS_EXACT_PLAN_AND_SUCCESSOR_PREREG_FROZEN`; R1 plan freezes 256 globally unique identities across 8 cells with 192 primary / 64 fallback and binds unchanged P012 atomic surfaces/gates; docs/acceptance/stage-atomic-proposition-successor-preregistration-20260914.md |
| P3-STAGE-013-D | Result-bearing successor development | P3-STAGE-013-C | closed `CLOSED_PRE_REVIEW_INPUT_ADMISSION_SHORTFALL`; sole exact-plan Job failed before review/scoring at PRE_STAR/MAIN complete-case admission; no retry; docs/reviews/stage-p013d-input-admission-four-party-review-20260914.md |
| P3-STAGE-014 | Admission-qualified Era × Board reservoir | P3-STAGE-013-D | closed `CLOSED_WITH_ATOMIC_REFERENCE_SUFFICIENCY_FAIL`; admission readiness and exact 8×32 qualified plan succeeded, but sealed 192-case blind ledger had orderly-consolidation TRUE=18 < frozen minimum 20 before candidate scoring; P3-STAGE-015 succeeds it |
| P3-STAGE-014-A | Admission-only Era × Board resource screen | P3-STAGE-014 | closed `PASS_ADMISSION_CAPACITY_READY_FOR_QUALIFIED_PLAN`; sole Job succeeded, all 8 cells >=32 qualified identities (minimum early-STAR=36), no semantic computation; config/stage-learning/stage-development-resource-admission-result.v1.json |
| P3-STAGE-014-B | Freeze exact admission-qualified 8×32 plan | P3-STAGE-014-A | closed `PASS_QUALIFIED_EXACT_PLAN_FROZEN`; 256 globally unique identities, 8×32, 192 primary + 64 fallback, no market read; config/stage-learning/stage-development-resource-qualified-plan-result.v1.json |
| P3-STAGE-014-C | Freeze atomic semantic preregistration/runtime on qualified plan | P3-STAGE-014-B | closed `PASS_ENGINEERING_READY_FOR_ONE_QUALIFIED_SEMANTIC_JOB`; exact qualified plan, unchanged P012 9/1/3 surfaces/gates, one-shot runtime and blind transport frozen; docs/acceptance/stage-atomic-proposition-admission-qualified-c-engineering-20260914.md |
| P3-STAGE-014-D | Run atomic semantic development on qualified plan | P3-STAGE-014-C | closed `CLOSED_ATOMIC_REFERENCE_SUFFICIENCY_FAIL`; sole Job `stage_7c11b97d47a0473fafa37783056f55f4` produced 192 complete cases and a sealed blind ledger, but orderly-consolidation definite TRUE=18 < 20; candidate selection transport remained unread |
| P3-STAGE-014-E | Independent I/R/O selection and projection compatibility | P3-STAGE-014-D | closed `NOT_REACHED_REFERENCE_INSUFFICIENCY`; no candidate scoring or preserved-2021 access |
| P3-STAGE-015 | Complete precommitted reserve reference route | P3-STAGE-014-D | closed `PASS_COMPLETE_RESERVE_REFERENCE_SUFFICIENCY`; exact pre-result P014-B 256-row ceiling yielded 252 review-ready cases, all 60 new ready cases were reviewed, and the combined immutable ledger passes unchanged P012 reference minima; candidate scoring remains separate; config/stage-learning/stage-complete-reserve-reference-sufficiency-result.v1.json |
| P3-STAGE-015-A | Complete-reserve review-packet readiness census | P3-STAGE-015 | closed `PASS_COMPLETE_RESERVE_REVIEW_READINESS`; sole Job froze 252/256 ready, four `PRIMARY_FACTS_UNAVAILABLE`, all prior 192 still ready, and exactly 60 new review-required rows; config/stage-learning/stage-complete-reserve-review-readiness-result.v1.json |
| P3-STAGE-015-B | Freeze full ready denominator and additional blind-review set | P3-STAGE-015-A | closed `PASS_COMPLETE_ADDITIONAL_REVIEW_SET_FROZEN`; exact R1 recovery fixed only the closeout field-path bug and froze all 60 matching rows with no semantic selection; config/stage-learning/stage-complete-reserve-additional-review-set-result.v1.json |
| P3-STAGE-015-C | Review only additional ready rows and seal combined ledger | P3-STAGE-015-B | closed `PASS_INCREMENTAL_60_REVIEW_LEDGER_SEALED`; new ledger SHA `sha256:db3216d322e00ad40ebd7de435684b43bcaf72cbc66a90dcb49403a8be461e06`, old 192 opinions unchanged; docs/acceptance/stage-complete-reserve-reference-sufficiency-20260915.md |
| P3-STAGE-015-D | Evaluate unchanged P012 atomic reference sufficiency | P3-STAGE-015-C | closed `PASS_COMPLETE_RESERVE_REFERENCE_SUFFICIENCY`; combined 252 references pass all unchanged P012 minima (I 40/147, R 46/170, O 33/172 definite positive/negative); candidate scoring remains unauthorized until a separate freeze |
| P3-STAGE-016 | Combined atomic scoring and projection compatibility | P3-STAGE-015 | closed `CLOSED_INSTABILITY_DEVELOPMENT_FAIL`; sole artifact-only scoring Job found 0/9 eligible frozen instability candidates under the pooled-count/macro-rate contract, so R/O and projection were not reached; config/stage-learning/stage-combined-atomic-scoring-result.v1.json |
| P3-STAGE-016-A | Freeze P016 scoring semantics and artifact closure | P3-STAGE-016 | closed `PASS_SELECT_MACRO_RATE_POOLED_COUNT_TWO_JOB_SCORING_ROUTE`; four-party same-origin review resolved P014-vs-P012 aggregate mismatch before candidate access; docs/reviews/stage-p016-combined-scoring-four-party-review-20260915.md |
| P3-STAGE-016-B | Independently score/freeze I, fixed R and O | P3-STAGE-016-A | closed `CLOSED_INSTABILITY_DEVELOPMENT_FAIL`; all 9 I candidates failed, fixed synthetic envelope added 0 incremental TP/FP in all trials, and the frozen stop rule prevented R/O scoring; config/stage-learning/stage-combined-atomic-scoring-result.v1.json |
| P3-STAGE-016-C | Projection compatibility on frozen P016-B outputs | P3-STAGE-016-B | closed `NOT_REACHED_NO_ATOMIC_FREEZE`; no projection or candidate reselection occurred |
| P3-STAGE-016-D | Close P016 and gate preserved evaluation | P3-STAGE-016-C | closed `PRESERVED_EVALUATION_NOT_AUTHORIZED`; preserved 2021/2025 remain unread; P3-STAGE-017 is the selected development successor |
| P3-STAGE-017 | Era × Board stratum-normalized instability development | P3-STAGE-016 | closed `CLOSED_STRATUM_NORMALIZED_INSTABILITY_FAIL`; exact R1 recovery completed the intended nine-profile experiment, 0/9 passed unchanged gates, so local scale calibration alone did not solve instability separability; config/stage-learning/stage-stratum-normalized-instability-result.v1.json |
| P3-STAGE-017-A | Freeze P017 normalization/topology/blinding semantics | P3-STAGE-017 | closed `PASS_STRATUM_NORMALIZED_INSTABILITY_ENGINEERING_FREEZE`; bases/profile thresholds frozen before reference-ledger read, no DWD/reference rewrite/gate relaxation |
| P3-STAGE-017-B | Run stratum-normalized instability scoring | P3-STAGE-017-A | closed `CLOSED_STRATUM_NORMALIZED_INSTABILITY_FAIL`; recovery Job `stage_5356bab390684fd9ae154e5d14b1a02b` succeeded, 0/9 profiles eligible, no prediction freeze produced |
| P3-STAGE-017-C | Review P017 semantic outcome | P3-STAGE-017-B | closed `PASS_OPEN_P018_INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC`; next question is expansion-vs-broad channel attribution, not threshold retuning; docs/reviews/stage-p017-stratum-normalized-no-pass-four-party-review-20260915.md |
| P3-STAGE-017-D | Close P017 and decide next gate | P3-STAGE-017-C | closed `PRESERVED_EVALUATION_NOT_AUTHORIZED`; P018 requires a separate freeze; 2021/2022-2024/2025 remain unread |
| P3-STAGE-018 | Instability channel decomposition diagnostic | P3-STAGE-017 | closed `PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE`; exact P017 channels show expansion is high-precision/low-coverage while broad-only dominates false positives; config/stage-learning/stage-instability-channel-decomposition-result.v1.json |
| P3-STAGE-018-A | Freeze P018 diagnostic artifact closure and channel semantics | P3-STAGE-018 | closed `PASS_CHANNEL_DECOMPOSITION_ENGINEERING_FREEZE`; exact P017 grid/bases and parent reference closure, no new threshold/profile/reference/label authority |
| P3-STAGE-018-B | Execute exact channel decomposition | P3-STAGE-018-A | closed `PASS_CHANNEL_DECOMPOSITION_DIAGNOSTIC_COMPLETE`; sole Job `stage_4e8eedc28b5542c798a8b33c8276755f`, no candidate selection or preserved read |
| P3-STAGE-018-C | Four-party review P018 attribution | P3-STAGE-018-B | closed `PASS_RECOMMEND_FRESH_BLIND_MECHANISM_REFERENCE_PILOT`; broad-only drives contamination, parent OR should not be repaired by threshold tuning |
| P3-STAGE-018-D | Close P018 and decide mechanism-specific next gate | P3-STAGE-018-C | closed `RECOMMEND_P019_FRESH_BLIND_MECHANISM_REFERENCE_PILOT`; preserved as the local Stage-0 recommendation, later superseded as active route by P3-STAGE-019 portfolio rebaseline |
| P3-STAGE-019 | Third-group portfolio rebaseline | P3-STAGE-018 | closed `PASS_REBASELINE_THIRD_GROUP_ROUTE`; parks active Stage-0 parent qualification, retains expansion research-only and Stage2/order as strong exploratory, and restores `1/12` as the next active third-group route; docs/work-items/stage-third-group-portfolio-rebaseline.md |
| P3-STAGE-020 | Downside lifecycle proposition pilot | P3-STAGE-019, P3-STRUCT-006 | closed after P020-C reference no-pass; Stage12 retained research-only, Stage1 retained as supported nested condition but not standalone-qualified; docs/work-items/stage-downside-lifecycle-proposition.md |
| P3-STAGE-020-A | Freeze downside-context / nested-stabilization semantics and reference protocol | P3-STAGE-020 | closed semantic freeze; no market read, threshold search, review opinion collection or primary-stage compression; config/stage-learning/stage-downside-lifecycle-concept.v1.json |
| P3-STAGE-020-B | Freeze Era×Board exposure/admission capacity and exact development plan | P3-STAGE-020-A | closed `CLOSED_FRESH_WINDOW_CAPACITY_SHORTFALL_BEFORE_ADMISSION`; exact R1 artifact reports zero fresh metadata windows in both STAR cells before any admission market read; config/stage-learning/stage-downside-lifecycle-resource-result.v1.json |
| P3-STAGE-020-C | Seal candidate-blind context/nested-condition references | P3-STAGE-020-B, P3-STAGE-024 | closed `CLOSED_BLIND_REFERENCE_INSUFFICIENT_NO_RESOURCE_REOPEN_REVIEWED`; Stage12 149 definite clusters but `30/179` unjudgeable exceeds 15%; every Stage1 gate passes; P020-D/resource repair not opened; `docs/work-items/stage-downside-lifecycle-blind-reference.md` |
| P3-STAGE-020-D | Freeze finite candidate topology and score propositions independently | P3-STAGE-020-C | closed not-opened; P020-C reference sufficiency failed, so no candidate topology or scoring was authorized |
| P3-STAGE-020-E | Candidate-route review / preserved gate | P3-STAGE-020-D | closed not-opened; P020-C failure policy closed the branch before candidate scoring |
| P3-STAGE-021 | Admission-only exposure taxonomy repair | P3-STAGE-020-B | closed `CLOSED_REPAIRED_TAXONOMY_GLOBAL_IDENTITY_SHORTFALL`; L2A taxonomy restores ≥32 identities in every cell, but unchanged 256-reserve global uniqueness reaches only 243; docs/work-items/stage-admission-exposure-taxonomy.md |
| P3-STAGE-021-A | Freeze L2A technical-read / L2B semantic-read taxonomy | P3-STAGE-020-B | closed preregistration; only P014 admission rows outside the qualified semantic plan may be L2A reusable, while P014 qualified-plan windows remain consumed |
| P3-STAGE-021-B | Recompute Era×Board capacity under frozen taxonomy | P3-STAGE-021-A | closed `CLOSED_REPAIRED_TAXONOMY_GLOBAL_IDENTITY_SHORTFALL`; per-cell capacity restored, global unique 8×32 = 243/256; config/stage-learning/stage-admission-exposure-capacity-result.v1.json |
| P3-STAGE-021-C | Four-party review repaired capacity | P3-STAGE-021-B | closed; final active review identities stay globally unique, dormant fallbacks need not be globally unique by reservation alone |
| P3-STAGE-021-D | Close P021 and gate any P020 successor | P3-STAGE-021-C | closed `RECOMMEND_P022_PRIMARY_UNIQUE_CONTINGENT_FALLBACK`; P020 remains blocked until a separately frozen resource contract passes |
| P3-STAGE-022 | Primary-unique / contingent-fallback resource contract | P3-STAGE-021 | closed `CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL`; exact 8×24 primary panel = 192 globally unique identities, but dormant fallback pool = 62/64 with early STAR at 6/8; docs/work-items/stage-primary-unique-contingent-fallback.md |
| P3-STAGE-022-A | Freeze primary/fallback independence semantics | P3-STAGE-021 | closed preregistration; 192 active primary identities globally unique, dormant fallback overlap legal only before activation |
| P3-STAGE-022-B | Freeze exact primary plan / fallback pools and test metadata feasibility | P3-STAGE-022-A | closed `CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL`; sole Job `stage_cfe744f4b8c941deb7520d1faa31c232`, 192/192 primary PASS, fallback 62/64; config/stage-learning/stage-primary-unique-contingent-fallback-result.v1.json |
| P3-STAGE-022-C | Four-party review P022 feasibility | P3-STAGE-022-B | closed; do not lower fallback ceiling; bind uniqueness to realized admission-ready cases rather than all planned identities |
| P3-STAGE-022-D | Close and gate any P020 admission successor | P3-STAGE-022-C | closed `OPEN_REALIZED_IDENTITY_ADMISSION_AND_CONTINGENT_REPLACEMENT`; separately freeze P023, P020 remains blocked |
| P3-STAGE-023 | Realized-identity admission and contingent replacement | P3-STAGE-022 | closed `CLOSED_REALIZED_ADMISSION_CAPACITY_SHORTFALL`; sole Job realizes 179 globally unique admission-ready rows, seven cells 24/24 and early STAR 11/24; docs/work-items/stage-realized-identity-admission.md |
| P3-STAGE-023-A | Freeze primary-first / realized-identity semantics | P3-STAGE-022 | closed preregistration before input read; config/stage-learning/stage-realized-identity-admission.v1.json |
| P3-STAGE-023-B | Run one primary-first admission / contingent replacement Job | P3-STAGE-023-A | closed; `stage_451124246782476ea51fa11b60c346ba`, 142 primaries ready, 37 fallbacks activated, 179 realized unique identities |
| P3-STAGE-023-C | Review realized plan and close P023 | P3-STAGE-023-B | closed; no-pass preserved, fallback readiness failures are small relative to identity-conflict pressure and early-STAR scarcity |
| P3-STAGE-023-D | Gate any P020-C preregistration | P3-STAGE-023-C | closed `OPEN_CROSS_ERA_CLUSTERED_IDENTITY_DEVELOPMENT_CONTRACT`; P020 remains blocked pending separately frozen P024 |
| P3-STAGE-024 | Cross-era clustered-identity development admission | P3-STAGE-023 | closed `CLOSED_CLUSTERED_ADMISSION_CAPACITY_SHORTFALL_REVIEWED`; sole Job ends at 187 rows / 179 clusters, early STAR 19/24; P024 remains no-pass and no P025 is opened; docs/work-items/stage-clustered-identity-admission.md |
| P3-STAGE-024-A | Freeze clustered-development admission/accounting contract | P3-STAGE-023-D | closed preregistration; row count and unique-cluster count are separate, repeated rows never inflate independent-evidence count |
| P3-STAGE-024-B | Implement artifact-bound supplemental admission runtime | P3-STAGE-024-A | closed engineering acceptance; one-shot/idempotent Worker + bounded `submit-p024` |
| P3-STAGE-024-C | Run sole supplemental admission Job | P3-STAGE-024-B | closed no-pass; 33 eligible unread conflicts, 20 reads, 8 activations, final early STAR 19/24, Stage1/12 and protected partitions unread |
| P3-STAGE-024-D | Review/close and gate P020-C preregistration | P3-STAGE-024-C | closed four-party portfolio review; preserve P024 no-pass, freeze exact 187/179 panel as final resource ceiling, open only separate P020-C preregistration |
| P3-STAGE-026 | High-side modifier proposition route | P3-STAGE-020-C, P3-STAGE-003, P3-STRUCT-006 | closed `CLOSED_STAGE8_RESOURCE_CAPACITY_SHORTFALL_NO_REPAIR`; Stage8 research-only, Stage9 parked, whole-vocabulary synthesis selected; docs/work-items/stage-high-side-modifier-proposition.md |
| P3-STAGE-026-A | Freeze advancing-context / Stage8 modifier semantics and Stage9 activity gap | P3-STAGE-026 | closed preregistration; Stage8 uses accepted Trend Evolution facts, Stage9 price-only route forbidden; config/stage-learning/stage-high-side-modifier-concept.v1.json |
| P3-STAGE-026-B | Freeze one P020-disjoint exposure-aware Stage8 resource/reference contract | P3-STAGE-026-A | closed preregistration; target 20/cell, hard floors >=16/cell, >=144 rows, >=128 identity clusters; one bounded route only |
| P3-STAGE-026-C | Prepare T-only Stage8 packets and seal blind references | P3-STAGE-026-B | closed resource no-pass after R1; 136 rows/136 clusters, STAR cells 5 and 11 below floor 16, packet/opinion count 0; config/stage-learning/stage-high-side-modifier-reference-result.v1.json |
| P3-STAGE-026-C-R1 | One pre-read technical contract-mapping recovery | P3-STAGE-026-C | closed; exact failed parent recovered once under frozen P026-A/B contract; no further retry authority; docs/reviews/stage-p026c-pre-read-technical-recovery-review-20260916.md |
| P3-STAGE-026-D | Gate finite Stage8 candidate topology | P3-STAGE-026-C | closed not opened; resource gate failed before semantic reference collection |
| P3-STAGE-026-E | Four-party high-side portfolio closeout / Stage9 activity decision | P3-STAGE-026-D or earlier fail-close | closed; no repair/gate lowering, Stage8 research-only, Stage9 parked; P3-STAGE-027 selected as separate governance-only successor |
| P3-STAGE-027 | Whole vocabulary portfolio synthesis | P3-STAGE-001, P3-STAGE-002, P3-STAGE-019, P3-STAGE-020-C, P3-STAGE-026 | closed `PASS_LAYERED_VOCABULARY_PORTFOLIO_SYNTHESIS`; 0 plus 1..12 accounted for across context/condition/modifier/parked roles, flat single-valued FSM superseded as implementation architecture; docs/work-items/stage-whole-vocabulary-portfolio-synthesis.md |
| P3-STAGE-028 | Layered Stage Proposition Bundle Contract | P3-STAGE-027 | closed `PASS_FIRST_MILESTONE_STAGE_PROPOSITION_MVP`; executable layered Bundle + strict validator + deterministic serialization + non-production Stage10/11 development-shadow view accepted, `primary_stage=null`; docs/work-items/stage-layered-proposition-bundle.md |
| P3-STAGE-029 | Real-History Stage Proposition Bundle Replay | P3-STAGE-028, P3-STAGE-002 | closed `PASS_REAL_HISTORY_LAYERED_INTERPRETATION_REPLAY_PIT_STILL_BLOCKED`; all 10 accepted real Stage10-active retrospective observations replayed with Facts-only/single-code/layered views, layered retained 16/16 explicit parent+nested propositions while comparison single-code retained 10/16; strict PIT remains blocked, no automatic successor; docs/work-items/stage-proposition-bundle-real-data-replay.md |
| P3-STAGE-030 | Strict PIT Stage10/11 public reconstruction | P3-STAGE-029 | closed `BLOCKED_SOURCE_VERSION_OR_AVAILABILITY_LINEAGE` before any PIT Stage predicate; current governed ODS/DWD lacks complete authenticated 2020 daily-bar revision/public-availability family, 4/4 blocker review opens only a separate bounded source-evidence feasibility route; docs/work-items/stage-pit-public-reconstruction.md |
| P3-STAGE-031 | Official-exchange historical EOD evidence feasibility | P3-STAGE-030 | closed `NO_PASS_OFFICIAL_EXCHANGE_EVIDENCE_INSUFFICIENT`; SSE/SZSE official EOD authority/coverage exists but exact historical version/correction lineage and T=15:30 publication evidence do not meet the frozen gate; 4/4 review permits only a separate two-product/three-date Level-1 intraday/realtime evidence feasibility successor; docs/work-items/stage-official-exchange-eod-evidence-feasibility.md |
| P3-STAGE-032 | Official Level-1 historical realtime evidence feasibility | P3-STAGE-031 | closed `NO_PASS_LEVEL1_HISTORICAL_DISSEMINATION_EVIDENCE_INSUFFICIENT`; SSE historical snapshot archive and SZSE realtime protocol do not prove exact historical retained-version -> official dissemination binding at T=15:30; fixed-date values remain not evaluable, and 4/4 phase review stops the current Stage historical PIT source-evidence hunt while preserving P029 retrospective development-shadow semantics; docs/work-items/stage-level1-historical-realtime-evidence-feasibility.md |
| P3-STAGE-033 | Layered Stage consumer utility | P3-STAGE-029, P3-STAGE-032, P3-STRUCT-002 | closed `INCONCLUSIVE_CONSUMER_UTILITY_ATTRIBUTION_MECHANICAL_GATE_PASS`; preregistered mechanical gate passed 12/20→0/20 critical-error packets, but 4/4 result review found Facts-only information sufficiency/equivalence unproven, so current Layered package gains bounded communication value without a pure-expression causal claim; no automatic successor; docs/work-items/stage-consumer-utility.md |
| P3-EXP-001 | Define expert protocols and abstention | P3 labels | closed |
| P3-AGG-001 | Implement veto precedence and constraint merge | P3-EXP-001 | closed |
| P3-AGG-002 | Implement score and action mapping | P3-AGG-001 | closed for fixture scope |
| P3-SHADOW-001 | Implement shadow execution and divergence log | P3-AGG-001 | closed for comparison-contract scope |
| P3-EXP-002 | Add vertical-slice rule experts | P3 aggregator | closed for fixture scope |
| P3-EXPERT-000 | Deliver the bounded Expert Strategy Layer MVP | META-DOC-001 | closed by `docs/acceptance/p3-expert-mvp-shadow-handoff-20260823.md` after all seven children were accepted |
| P3-EXPERT-001 | Implement additive expert evaluation protocol v2 | P3-EXP-001, P3-AGG-001 | closed by `docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md` |
| P3-EXPERT-002 | Run expert evaluation through the PostgreSQL Worker and immutable artifacts | P3-EXPERT-001, PK-WORKER-000 | closed by `docs/acceptance/p3-expert-evaluation-worker-20260820.md` |
| P3-EXPERT-003 | Freeze the first target/resolver and implement one market-data technical expert | P3-EXPERT-001, P3-EXPERT-002, P3-FEAT-001, P3-REPLAY-001, P4-LABEL-001 | closed by `docs/acceptance/p3-expert-first-target-production-20260821.md` |
| P3-STRAT-002 | Freeze and implement the first formal two-expert strategy topology | P3-EXPERT-002, P3-EXPERT-003, P3-POS-003 | closed by `docs/acceptance/p3-strat-first-formal-topology-production-20260822.md` |
| P3-POS-001 | Implement unit/position state machine | P2 ledger | closed |
| P3-POLICY-001 | Freeze trial/add/hold/cooldown parameters | P2 acceptance | closed for fixture scope |
| P3-RISK-001 | Implement concentration caps | P3-POS-001 | closed |
| P3-POS-002 | Implement trial/add/review rules | P3 experts | closed for no-add fixture scope |
| P3-EXIT-001 | Implement four-source exit arbitration | P3 aggregator | closed |
| P3-POS-003 | Implement cooldown and carried exit | P2 matcher | closed |
| P3-POS-004 | Add versioned Trial-position reduction compatibility | P3-POS-001, P2 ledger, P3-STRAT-002 | closed by `docs/acceptance/p3-pos-trial-reduction-compatibility-production-20260822.md` |
| P3-FUNNEL-001 | Implement opportunity funnel | P3 vertical slice | closed |
| P3-ATTR-001 | Run bounded attribution for the first formal expert strategy | P3-STRAT-002, P3-EXPERT-002 | closed by `docs/acceptance/p3-attr-formal-production-20260823.md` |
| P3-DASH-001 | Produce monitoring report | Funnel and attribution | deferred pending recurring formal strategy |
| P3-EXPERT-004 | Add read-only expert evidence and research decisions to candidate detail | P3-ATTR-001, P3-EXPERT-002 | closed by `docs/acceptance/p3-expert-candidate-detail-evidence-20260823.md` |
| P3-EXPERT-005 | Accept the development MVP and resolve forward-Shadow disposition | P3-EXPERT-004, P3-ATTR-001, P3-SHADOW-001 | closed by `docs/acceptance/p3-expert-mvp-shadow-handoff-20260823.md`; decision inconclusive, Shadow disabled |
| P3-EXPERT-006 | Consume Stage10/11 semantics through four expert roles before vocabulary expansion | closed stock-stage phase 1, P029, P033 | ready; one frozen research-only consumption-discovery run, exact P029 population, zero action authority; `docs/work-items/expert-stage-semantic-consumption.md` |
| P3-ACCEPT-001 | Execute multi-year vertical-slice acceptance | All P3 release blockers | closed |

**Stock-stage research phase 1 is formally converged and closed.** Machine authority:
`config/stage-learning/stock-stage-research-phase1-closeout.v1.json`; acceptance:
`docs/acceptance/stock-stage-research-phase1-convergence-20260916.md`. No P034 is opened. Historical
0..12 completeness is not a delivery requirement; reopening requires a concrete downstream consumer
need or independently new admissible evidence.

## P4-P7

| ID | Title | Phase | Disposition |
| --- | --- | --- | --- |
| P4-EVENT-001 | Freeze first research question and event/split contracts | P4 | accepted |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4 | accepted |
| P4-LABEL-001 | Freeze outcome and structural-break cards | P4 | accepted |
| P4-REPORT-001 | Produce daily group comparison report | P4 | accepted |
| PERF-BATCH-001 | Reuse composite-Job prerequisites and publish runtime telemetry | P4 | accepted |
| PERF-EVENT-001 | Accelerate exact Event scanning and expose internal progress | P4 | accepted |
| PERF-ENTRY-001 | Replace T+1 evidence reads with a set-based join | P4 | accepted |
| PERF-MEM-001 | Keep the complete P4 Event batch below 16 GiB | P4 | accepted |
| PERF-SHARD-001 | Prove 3 GiB deterministic multi-Worker Event execution | P4 | accepted |
| P5-TARGET-001 | Define independent target cards and metrics | P5 | deferred; optional research |
| P5-BASE-001 | Build rule and logistic baseline scoreboard | P5 | deferred; optional research |
| P5-GBDT-001 | Train constrained GBDT research pipeline | P5 | deferred; optional research |
| P5-PROMOTE-001 | Implement model promotion/shadow gate | P5 | deferred; optional research |
| P6-000 | Coordinate the bounded P6 prospective-validation program | P6 | open epic |
| P6-GOV-001 | Freeze entry proposal, observation budget, stop rules, and rollback | P6 | ready |
| P6-DATA-001 | Enforce prospective DWD boundary, access ledger, and correction disposition | P6 | blocked by P6-GOV-001 |
| P6-SCORE-001 | Freeze role-specific forward scorecards and loss attribution | P6 | blocked by P6-DATA-001 |
| P6-GOV-002 | Implement composite Forward Worker and disabled-state rehearsal | P6 | blocked by P6-SCORE-001 |
| P6-PAPER-001 | Implement isolated P2 paper lane and fixed-unit ledger | P6 | blocked by P6-GOV-002 |
| P6-WEB-001 | Project forward and paper evidence in candidate detail | P6 | blocked by P6-PAPER-001 |
| P6-ACTIVATE-001 | Authorize and run the first official prospective cycle | P6 | blocked by P6-WEB-001 |
| P6-REVIEW-001 | Complete the time-matured simulation review | P6 | blocked by P6-ACTIVATE-001 and closed budget |
| P7-LIVE-001 | Define email-advice and actual-operation reporting boundary; defer broker adapter | P7 | blocked by P6 review |
| P7-RECON-001 | Reconcile advice with operator-reported fills, changes, or no action | P7 | blocked by P7-LIVE-001 |
| P7-SCALE-001 | Implement explicit unit expansion decision | P7 | blocked by P7 reconciliation |

## Platform Kernel

Platform Kernel is orthogonal to P1-P7 and is governed by ADR 0010, ADR 0012, LLD 11, and
`config/platform-kernel-gates.v1.json`. PK1 Minimal Boot through PK4 Operator Web are accepted.
PK5 Optional Authoring is explicitly not applicable to the first cutover and remains recoverable
behind a later activation decision. PK6, `PK-RECOVERY-001`, and `PK-CUTOVER-001` are accepted with
the self-verifying recovery package, isolated restore, rollback/forward cycle, public acceptance,
and old-path shutdown evidence. `PK-WORKER-000` is accepted: production
replay runs in one PostgreSQL-backed Replay Worker and the five-candidate producer loop is closed.
Existing node-local warehouse schedules remain explicitly outside this milestone.

| ID | Title | Depends on | Disposition |
| --- | --- | --- | --- |
| PK-BOUNDARY-001 | Freeze capability dispositions, ownership, source baseline, and activation conditions | META-DOC-001 | accepted |
| PK-COMPOSE-001 | Implement the Feature Manifest and explicit composition root | PK-BOUNDARY-001 | accepted |
| PK-BOOT-001 | Boot the minimal approved API kernel | PK-COMPOSE-001 | accepted |
| PK-SCHEMA-001 | Split platform migrations and remove shared bootstrap ownership | PK-BOOT-001 | accepted |
| PK-DEPS-001 | Split core and optional dependency groups | PK-BOOT-001 | accepted |
| PK-AUTH-001 | Detach identity and administration from monetization domains | PK-SCHEMA-001, PK-DEPS-001 | accepted |
| PK-OBS-001 | Extract health, request id, metrics, and structured logging | PK-COMPOSE-001 | accepted |
| PK-JOBS-001 | Define JobRunner and decide the local versus Celery adapter | PK-DEPS-001 | accepted |
| PK-ADAPTER-001 | Connect selected workflows to Quanthus application ports | PK-AUTH-001, PK-JOBS-001 | accepted |
| PK-AUTHORITY-001 | Enforce the platform/domain authority boundary | PK-ADAPTER-001 | accepted |
| PK-WEB-001 | Admit, trim, and connect the desktop operator Web | PK-AUTHORITY-001 | accepted |
| PK-WEB-002 | Correct the primary product Web to candidate-only workflows | PK-WEB-001 | accepted; superseded in product depth by PK-WEB-003 |
| PK-WEB-003 | Turn the candidate catalogue into a validation workbench with real detail evidence and bounded review actions | PK-WEB-002 | accepted |
| PK-WEB-004 | Migrate candidate approval to independent evidence-review, governance, and execution projections | PK-WEB-003 | accepted |
| PK-CANDIDATE-001 | Prove one QuantDinger signal through Quanthus replay without governance promotion | PK-AUTHORITY-001 | accepted |
| PK-WORKER-000 | Deliver lean PostgreSQL Replay Worker and candidate replay production loop | PK-JOBS-001, PK-CANDIDATE-001, PK-WEB-004 | accepted |
| PK-WORKER-001 | Freeze Job/Worker/Run contracts and state machines | PK-WORKER-000 | accepted |
| PK-WORKER-002 | Implement PostgreSQL queue, leases, cancellation, and bounded progress | PK-WORKER-001 | accepted |
| PK-WORKER-003 | Implement independent Worker Supervisor and isolated attempt process | PK-WORKER-002 | accepted |
| PK-WORKER-004 | Connect candidate replay to authoritative DWD/PIT and P2 | PK-WORKER-003 | accepted |
| PK-WORKER-005 | Publish immutable Run/evidence and switch active digest atomically | PK-WORKER-004 | accepted |
| PK-WORKER-006 | Add replay operations and Run history to the candidate workbench | PK-WORKER-005 | accepted |
| PK-WORKER-007 | Inventory warehouse execution boundaries and retain node-local schedules | PK-WORKER-003 | accepted |
| PK-WORKER-008 | Deploy and accept one PostgreSQL Replay Worker on OCI | PK-WORKER-005, PK-WORKER-006 | accepted |
| PK-AUTHORING-001 | Optionally implement isolated strategy/indicator draft authoring | PK-WEB-001 | not-applicable |
| PK-RECOVERY-001 | Build provenance, contract, schema, dependency, and restoration packages | PK-BOUNDARY-001 | accepted |
| PK-CUTOVER-001 | Execute deployment, rollback, restoration drill, and old-path shutdown | PK-WEB-001, PK-AUTHORING-001, PK-RECOVERY-001 | accepted |

## Issue creation policy

P1, P2, and P3 work items are created as actionable issues now. P4-P7 receive traceability epic
issues plus the stable child-id checklist. Child issues for a later phase are created when its
predecessor gate passes, preventing a stale speculative backlog while preserving full design
traceability.

## Planning synchronization

OCI owns GitHub planning synchronization. Preview the document-derived plan without network
access, then apply it through the GitHub CLI authenticated on OCI:

```bash
python3 scripts/sync_github_planning.py
python3 scripts/sync_github_planning.py --apply
```

The synchronization is idempotent, reconciles transient GitHub list inconsistencies, aborts
on duplicate stable IDs, and preserves unmanaged labels. GitHub credentials remain in OCI's
user configuration and are never read from the repository.
