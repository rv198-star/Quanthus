# P3-STAGE-001-E learning implementation readiness

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-E` / GitHub #242 |
| Evidence status | implementation ready / execution waits for complete D synthetic consensus |
| Predecessor | `P3-STAGE-001-D` ready for SYNTHETIC_EXPERT_CONSENSUS review |
| Execution authority | development-only R0/R1 against complete synthetic consensus authorized; formal `training_authorized=false` |

## Implemented boundary

The first-group learning substrate is implemented without opening formal qualification training. The implementation enumerates the exact 18 frozen R1 threshold combinations from `config/stage-learning/experiment.v1.json`, retains the frozen R0 baseline, projects one T-only learning observation per frozen D context, and evaluates stage-3/stage-4 predictions only against a checksum-verified resolved reference artifact. Current policy permits that artifact to be `SYNTHETIC_EXPERT_CONSENSUS` for development-only R0/R1; the evidence tier must remain visible and cannot be rewritten as human validation.

The scoring path keeps `definite`, `ambiguous_set`, `coexistence`, and `unjudgeable` roles distinct. Definite references produce per-stage precision/recall/F1 and a one-sided 95% Wilson precision lower bound. Coverage is reported explicitly. A unique prediction on an `ambiguous_set` context is counted as an ambiguity over-assertion rather than silently converted to a correct label. `unjudgeable` rows are not encoded as negatives.

The R1 evaluator rebuilds the D pilot's admitted T-only Structure/Activity inputs once, then evaluates all 18 preregistered threshold combinations on that shared observation panel. R0 and every R1 trial are retained in the trial ledger. Development ranking is explicitly non-qualifying and prefers measured semantic score/coverage with a lower-complexity tie break; it is not a SHADOW/QUALIFIED decision.

## Fail-closed gates

Formal E qualification execution remains closed because `training_authorized=false`, but development-only R0/R1 may run after D publishes all 120 resolved `SYNTHETIC_EXPERT_CONSENSUS` contexts. The supplied D resolution artifact must verify and bind the exact D pilot sample manifest; its provenance tier must be admitted by policy. The learner does not read `settlements.jsonl`, future target results, arbitration data, permanent-blind data, strategy outcomes, or P2 economics.

The D review-resolution tool separately enforces two distinct primary reviewers on every context. Primary agreement forbids an adjudicator. Primary disagreement requires exactly one independent adjudicator covering exactly the disagreement set. Evidence digests must match the frozen T-only context. A complete resolved-reference package is checksum-bound and explicitly remains a development-pilot reference, not a confirmatory test.

## Current disposition

E implementation may remain in the repository and be tested while D synthetic review is incomplete. No result-bearing semantic learning has yet occurred, no R1 winner exists, R2 has not been triggered, and no model/threshold set is qualified. `P3-STAGE-001-E` remains blocked only until D publishes a complete admitted synthetic consensus; after that, development-only R0/R1 may execute while HUMAN_EXPERT_REFERENCE and formal qualification remain closed.
