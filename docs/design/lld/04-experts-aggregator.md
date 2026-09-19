# LLD 04: Experts and Aggregator

| Field | Value |
| --- | --- |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | `P3-EXP-001` accepted; `P3-AGG-001` accepted; `P3-SHADOW-001` comparison contract accepted; fixture v1 remains frozen; the additive Expert Strategy Layer MVP is accepted with one formal two-expert topology, formal attribution, read-only evidence, and an inconclusive lifecycle decision with Shadow disabled |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-RULE-002/003/006, FR-AUDIT-001 |

## 1. Goal

Keep all action authority in typed expert and governance contracts. Experts produce assessments or
boundaries; the aggregator applies fixed precedence and records why an action exists. A versioned,
composable expert topology is the primary strategy research and decision-expression unit, while the
complete strategy topology remains the final unit of economic acceptance.

### Detailed design

| Child | Ownership | Delivery state |
| --- | --- | --- |
| `04-experts-aggregator/expert-strategy-layer.md` | Additive v2 evaluation artifacts, assessment/policy separation, scorecards, lifecycle, Worker boundary, formal-strategy MVP, stop conditions, and routing to work-item child designs | MVP accepted; all seven child work items closed with immutable evidence |

## 2. Interfaces

### Scoring output

Signal in [-1, 1], confidence in [0, 1], evidence references, expert/rule version, and an
applicability reason. A non-applicable expert returns an explicit abstention.

These are accepted v1 action-policy semantics. `confidence` is not a calibrated forecast
probability. New forecast, settlement, scorecard, and policy-mapping identities are additive v2
sidecar artifacts owned by `04-experts-aggregator/expert-strategy-layer.md`; accepted v1 fields and
Run digests are never reinterpreted or rewritten.

### Constraint output

Minimum/maximum position, stop rule, hold rule, scope, reason, and version. Constraint
intersection must either produce a valid range or a typed conflict that fails closed.

### Veto output

Allow flag, reason code, required action, evidence, and version. Required action is one of
none, block-new, reduce-only, or force-liquidate.

`src/quanthus/expert_protocols.py` now owns the typed expert context and the scoring, constraint,
and veto output contracts. Each output carries an expert id/version, applicability state,
applicability and result reason codes, and evidence references that must resolve to the current
snapshot, feature lineage, or LabelResult. Abstention is structurally distinct from a neutral
opinion: it carries no score, bounds, allow flag, or required action. Applicable scores enforce
`[-1, 1]` signal and `[0, 1]` confidence; constraints enforce complete non-conflicting position
bounds and explicit stop/hold rules; vetoes enforce agreement between `allow` and required action.
The runner rejects output identity drift and evidence outside the supplied PIT context. Evidence:
`docs/acceptance/p3-expert-protocol-slice-20260728.md`.

This slice implements no concrete expert and assigns no decision authority. Expert meanings,
activation modes, and vertical-slice rules remain `P3-EXP-002`; precedence and aggregation remain
`P3-AGG-001/002`.

## 3. Aggregation sequence

1. Evaluate system and strategy vetoes.
2. Select the strictest required action: force-liquidate, reduce-only, block-new.
3. If no terminating veto applies, intersect all constraints.
4. Compute equal-weight confidence-adjusted score in the first version.
5. Map score to no-op, trial entry, add, reduce, or exit.
6. Apply final position and concentration bounds.
7. Persist all inputs, intermediate values, and final reasons.

This ordering is code-level policy and cannot be changed through a runtime knob.

`src/quanthus/aggregator.py` now implements steps 1-3 as a deterministic governance boundary.
Veto inputs are ordered system before strategy and then by expert id/version; force-liquidate,
reduce-only, block-new, and none have fixed descending precedence. At least one applicable system
veto is mandatory, so an absent or entirely abstained system layer fails closed. Any actionable
veto terminates before constraint processing. Otherwise applicable constraints intersect as
`max(minimum)` and `min(maximum)`, retaining every stop/hold rule and source-output digest; an empty
intersection or a fully abstained constraint set is a typed failure rather than an implicit bound.
Inputs and output are rebound to one ExpertContext, canonically ordered, and deterministically
serialized. Evidence: `docs/acceptance/p3-governance-aggregation-slice-20260728.md`.

