# LLD 04: Experts and Aggregator

| Field | Value |
| --- | --- |
| Phase | P3 |
| Design status | approved |
| Delivery gate | open |
| Implementation status | `P3-EXP-001` accepted; `P3-AGG-001` accepted; `P3-SHADOW-001` comparison contract accepted; candidate-v1 scoring/action mapping and concrete experts implemented; shadow decision production open |
| Gate source | `config/p2-gates.v1.json` |
| Governing requirements | FR-RULE-002/003/006, FR-AUDIT-001 |

## 1. Goal

Keep all action authority in typed expert and governance contracts. Experts produce opinions
or boundaries; the aggregator applies fixed precedence and records why an action exists.

## 2. Interfaces

### Scoring output

Signal in [-1, 1], confidence in [0, 1], evidence references, expert/rule version, and an
applicability reason. A non-applicable expert returns an explicit abstention.

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

`src/quanthus/aggregator.py` now adds the candidate-v1 continuation of the accepted governance
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
and market constraints, structural exit, concentration, and audit logging. Other price/volume,
trend, leadership, and context experts run shadow-only. Sector, news, and company-quality
experts are disabled until their point-in-time inputs are approved.

`src/quanthus/shadow_divergence.py` now validates that an active/rejected decision and each shadow
decision belong to the same review, then emits a canonical comparison of actions, scores, expert
outputs, vetoes, constraints, reasons, and policy versions. Input-order and nested-mapping order do
not change comparison identity. Every output declares `execution_authorized=false`; the module does
not create or persist decisions, signals, intents, orders, fills, or positions. Evidence:
`docs/acceptance/p3-shadow-divergence-contract-slice-20260728.md`.

This completes only the reusable `P3-SHADOW-001` comparison contract. Candidate-v1 experts and
score/action mapping now exist, but the governed runner does not produce alternative shadow
decisions. Production shadow-decision generation remains open and cannot be inferred from the
active decision stream.

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

## 7. Acceptance

A perfect score cannot bypass a veto. Disabling any non-system expert degrades explicitly and
does not crash. Any decision can answer which experts agreed, which veto or constraint applied,
which evidence was used, and which versions produced the result.
