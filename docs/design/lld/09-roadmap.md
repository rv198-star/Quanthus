# LLD 09: Delivery Roadmap and Milestones

## 1. Build order

The architecture flows data to representation to experts to positions, but delivery order is
data, backtest referee, rule-only vertical slice, event library, research models, simulation,
and live operation. A later phase cannot compensate for an untrusted earlier phase.

## 2. Phase plan

| Phase | Deliverable | Gate | Dependency |
| --- | --- | --- | --- |
| P0 | Policy, PRD, HLD/LLD, configuration, contracts | `make check` and approved decisions | None |
| P1 | Daily data, lifecycle, tradability, snapshots | C1/C2/S1 closed and D1/D2/D3 pass | P0 |
| P2 | Matcher, unit ledger, audit, metrics | Golden cases reconcile and rerun identically | P1 |
| P3 | Rule-only vertical slice and shadow experts | Multi-year closed loop is auditable | P2 |
| P4 | Event library and daily group report | Three leading differentiators | P3 |
| P5 | Optional research scoring layer | Beats rule baseline and passes promotion gate | P4; deferred and not a P6 predecessor |
| P6 | Completed historical-mechanism and paper-flow validation | Owner scope closeout 2026-10-23; future prospective work cancelled, not accepted | P3/P4 and Expert Strategy MVP |
| P7 | Operator-assisted small-capital operation | Every advice closes with an accepted actual/no-action report and reconciliation | P6 |
| P10 | Permanent acquisition, shared data quality/governance and versioned supply | Each bounded repair/publication passes its own gates; standing line remains open | Parallel permanent line; P1 historical foundation retained |

P10 owns acquisition and shared data-quality/governance issues. Other phases register a defect,
choose a disclosed business bypass, temporary ignore or acceptance of a known limitation, and
focus on domain work; they must not recover/backfill/repair inputs themselves. Stop only a step
that cannot meet its existing hard gate. P10 is not a sequential release after P7 and does not
accept P3/P5 or activate training/production. See
[`12-permanent-data-line.md`](12-permanent-data-line.md) and ADR 0015.

P6 is closed under the [Owner-revised historical scope](../../acceptance/p6-owner-scope-closeout-20261023.md).
This does not open P7 or accept the cancelled prospective gates. P5 remains optional
and deferred. A development build cannot alter a frozen simulation policy.

Platform Kernel is a separate engineering track rather than P8. It may proceed beside P4 when its
own predecessor gate passes, but it cannot weaken a P1-P7 domain gate or redefine Quanthus data,
replay, strategy, decision, or execution authority.

Current state: P2 remains conditionally accepted for the active daily research profile by
`config/p2-gates.v1.json`, and P3 is accepted by `config/p3-gates.v1.json`. Its strategy-neutral
replay, rule contracts, position/risk path, audit funnel, and one frozen capability fixture prove
the multi-year closed loop. That fixture is not a formal strategy lineage and cannot be tuned or
promoted. The P4 Event Library, Label Corpus, Comparison Report, and bounded Worker execution are
accepted. `P6-GOV-001` is accepted with one frozen prospective-mechanism-MVP entry proposal; the
mandatory owner value checkpoint subsequently opened and closed `P6-DATA-001`; `P6-SCORE-001`
then froze role-specific forward scorecards and material-loss attribution. `P6-GOV-002` closed the
disabled composite-Worker lifecycle on the existing replay queue with immutable recovery and zero
prospective side effects. `P6-PAPER-001` then accepted the isolated five-unit fixed-notional P2
paper lane, and `P6-WEB-001` accepted the digest-bound read-only candidate-detail projection.
`P6-ACTIVATE-001` has crossed the production activation boundary: the signed activation is enabled,
production health projects `prospective_activation_enabled`, and official collection remains `0 / 66`
until the first legal post-activation READY trading-session release. The work item closes only after
that first official cycle and independent reproduction; `P6-REVIEW-001` is the existing GitHub #120
and waits for the frozen real-calendar budget. P5 is optional research and remains deferred. Target two-window activation
and forward notification retain their separate blockers and are outside the initial P6 profile.

## 3. Milestones

- **M0**: repository baseline, versioned P0 configuration, documentation, tests, and issue map.
- **M1**: accepted data foundation and simple baseline strategy through the trusted engine.
- **M2**: rule-only vertical slice over multiple years with complete decision trace.
- **M3**: first event differentiation report and proposal-to-shadow-to-decision loop.
- **M4**: frozen prospective Shadow and isolated fixed-unit paper operation begin, then accumulate
  a predeclared time-matured evidence budget without notification or live-fill claims.
