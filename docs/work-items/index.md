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
| P3-STRUCT-004 | Deliver the bounded Facts v2 B1-B5 vertical slice | P3-STRUCT-003 | ready; parent #216; `docs/design/structure-facts-v2-delivery.md`; all child gates required, runtime not accepted |
| P3-STRUCT-004-B1 | Freeze Facts v2 domains, semantic naming and coverage blueprint | P3-STRUCT-003 | closed; #217; retained `structure-facts-v2-contract-checkpoint-20260906.md` and design-entry review |
| P3-STRUCT-004-B1-R | Separate business clocks, production receipts and consumption audit | P3-STRUCT-004-B1 | closed; #218; retained `structure-facts-v2-temporal-ownership-checkpoint-20260906.md` and design-entry review |
| P3-STRUCT-004-B2 | Qualify bounded Facts v2 input, time view, identity and coordinates | P3-STRUCT-004-B1-R | ready; #219; bounded retrospective input readiness and complete denominator required |
| P3-STRUCT-004-B3 | Build prior-20/60 references with qualified target identity | P3-STRUCT-004-B2 | blocked; #220; predecessor real-input readiness required |
| P3-STRUCT-004-B4 | Record same-anchor close crossings, loss and reclaim | P3-STRUCT-004-B3 | blocked; #221; exact reference grain and evidence required |
| P3-STRUCT-004-B5 | Qualify minimal episodes, audited consumer and P2 through cold A/B | P3-STRUCT-004-B4 | blocked; #222; independent runtime evidence, persisted consumption audit and sample review required |
| P3-DICT-001 | Maintain data and indicator dictionaries | P3-FEAT-001 | deferred independent backlog under `docs/work-items/data-indicator-dictionary.md`; not a predecessor or blocker of P3-STRUCT-004 |
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
| P3-ACCEPT-001 | Execute multi-year vertical-slice acceptance | All P3 release blockers | closed |

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
