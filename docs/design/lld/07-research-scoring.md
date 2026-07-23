# LLD 07: Research Scoring Layer

| Field | Value |
| --- | --- |
| Phase | P5 |
| Status | Deferred until rule baselines and target cards exist |
| Governing requirements | FR-ML-001 through FR-ML-003 |

## 1. Role

The scoring layer is a research instrument. It produces feature importance, calibrated state
assessments, and hypotheses for expert-rule proposals. It does not emit an order or bypass the
aggregator. Remaining in research-only mode is an acceptable permanent outcome.

## 2. Tasks

Potential tasks are next-session improvement/deterioration, trend stability, holding
necessity, structural break, and event three-class outcome. Each task requires an independent,
versioned target card defining horizon, inputs, exclusions, class precedence, and evaluation
metric before training begins. Holding necessity must not be defined by the same policy it is
supposed to evaluate.

## 3. Model ladder

1. Frozen rule baseline.
2. Logistic or simple linear baseline.
3. GBDT with a small reviewed feature set.
4. Sequence models only after event count exceeds the approved threshold and GBDT plateaus.

Feature count is capped relative to independent event count. Raw minute sequences are not
accepted as first-stage inputs.

## 4. Validation

- Temporal event-level folds with purge/embargo; no random slice split.
- Calibration fitted only on training/calibration partitions.
- Metrics include class-aware discrimination, calibration, and regime buckets.
- Confidence intervals use event or temporal block resampling.
- Perturbations include feature noise, one-session delay, and rule-threshold variation.
- All experiment attempts, features, hyperparameters, and data manifests are recorded.

## 5. Promotion

A model may replace only a state-assessment rule and still enters through an expert contract.
Promotion requires out-of-sample non-inferiority to the rule system, no catastrophic regime
bucket, perturbation stability, calibrated output where called probability, and per-decision
feature evidence. Arbitration or blind failure means rollback, not retuning on that period.

## 6. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P5-TARGET-001 | Define independent target cards and metrics | P4 dataset | Target leakage review |
| P5-BASE-001 | Build rule and logistic baseline scoreboard | Target cards | Versioned baseline report |
| P5-GBDT-001 | Train constrained GBDT research pipeline | Baselines | Temporal CV and calibration |
| P5-PROMOTE-001 | Implement model promotion/shadow gate | Governance | Promote/rollback rehearsal |

## 7. Acceptance

The rule baseline exists for every task, a feature-importance report is reproducible, at least
one model insight completes proposal-to-rule-shadow review, and no model enters production
without all promotion evidence.
