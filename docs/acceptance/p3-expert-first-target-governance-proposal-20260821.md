# P3-EXPERT-003 First Target Governance Proposal

| Field | Value |
| --- | --- |
| Status | outcome-blind proposal; awaiting exact human approval; not acceptance evidence |
| Work item | `P3-EXPERT-003` |
| Decision card | `expert-target.p4-event-first-passage-reuse@1.0.0` |
| Proposed semantic digest | `sha256:b15ce3f844e31c485ee982449276324d1c3c53a3f4ee41e6d60468a08c1e6c40` |
| Result access | blocked |
| Governing issue | `https://github.com/rv198-star/Quanthus/issues/148` |

This record contains no realized labels, class frequencies, score, sample count, or DWD result
query. It records the exact decision proposed before the first result-bearing expert evaluation.
Passing configuration tests does not approve the proposal.

## Decision recommendation

Reuse the accepted `P4-LABEL-001` cards instead of creating an expert-owned target:

- outcome: `p4-event.outcome-first-passage@1.0.0`, digest
  `sha256:b623a647116a69a0d6559bd265b51bdd3ac0654826cf54ba38b87d819ea165ca`;
- structural break: `p4-event.structural-break-trend@1.0.0`, digest
  `sha256:1294e0550361bafe9ecfbcfddb32a7bb1ff378332c4fb173db662f95debad9fc`.

The subject, T-close candidate anchor, actual executable T+1 entry anchor, 20-session path,
corporate-action semantics, failure-wins ordering, suspension/delisting handling, missing evidence,
and partition-tail censoring already match the first trend-stability assessment. Reuse leaves one
factual referee and prevents an expert-specific copy of the +15%/-7% thresholds from drifting.

The rejected alternatives are outcome-blind design alternatives, not tested losers:

| Alternative | Reason not selected |
| --- | --- |
| Simple terminal return | Discards the already approved first-passage and structural-break path semantics and creates a second answer for the same P4 event. |
| Benchmark-relative return | Requires a new benchmark target, missing-data policy, and economic claim that the first technical proof does not need. |
| New return/drawdown target | Duplicates the path-aware role already covered by the approved P4 favorable, adverse, and structural-break rules. |

## Frozen population and target

- subject: accepted P4 opportunity, keyed by immutable `event_id`;
- eligible population: every retained
  `p4-event.price-volume-state-transition@1.0.0` candidate assigned to `development`, before T+1
  entry or outcome evidence is known;
- eligible-population predicate digest:
  `sha256:e3fe6155dc33169b9eba7cfcbd6a669d50737902d4a23e9005bfc9ce71ec1308`;
- intended first-strategy population: the same predicate as a mandatory predeclared slice;
- missing T-close expert inputs: explicit abstention, never retrospective imputation;
- target order: `failure=0`, `neutral=1`, `positive=2`;
- `not_executable`: retained in the sample ledger and excluded from target scoring;
- observation anchor: candidate T close, using only evidence visible by that cutoff;
- target horizon and settlement: inherited exclusively from the approved P4 card references.

The assessment output is ordinal: `fragile < neutral < stable`, mapped in the same order to the
three target states for scoring. This mapping does not make the assessment a target label or grant
an action.

## Frozen evaluation plan

- primary metric: mean absolute ordinal error in target-order steps, lower is better;
- naive baseline: always predict `neutral`, fixed without consulting class frequencies;
- hypothesis family: one confirmatory expert/target comparison; expert MAOE is lower than the
  always-neutral MAOE;
- mandatory slices: calendar year, static level-one industry for stratification only, and T-close
  trend regime;
- uncertainty: 2,000 deterministic bootstrap resamples of complete non-overlapping 22-market-session
  time blocks, 95% interval, seed derived from the evaluation-spec digest;
- dependence statement: overlapping event windows, dates, securities, and market shocks are not
  independent rows.

Publication blocks on reference or population drift, an unclosed sample ledger, or deterministic
rerun mismatch. The assessment is `inconclusive` when coverage is below 80%, unavailable outcomes
exceed 5% of applicable observations, fewer than 24 settled time blocks remain, or the upper 95%
block-bootstrap bound of `expert MAOE - baseline MAOE` is not below zero. An inconclusive result
does not authorize threshold or population repair in place.

## Human decision gate

Approval must name the exact decision id, version, and semantic digest above. Approval authorizes
only development-partition resolver, expert, and scorecard implementation through the accepted
Worker. It does not authorize arbitration, permanent-blind, portfolio-value claims, formal strategy
activation, Shadow activation, notification, or execution.

Any change to the reused target references, population, ordinal order, metric, baseline, hypothesis
family, slices, uncertainty method, or stop conditions changes the semantic digest and requires a
new human decision before result access.
