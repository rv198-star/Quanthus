# P3-STAGE-010-D1 — Fresh 2019 Development Prepare Engineering Acceptance

## Disposition

```text
D1 IMPLEMENTATION: ACCEPTED
AUTHORIZED NEXT ACTION: ONE D1 RESULT-BEARING JOB ONLY
AUTHORIZED MARKET READ: 2018 WARM-UP + 2019 DEVELOPMENT ONLY
PRESERVED 2021 EVALUATION: STILL UNREAD / NOT AUTHORIZED
PARAMETER SEARCH: NOT AUTHORIZED IN D1
2022-2025: SEALED
```

## Implemented scope

D1 is implemented by:

- `config/stage-learning/positive-instability-quantitative-development-screen.v1.json`;
- `config/stage-learning/positive-instability-quantitative-development-execution.v1.json`;
- `src/quanthus/stage_learning/positive_instability_quantitative_development.py`;
- `src/quanthus/stage_learning/positive_instability_quantitative_development_prepare.py`;
- the fixed Stage Worker kind `quanthus.stage-positive-instability-quantitative-d1-prepare.v1`;
- candidate-blind Primary review transport in `src/quanthus/stage_learning/worker.py`.

## Source isolation

The prepare path verifies the exact accepted Stage-007, Stage-008 and Stage-009 artifacts before it
constructs a fourth fresh economic-identity pool. The excluded identity closure contains:

- Stage-007 excluded identities and its full exposed pool;
- the entire Stage-008 fresh pool;
- the entire Stage-009 512-identity fresh pool, including all identities in its still-unread 2021
  evaluation plan.

The P010 development pool therefore cannot overlap the preserved evaluation identity plan.

## D1 data boundary

The only market-data read authorized by this checkpoint is the preregistered D1 scope:

```text
warm-up start:       2018-09-01
development start:   2019-01-01
development end:     2019-12-31
```

D1 may build unlabeled Structure Facts and a nine-candidate threshold basis from the 256 development-
capacity identities. It may select at most 160 morphology-blind review cases by identity hash,
assigned quarter and date hash only. No result-dependent backfill is allowed.

## Preserved evaluation boundary

D1 loads the previously frozen Stage-009 evaluation-plan artifact only to verify and copy its exact
identity/quarter binding. It does **not** read 2021 market rows. The output records the exact preserved
plan digest and `preserved_evaluation_data_read=false`.

## Candidate blindness

The D1 review transport exposes only:

- the T-time Primary case;
- the two directional path Facts;
- the last 21 raw T-time path rows.

It explicitly excludes threshold basis, candidate outputs, recent derived Facts, future rows and the
preserved 2021 evaluation payload. Reference opinions therefore remain sealable before D2 candidate
scoring.

## One-shot runtime contract

The admitted Stage Worker kind has a dedicated budget marker. Same-revision submission is idempotent;
a different revision is rejected as having no retry/replacement authority. D1 is the only runtime Job
authorized by this checkpoint.

## Verification

Focused D1/Worker verification:

```text
41 passed
Ruff: PASS
git diff --check: PASS
```

Full repository gate before authority activation:

```text
2219 passed / 7 skipped
Quanthus doctor: OK
compileall: PASS
planning sync: PASS
```

## Claim ceiling

This engineering acceptance does not freeze a candidate, score references, authorize parameter search,
open the 2021 evaluation, change Stage 2, retune the relocation guard, or authorize official labels,
strategy or trading.
