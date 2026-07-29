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
| META-DATA-PIPE-001 | Operate Hermes ODS releases and OCI T+5h publication | active |
| META-DATA-PIPE-002 | Retire the legacy weekly monolith and split Hermes ODS schedules | planned |
| META-DATA-PIPE-003 | Build dual-lane DWD/DWS candidates, OCI canonical release sets, and portable ADS | active; cutover blocked |

`META-DATA-PIPE-003` is governed by ADR 0005 and GitHub issue #45. Implementation is limited to
copy-only contract, shadow, comparison, recovery, and release-set work beside the legacy path. It
does not authorize a live `CURRENT` change, data deletion, retention cleanup, or access to sealed
research partitions. Production cutover requires its separate evidence gate and explicit
`GO_FOR_CUTOVER`.

The repository contract now includes portable `strategy_replay` ADS, fenced publication and
rollback, correction/revocation records, and accepted-ODS recovery evidence. Formal three-node
deployment, three historical ADS backfills, replay equivalence evidence, independent restore
qualification, and consumer migration remain active work. The historical 2025 bootstrap can prove
physical role separation only; prior developer visibility prevents it from serving as genuine
unopened blind evidence.

## P1 data foundation

| ID | Title | Depends on |
| --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and dataset manifests | Bootstrap |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 |
| C2 | Classify every security-calendar gap | S4, security master |
| S1 | Measure and complete delisted-security coverage | Security master |
| S2 | Derive historical ST state from announcements | Landing data |
| S3 | Derive board-specific price limits | S2, security master |
| S4 | Build authoritative trading calendar | Source ingestion |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars |
| P1-SNAPSHOT-001 | Implement atomic point-in-time snapshots | Canonical datasets |
| P1-ACCEPT-001 | Execute Chapter 01 acceptance suite | All P1 blockers |
| P1-DATA-002 | Restore incomplete none histories detected by S3 replay | S3 coverage audit |
| P1-DATA-003 | Restore truncated active none histories detected by daily coverage | P1-DATA-002 |
| P1-DATA-004 | Normalize mixed-unit corporate actions for continuous historical replay | C1, S4, P1-SNAPSHOT-001 |

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
| P3-EXP-001 | Define expert protocols and abstention | P3 labels | closed |
| P3-AGG-001 | Implement veto precedence and constraint merge | P3-EXP-001 | closed |
| P3-AGG-002 | Implement score and action mapping | P3-AGG-001 | closed for fixture scope |
| P3-SHADOW-001 | Implement shadow execution and divergence log | P3-AGG-001 | closed for comparison-contract scope |
| P3-EXP-002 | Add vertical-slice rule experts | P3 aggregator | closed for fixture scope |
| P3-POS-001 | Implement unit/position state machine | P2 ledger | closed |
| P3-POLICY-001 | Freeze trial/add/hold/cooldown parameters | P2 acceptance | closed for fixture scope |
| P3-RISK-001 | Implement concentration caps | P3-POS-001 | closed |
| P3-POS-002 | Implement trial/add/review rules | P3 experts | closed for no-add fixture scope |
| P3-EXIT-001 | Implement four-source exit arbitration | P3 aggregator | closed |
| P3-POS-003 | Implement cooldown and carried exit | P2 matcher | closed |
| P3-FUNNEL-001 | Implement opportunity funnel | P3 vertical slice | closed |
| P3-ATTR-001 | Implement constrained contribution reruns | P3 vertical slice | deferred pending formal strategy questions |
| P3-DASH-001 | Produce monitoring report | Funnel and attribution | deferred pending recurring formal strategy |
| P3-ACCEPT-001 | Execute multi-year vertical-slice acceptance | All P3 release blockers | closed |

## P4-P7

| ID | Title | Phase |
| --- | --- | --- |
| P4-EVENT-001 | Define event, isolation, and split schemas | P4 |
| P4-EVENT-002 | Implement forward candidate scan and not-executable partition | P4 |
| P4-LABEL-001 | Freeze outcome and structural-break cards | P4 |
| P4-REPORT-001 | Produce daily group comparison report | P4 |
| P5-TARGET-001 | Define independent target cards and metrics | P5 |
| P5-BASE-001 | Build rule and logistic baseline scoreboard | P5 |
| P5-GBDT-001 | Train constrained GBDT research pipeline | P5 |
| P5-PROMOTE-001 | Implement model promotion/shadow gate | P5 |
| P6-GOV-001 | Implement proposal and expectation record | P6 |
| P6-GOV-002 | Implement shadow lifecycle gate | P6 |
| P6-SCORE-001 | Implement expert and aggregator scorecards | P6 |
| P6-DATA-001 | Enforce partition access and reveal logging | P6 |
| P6-REVIEW-001 | Run first quarterly arbitration review | P6 |
| P7-LIVE-001 | Define email-advice and actual-operation reporting boundary; defer broker adapter | P7 |
| P7-RECON-001 | Reconcile advice with operator-reported fills, changes, or no action | P7 |
| P7-SCALE-001 | Implement explicit unit expansion decision | P7 |

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
