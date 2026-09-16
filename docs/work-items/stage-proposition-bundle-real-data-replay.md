# P3-STAGE-029 — Real-History Stage Proposition Bundle Replay

Design status: approved validation scope  
Delivery gate: accepted-conditional

Parent: `stage-layered-proposition-bundle.md`  
Governing contract: `../../config/stage-learning/stage-proposition-bundle-real-data-replay.v1.json`  
Machine result: `../../config/stage-learning/stage-proposition-bundle-real-data-replay-result.v1.json`  
Acceptance: `../acceptance/stage-proposition-bundle-real-data-replay-20260916.md`  
Four-party review: `../reviews/stage-p029-real-data-bundle-four-party-review-20260916.md`

## Goal

Test one bounded post-P028 question on real historical observations: whether the accepted layered
Stage10/11 development-shadow representation preserves the still-active Stage10 structural-failure
context when the nested Stage11 rebound condition is true, compared with an explicitly lossy
single-code representation.

P029 is a representation and consumer-semantics validation. It is not a predictive or economic
experiment.

## Frozen scope

P029 consumes only the two previously accepted immutable P3-STRUCT-005/F9 real-DWD-derived artifacts.
It includes every formal Stage10-active observation in the accepted projection: ten rows total, of
which four are Stage10-only and six contain Stage10+11. There is no sampling and no result-driven
selection.

The three views over the same observation identity are:

1. **Facts-only** — accepted continuous Support Evolution facts and references, no Stage proposition;
2. **comparison-only single code** — `11` when Stage11 is true, otherwise `10`; this is not claimed to
   be a historical or production policy;
3. **layered development-shadow Bundle** — P028 context/condition assertions under the frozen
   evidence and authority contract.

## Result

The preregistered target is explicit parent-context retention under a nested rebound.

- explicit proposition opportunities: `16`;
- comparison-only single code retains `10`, with Stage10 parent omitted on `6/6` nested rows;
- layered Bundle retains `16/16`, with Stage10 parent explicit on `6/6` nested rows;
- layered parent elision: `0`;
- development-shadow semantic mismatch: `0`;
- non-null `primary_stage`: `0`;
- market data reads during replay: `0`;
- DWD provider opens during replay: `0`.

Result:

`PASS_LAYERED_REPRESENTATION_PRESERVES_EXPLICIT_PARENT_AND_NESTED_SEMANTICS`.

This proves explicit representation fidelity only. A hierarchy-aware consumer may infer Stage10 from
Stage11 even when the comparison view does not display the parent, so P029 does not claim that layered
output contains new independent market information.

## Knowledge boundary

The accepted source artifacts use `knowledge_view=retrospective` /
`retrospective_dwd_record_view`. Therefore:

`STRICT_PIT_GATE = BLOCKED_BY_RETROSPECTIVE_SOURCE_RECORD_VIEW`.

P029 does not establish that the underlying facts or propositions were available as-known at each
historical observation time. It also does not establish prediction, economics or out-of-sample value.

## Four-party phase review

Independent short-term, fund/risk, quant and data-governance reviews all selected one common next
validation dependency: strict PIT/as-known Stage10/11 lineage/replay. Their recommendation does not
open a successor and grants no additional authority.

## Closed boundaries

P029 did not:

- read new market/DWD data;
- access future outcomes;
- touch consumed 2022-2024 arbitration data or preserved 2025 data;
- change Stage10/11 predicates, thresholds, evidence grades or use authority;
- expand the vocabulary or reopen parked Stage routes;
- create a primary-stage policy;
- authorize production expert, strategy or trading consumption.

## Closeout

P029 is closed at the requested phase-review boundary. No P030 or PIT successor is automatically
opened. A separate decision is required before any strict PIT/as-known implementation work.
