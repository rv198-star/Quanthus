# LLD 04: Experts and Aggregator

| Field | Value |
| --- | --- |
| Phase | P3 |
| Status | Vertical-slice baseline |
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

## 3. Aggregation sequence

1. Evaluate system and strategy vetoes.
2. Select the strictest required action: force-liquidate, reduce-only, block-new.
3. If no terminating veto applies, intersect all constraints.
4. Compute equal-weight confidence-adjusted score in the first version.
5. Map score to no-op, trial entry, add, reduce, or exit.
6. Apply final position and concentration bounds.
7. Persist all inputs, intermediate values, and final reasons.

This ordering is code-level policy and cannot be changed through a runtime knob.

## 4. Release modes

The vertical slice enables tradability/funding vetoes, a basic candidate rule, extension-risk
and market constraints, structural exit, concentration, and audit logging. Other price/volume,
trend, leadership, and context experts run shadow-only. Sector, news, and company-quality
experts are disabled until their point-in-time inputs are approved.

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