- **M5**: first operator-assisted live review and explicit continue/stop decision.
- **Platform Kernel**: selectively extract the operator platform from QuantDinger through PK0-PK6
  while retaining Quanthus domain authority.
- **Expert Strategy Layer MVP**: a bounded post-P3 product increment that adds falsifiable expert
  evaluation, one formal two-expert topology, constrained attribution, candidate-detail
  evidence, and an explicit forward-Shadow disposition. It does not reopen P3 acceptance or advance
  P4-P6 gates.

### Platform Kernel stage gates

| Gate | Outcome | Opens when | Blocks expansion when |
| --- | --- | --- | --- |
| PK0 Boundary Freeze | Complete keep/replace/defer/remove and recovery inventory | Fixed upstream backend revision is audited | Web source/license, capability owner, or disposition is unresolved |
| PK1 Minimal Boot | Explicitly composed auth/API/observability kernel | PK0 accepted | A removed route, hook, process, schema, or dependency remains reachable |
| PK2 Structural Slimming | Modular schema/dependencies and billing-free identity | PK1 accepted | Core boot or auth still needs a removed domain |
| PK3 Quanthus Integration | Platform workflows use versioned Quanthus ports | PK2 accepted | Platform bypasses or manufactures domain truth |
| PK4 Operator Web | Source-built retained desktop workflows | PK3 accepted and the Quanthus-owned Web baseline is declared | Navigation or workflows still depend on removed APIs or prebuilt-only assets |
| PK5 Optional Authoring | Draft-only strategy/indicator generation, or not applicable | PK4 accepted plus a separate activation decision | Output can publish, decide, notify, or execute directly |
| PK6 Cutover and Recovery | Deploy, rollback, restoration drill, and old-path closure | PK4 accepted and PK5 accepted/not-applicable | Rollback/restoration fails or an old service remains required |

`config/platform-kernel-gates.v1.json` is the stage authority. LLD 11 owns the detailed module
matrix, entry evidence, stop conditions, and work items. PK1 through PK4 and PK6 are accepted; PK5
optional authoring is not applicable to the first cutover and remains separately activation-gated.

## 4. Release mechanics

The learning-system MVP adopts [blueprint-linked progress](../blueprint-progress.md): each
tracked issue maps to stable module IDs, and meaningful outcomes append scoped evidence before
the generated module view changes. GitHub issue lifecycle, engineering, research quality,
purpose value and consumer admission are independent. CI detects missing reconciliation and
edited history; it never promotes a module or rewrites a frozen gate. The first adoption is
#540–#548, not a migration of all sealed historical phase records.

Each phase has a GitHub milestone and an epic issue. Child issues carry stable ids from
`docs/work-items/index.md`, list dependency ids, and link the governing LLD section. A gate
changes only through configuration, tests, and evidence in one pull request.

The cross-phase Expert Strategy Layer MVP is accepted and its `P3-EXPERT-000` tracking epic is
closed. All seven blocking children have accepted evidence. The formal two-expert topology passed
production and isolated reproduction, bounded 2020-2021 attribution reproduced byte-identically,
and the read-only candidate detail binds the resulting evidence. The terminal lifecycle decision
is `inconclusive`; forward Shadow is disabled because no explicit observation budget was
authorized. This product increment used development data only and did not open arbitration,
permanent-blind, P6 promotion, notification, or execution authority.

## 5. Stop rules

- If M2 drawdown is uncontrolled and attribution does not converge, revisit labels and entry
  threshold mechanisms; do not add a model to conceal the failure.
- If simulation materially diverges from the honest backtest, stop expansion and attribute the
  difference through data, execution, and decision logs.
- Two failed quarterly reveals freeze live-capital expansion.
- No phase creates a return promise.

M2 is accepted by `docs/acceptance/p3-conformance-acceptance-20260729.md`. Deferred human label
review, constrained attribution, dashboards, and concrete shadow production resume only for a
formal semantic proposal; they do not reopen M2 or authorize changes to its fixture.

## 6. Work items

| ID | Work | Acceptance evidence |
| --- | --- | --- |
| META-DOC-001 | Maintain PRD/HLD/LLD and precedence | Docs and policy agree |
| META-TRACE-001 | Maintain requirement-to-issue traceability | No orphan active issue |
| META-GATE-001 | Automate release gate reporting | CI gate artifact |
| P7-LIVE-001 | Define email-advice and actual-operation reporting boundary; defer broker adapter | Paper-compatible manual protocol |
| P7-RECON-001 | Reconcile advice with operator-reported fills, changes, or no action | Daily discrepancy report |
| P7-SCALE-001 | Implement explicit unit expansion decision | Signed reveal decision |