This slice does not compute a score or map governance output to a position action. Those remain
`P3-AGG-002`, after a versioned action policy is frozen.

### Governed candidate action mapping

`src/quanthus/aggregator.py` adds the capability-fixture continuation of the accepted governance
boundary. Applicable scoring outputs are ordered by expert identity, converted to an equal-weight
confidence-adjusted aggregate, and mapped through a versioned minimum-entry score. A system or
strategy block-new veto still changes a proposed Trial entry to no action; score cannot override
the governance result.

`src/quanthus/governed_strategy.py` supplies two vetoes, one Trial-position constraint, and three
concrete scoring experts for the frozen candidate. The complete chain materializes one P2
`DecisionEvent` for every bounded candidate and held-position review. The initial development run
produced 52,423 decisions, of which 461 accepted decisions resolve to exactly 461 P2 fills and the
remaining 51,962 are non-ordering rejections or holds. Evidence:
`docs/acceptance/p3-governed-strategy-development-20260729.md`.

## 4. Release modes

The vertical slice enables tradability/funding vetoes, a basic candidate rule, extension-risk
and market constraints, structural exit, concentration, and audit logging. Later price/volume,
trend, leadership, and context proposals must begin in shadow-only mode and carry the evaluation
contract required by the Expert Strategy Layer child. Sector, news, and company-quality experts
are disabled until their point-in-time inputs are approved.

`src/quanthus/shadow_divergence.py` now validates that an active/rejected decision and each shadow
decision belong to the same review, then emits a canonical comparison of actions, scores, expert
outputs, vetoes, constraints, reasons, and policy versions. Input-order and nested-mapping order do
not change comparison identity. Every output declares `execution_authorized=false`; the module does
not create or persist decisions, signals, intents, orders, fills, or positions. Evidence:
`docs/acceptance/p3-shadow-divergence-contract-slice-20260728.md`.

This completes the reusable `P3-SHADOW-001` comparison contract. The frozen fixture has no genuine
alternative expert proposal, so generating synthetic shadow decisions would add volume without a
falsifiable comparison. Concrete shadow production resumes under the later proposal/promotion
workflow when a formal alternative exists; it cannot be inferred from the active decision stream.

## 5. Audit record

