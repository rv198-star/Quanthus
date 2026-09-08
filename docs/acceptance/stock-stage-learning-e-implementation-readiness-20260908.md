# P3-STAGE-001-E learning implementation readiness

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-E` / GitHub #242 |
| Evidence status | implementation ready / execution blocked |
| Predecessor | `P3-STAGE-001-D` remains blocked on independent semantic review |
| Execution authority | `training_authorized=false` |

## Implemented boundary

The first-group learning substrate is implemented without opening semantic training. The implementation enumerates the exact 18 frozen R1 threshold combinations from `config/stage-learning/experiment.v1.json`, retains the frozen R0 baseline, projects one T-only learning observation per frozen D context, and evaluates stage-3/stage-4 predictions only against a checksum-verified independent reference-resolution artifact.

The scoring path keeps `definite`, `ambiguous_set`, `coexistence`, and `unjudgeable` roles distinct. Definite references produce per-stage precision/recall/F1 and a one-sided 95% Wilson precision lower bound. Coverage is reported explicitly. A unique prediction on an `ambiguous_set` context is counted as an ambiguity over-assertion rather than silently converted to a correct label. `unjudgeable` rows are not encoded as negatives.

The R1 evaluator rebuilds the D pilot's admitted T-only Structure/Activity inputs once, then evaluates all 18 preregistered threshold combinations on that shared observation panel. R0 and every R1 trial are retained in the trial ledger. Development ranking is explicitly non-qualifying and prefers measured semantic score/coverage with a lower-complexity tie break; it is not a SHADOW/QUALIFIED decision.

## Fail-closed gates

Real E execution currently refuses to start because `training_authorized=false`. Opening that flag alone is still insufficient: the supplied D resolution artifact must verify, must contain all 120 independently resolved contexts, and must bind the exact D pilot sample manifest. The learner does not read `settlements.jsonl`, future target results, arbitration data, permanent-blind data, strategy outcomes, or P2 economics.

The D review-resolution tool separately enforces two distinct primary reviewers on every context. Primary agreement forbids an adjudicator. Primary disagreement requires exactly one independent adjudicator covering exactly the disagreement set. Evidence digests must match the frozen T-only context. A complete resolved-reference package is checksum-bound and explicitly remains a development-pilot reference, not a confirmatory test.

## Current disposition

E implementation may remain in the repository and be tested while D is unresolved. No result-bearing semantic training has occurred, no R1 winner exists, R2 has not been triggered, and no model/threshold set is qualified. `P3-STAGE-001-E` remains blocked on D reference completion and the subsequent explicit opening of development training execution.
