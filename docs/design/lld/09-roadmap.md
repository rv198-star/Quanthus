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
| P5 | Research scoring layer | Beats rule baseline and passes promotion gate | P4 |
| P6 | Frozen paper operation and two-window notification rehearsal | Distribution aligns with honest backtest; no live-fill claims | P3 baseline |
| P7 | Operator-assisted small-capital operation | Every advice closes with an accepted actual/no-action report and reconciliation | P6 |

P5 research may run beside P6 frozen simulation, but a development build cannot alter the
simulation policy.

Platform Kernel is a separate engineering track rather than P8. It may proceed beside P4 when its
own predecessor gate passes, but it cannot weaken a P1-P7 domain gate or redefine Quanthus data,
replay, strategy, decision, or execution authority.

Current state: P2 remains conditionally accepted for the active daily research profile by
`config/p2-gates.v1.json`, and P3 is accepted by `config/p3-gates.v1.json`. Its strategy-neutral
replay, rule contracts, position/risk path, audit funnel, and one frozen capability fixture prove
the multi-year closed loop. That fixture is not a formal strategy lineage and cannot be tuned or
promoted. P4 event-library implementation is open; target two-window activation and forward
notification retain their separate blockers.

## 3. Milestones

- **M0**: repository baseline, versioned P0 configuration, documentation, tests, and issue map.
- **M1**: accepted data foundation and simple baseline strategy through the trusted engine.
- **M2**: rule-only vertical slice over multiple years with complete decision trace.
- **M3**: first event differentiation report and proposal-to-shadow-to-decision loop.
- **M4**: frozen paper operation and idempotent signal notification begin and continuously record
  arbitration evidence without live-fill claims.
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
matrix, entry evidence, stop conditions, and work items. PK0 was accepted on 2026-08-12 with the
six default boundary decisions; PK1 is now open and begins with `PK-COMPOSE-001` only.

## 4. Release mechanics

Each phase has a GitHub milestone and an epic issue. Child issues carry stable ids from
`docs/work-items/index.md`, list dependency ids, and link the governing LLD section. A gate
changes only through configuration, tests, and evidence in one pull request.

The cross-phase Expert Strategy Layer MVP has its own GitHub milestone and `P3-EXPERT-000` epic.
`P3-EXPERT-001`, `P3-EXPERT-002`, and `P3-EXPERT-003` are accepted. `P3-STRAT-002` now has a
human-approved outcome-blind exact strategy proposal and is implementing its bounded Sentinel;
later children remain blocked by explicit predecessors, and the existing
`P3-ATTR-001` gate remains deferred until
the formal strategy and predeclared questions exist. This product increment may use development
data but cannot open arbitration, permanent-blind, P6 promotion, notification, or execution
authority.

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
