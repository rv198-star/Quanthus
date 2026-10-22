# LLD 08: Validation and Expert Evolution Governance

| Field | Value |
| --- | --- |
| Phase | P6 historical-replay successor; prospective MVP retained; P5 optional research deferred |
| Status | Fixed-snapshot mechanism replay selected; historical PIT/economic acceptance remains separate. Earlier prospective activation is retained history, not current CN02 admission. |
| Governing requirements | FR-GOV-001/002, NFR-004 |
| Detailed design | `08-validation-governance/p6-forward-validation.md` |

## 1. Goal

The Owner's 2026-10-22 current execution direction is the separate
[P6 historical replay successor](08-validation-governance/p6-historical-replay.md),
using existing data and fixed intervals without waiting for future observations.
It preserves the frozen prospective program and all protected partitions;
historical input qualification and actual replay completion remain separate.
Earlier production-activation records are retained program checkpoints,
not evidence of an enabled or historically qualified CN02 run.
The Owner's later [CN02-only runtime decision](../../work-items/cn02-project-migration.md#current-runtime-ownership-cn02-only)
retires OCI from future Quanthus responsibility; it is no longer an execution
target or a pending P6 dependency.

Make rule evolution evidence-driven and make repeated historical tuning visible. Validation
discipline has higher priority than a favorable performance number.

## 2. Partitions

- Development: 2012-2021; iterative work is allowed and logged.
- Arbitration: 2022-2024; revealed on the quarterly schedule, then rolled into development.
- Permanent blind: 2025; reserved for major promotion decisions and never ordinary tuning.
- Simulation: 2026 onward by partition policy, but official prospective evidence begins only after
  an immutable P6 activation boundary and accumulates under frozen behavior.

Reports state which partitions were accessed. A result without arbitration remains a research
result even if it is profitable.

## 3. Change workflow

1. Proposal: cite attribution evidence and the observed failure mode.
2. Written expectation: name cases that should change and controls that should not.
3. Development replay: verify mechanism and side effects.
4. Shadow and paper trial: record frozen assessments/actions and isolated P2 paper outcomes for a
   predeclared observation budget without notification, broker, or live-capital authority.
5. Promote or rollback: apply the predeclared criteria and preserve both versions.

Construction mode permits logged baseline iteration. Once a policy enters shadow or simulation,
parameter and structure quotas apply and every change uses this workflow.

## 4. Scorecards

Scoring experts are evaluated for marginal value, score monotonicity, lead time, and signal
independence. Vetoes use recall, precision, and portfolio-constrained veto cost rather than raw
profit. Constraints use activation rate and matched risk-adjusted outcome. Aggregator reports
compare consensus and disagreement and break results out by market regime.

Losses receive explicit error attribution: wrong expert, missed veto, wrong constraint,
execution assumption, or data-quality event.

## 5. Change budget

After baseline freeze, one expert may receive at most two parameter changes per quarter and the
system at most two structural expert changes per year unless a documented incident forces an
emergency fix. Exceeding the budget downgrades that period's research credibility.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P6-000 | Coordinate the bounded P6 prospective-validation program | META-DOC-001, accepted P3/P4 and Expert Strategy MVP | All eight children accepted |
| P6-GOV-001 | Freeze entry proposal, observation budget, stop rules, and rollback | P6-000 entry authority | `docs/acceptance/p6-gov001-prospective-mvp-entry-20260917.md` |
| P6-DATA-001 | Enforce prospective DWD boundary, access ledger, and correction disposition | P6-GOV-001 plus explicit post-entry owner value review | `docs/acceptance/p6-data001-prospective-boundary-20260917.md` |
| P6-SCORE-001 | Freeze role-specific expert, aggregator, strategy, and paper scorecards | P6-DATA-001, P3-ATTR-001 | `docs/acceptance/p6-score001-forward-scorecards-20260917.md` |
| P6-GOV-002 | Implement the composite Forward Worker and disabled-state lifecycle rehearsal | P6-SCORE-001, PK-WORKER-000 | `docs/acceptance/p6-gov002-forward-worker-20260917.md` |
| P6-PAPER-001 | Implement the isolated P2 paper lane and fixed-unit ledger | P6-GOV-002, P2-ACCEPT-001 | `docs/acceptance/p6-paper001-paper-lane-20260917.md` |
| P6-WEB-001 | Project forward and paper evidence in candidate detail | P6-PAPER-001 | `docs/acceptance/p6-web001-forward-paper-product-view-20260917.md` |
| P6-ACTIVATE-001 | Authorize and run the first official post-activation forward cycle | P6-WEB-001 | `docs/acceptance/p6-activate001-production-activation-status-20260919.md`; signed activation enabled, first legal official-cycle reproduction pending |
| P6-REVIEW-001 | Complete the time-matured simulation review and any authorized reveal | P6-ACTIVATE-001 plus closed budget | GitHub #120; signed review package and closed ledger after the real-calendar budget matures |

## 7. Acceptance

The earlier production activation is retained history; CN02 does not inherit
it. A new CN02 activation and its first legal post-activation prospective cycle
with independent reproduction remain separate engineering gates. Full P6 acceptance
additionally waits for the predeclared real-calendar observation budget and target-maturity tail,
closes and reconciles the paper ledger, records a signed human review, and proves zero notification,
broker, or live-capital authority. A negative or inconclusive result may close P6 honestly;
missing prospective time may not.

## 8. Successor data interface

[Base 2.4 P6 input handoff](08-validation-governance/p6-base24-inputs.md) owns the
opt-in source binding and purpose-specific checks. It does not change the frozen
P6 runtime or confer a new activation.