Each decision record contains event/candidate id, unit, lifecycle state, snapshot, feature and
label versions, every expert output, veto evaluation order, merged constraints, aggregate
score, proposed/final action, rejected action when applicable, and reason codes.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P3-EXP-001 | Define typed expert protocols and abstention | Labels | Contract tests |
| P3-AGG-001 | Implement veto precedence and constraint merge | Expert protocol | Conflict/veto golden cases |
| P3-AGG-002 | Implement score and action mapping with versioning | P3-AGG-001 | Boundary fixtures |
| P3-SHADOW-001 | Implement shadow execution and divergence log | Audit schema | No action-path side effects |
| P3-EXP-002 | Add vertical-slice rule experts | Aggregator | End-to-end replay |
| P3-EXPERT-000 | Deliver the bounded Expert Strategy Layer MVP | META-DOC-001 | All seven blocking children close, one lifecycle decision and Shadow disposition are recorded, and sealed partitions remain unopened |
| P3-EXPERT-001 | Implement additive expert evaluation protocol v2 | P3-EXP-001, P3-AGG-001 | [Accepted protocol and byte-stable v1 compatibility evidence](https://github.com/rv198-star/Quanthus/blob/main/docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md) |
| P3-EXPERT-002 | Run expert evaluation through the PostgreSQL Worker and immutable artifacts | P3-EXPERT-001, PK-WORKER-000 | [Accepted Worker, recovery, fencing, and deterministic publication evidence](https://github.com/rv198-star/Quanthus/blob/main/docs/acceptance/p3-expert-evaluation-worker-20260820.md) |
| P3-EXPERT-003 | Freeze the first target/resolver and implement one market-data technical expert | P3-EXPERT-001, P3-EXPERT-002, P3-FEAT-001, P3-REPLAY-001, P4-LABEL-001 | [Accepted production Worker Run, byte-identical reproduction, leakage checks, ledger, and scorecard](https://github.com/rv198-star/Quanthus/blob/main/docs/acceptance/p3-expert-first-target-production-20260821.md) |
| P3-STRAT-002 | Freeze and implement the first formal two-expert strategy topology | P3-EXPERT-002, P3-EXPERT-003, P3-POS-003 | Approved strategy card, executable two-expert binding, expected changes, controls, and legal interventions |
| P3-STRAT-003 | Validate multi-expert incremental value on fixed recent history | P3-STRAT-002, P3-ATTR-001 | Parent closes only from A-E evidence under `docs/work-items/p3-strat-003.md`; P6 remains parallel |
| P3-STRAT-003-A | Freeze research request, four-way factorial, data authority and result rules | P3-STRAT-002, P3-ATTR-001 | `config/research/p3-strat-003/request.v1.json` plus `docs/acceptance/p3-strat-003-a-preregistration-20260920.md` |
| P3-STRAT-003-B | Qualify immutable 2025-2026 inputs, PIT boundary and run isolation | P3-STRAT-003-A | Immutable input manifest, coverage/exception ledger and zero strategy-result production |
| P3-STRAT-003-C | Implement B/T/R/TR research control and exact four-variant runner | P3-STRAT-003-A | [Accepted result-blind four-variant engineering, exact-input resume, recent-history partition gate and P6 isolation](../acceptance/p3-strat-003-c-engineering-20260920.md) |
| P3-STRAT-003-D | Execute primary recent-history batch and independent cold reproduction | P3-STRAT-003-B, P3-STRAT-003-C | Two exact composite batches, complete P2 evidence, preregistered report and byte/semantic reproduction |
| P3-STRAT-003-E | Review evidence and select at most one bounded successor question | P3-STRAT-003-D | Read-only lifecycle decision that accepts support, harm, no support or inconclusive without automatic promotion |
| P3-EXPERT-004 | Add read-only expert evidence and research decisions to candidate detail | P3-ATTR-001, P3-EXPERT-002 | Digest-resolved API/Web contracts with no calculation, approval, or execution authority |
| P3-EXPERT-005 | Accept the development MVP and resolve forward-Shadow disposition | P3-EXPERT-004, P3-ATTR-001, P3-SHADOW-001 | Deterministic acceptance packet, lifecycle decision, enabled/disabled Shadow proof, cost report, rollback, and zero sealed-partition access |
| P3-EXPERT-006 | Consume existing Stage10/11 semantic surface before vocabulary expansion | accepted Expert Strategy Layer MVP; closed stock-stage phase 1 | closed `SUFFICIENT_FOR_BOUNDED_CONSUMPTION_NO_STAGE_REOPEN`; 0/40 semantic errors, 40/40 bounded-sufficient, 0 unsafe, no recurring Stage gap; stock-stage phase 1 remains closed; `expert-strategy-layer/stage-semantic-consumption.md` |

## 7. Acceptance

A perfect score cannot bypass a veto. Disabling any non-system expert degrades explicitly and
does not crash. Any decision can answer which experts agreed, which veto or constraint applied,
which evidence was used, and which versions produced the result.

The Expert Strategy Layer MVP additionally requires every declared assessment to resolve to a
falsifiable evaluation contract, every action-changing policy to disclose its complete-strategy
economic effect, and at least one evidence-backed lifecycle decision. It does not authorize a
cross-target ranking or automated expert evolution.
