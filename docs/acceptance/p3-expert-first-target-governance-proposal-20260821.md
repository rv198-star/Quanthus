# P3-EXPERT-003 First Target Governance Proposal

| Field | Value |
| --- | --- |
| Status | exact engineering-proof decision approved; implementation authorized |
| Work item | `P3-EXPERT-003` |
| Decision card | `expert-target.p4-event-first-passage-reuse@1.0.0` |
| Approved semantic digest | `sha256:a67accc8688b2a63ef01afff160666119b72b5c0ec4adc421d266104e76087f5` |
| Result access | authorized only for the 2020-2021 engineering proof |
| Human decision | `https://github.com/rv198-star/Quanthus/issues/148#issuecomment-5371293034` |

This decision was frozen without a new realized-label, class-frequency, score, sample-count, or
DWD result query. The human approval authorizes only the exact engineering proof below. Passing
configuration tests did not supply approval.

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
  `p4-event.price-volume-state-transition@1.0.0` candidate from `2020-01-01` through `2021-12-31`
  assigned to `development`, before T+1 entry or outcome evidence is known;
- eligible-population predicate digest:
  `sha256:a66195a194933ca3b0e3850c7fb515a28b5125eff1b29241d13f953565c9961c`;
- intended first-strategy population: not frozen by this engineering proof; action use remains
  blocked until `P3-STRAT-002` freezes a formal population and obtains matching evaluation evidence;
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
- hypothesis family: one technical expert/target proof that reproducibly produces observations,
  settlements, a closed ledger, and a descriptive scorecard;
- mandatory slices: calendar year, static level-one industry for stratification only, and T-close
  trend regime;
- uncertainty: 2,000 deterministic bootstrap resamples of complete non-overlapping 22-market-session
  time blocks, 95% interval, seed derived from the evaluation-spec digest;
- dependence statement: overlapping event windows, dates, securities, and market shocks are not
  independent rows.

Publication blocks on reference or population drift, PIT leakage, an unclosed sample ledger,
artifact verification failure, or deterministic rerun mismatch. An empty eligible or settled
engineering corpus is `inconclusive`. Coverage, unavailable outcomes, effective block count, and
expert-versus-baseline MAOE remain visible but do not decide engineering acceptance or expert
effectiveness.

## Human approval

The project owner approved the exact decision id, version, semantic digest, date window, and claim
boundary at the linked Issue comment. Approval authorizes only development-partition resolver,
expert, and scorecard implementation through the accepted Worker. It does not authorize
arbitration, permanent-blind, an expert-effectiveness claim, portfolio-value claims, formal
strategy activation, Shadow activation, notification, or execution.

Any change to the reused target references, population, ordinal order, metric, baseline, hypothesis
family, slices, uncertainty method, or stop conditions changes the semantic digest and requires a
new human decision before result access.
