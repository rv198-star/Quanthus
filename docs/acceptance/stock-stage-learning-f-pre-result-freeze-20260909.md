# P3-STAGE-001-F pre-result freeze — 2026-09-09

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-F` / #243 |
| Evidence status | **PRE-RESULT FROZEN / READY FOR ONE GOVERNED RUN** |
| Candidate | `stock-stage-first-group-3-4-r1-development-v1` |
| Candidate digest | `sha256:d492d12686be5f5ac84f87ba278afd873d9999aadc5fa5dc79a6f5f5985f176c` |
| Qualification config | `config/stage-learning/qualification.v1.json` |
| Qualification digest | `sha256:e235bd53ff7226358dc19eb6b80a08348cfeacf19455dd3bfa37626df43d15ac` |
| Result partition | `2022-2024-arbitration` |
| Permanent blind | `2025-permanent-blind` remains unopened |
| Result-bearing Runs budget | exactly `1` |
| Maximum success claim | `SHADOW_EXTERNAL_SEMANTIC_ACCEPTED` |

## 1. What is frozen before result access

D/E development selection is closed by
`stock-stage-learning-de-v2-development-closeout-20260909.md`. F consumes the exact frozen
candidate without reopening R1 search or R2. The candidate parameters are:

```text
probe_volume_ratio20_min              = 1.0
acceptance_hold_sessions              = 2
acceptance_signed_distance_min        = 0.0
acceptance_window_volume_ratio20_min  = 1.0
```

The signed-distance `0.0` value retains the D/E claim ceiling: `0.0` and `0.0025` were not
identifiable on development evidence; zero is the lower-complexity unchanged tie break, not a
market-truth claim.

The F sample is preregistered at 640 contexts, at most one context per episode:

| Stratum | Count |
| --- | ---: |
| started activity | 200 |
| early held, hold 1/2/3 | 180 |
| near-anchor loss negative | 100 |
| reclaimed ambiguity | 80 |
| mature held | 80 |

The exact sub-strata, deterministic seeds, 64-security pre-2022 cohort construction, 2021 warm-up,
2022-2024 report window, and hard gates are machine-bound in `qualification.v1.json`. Sample
selection cannot read candidate predictions, future settlements, or strategy results.

## 2. Frozen hard gates

A candidate can receive the bounded SHADOW external-semantic disposition only when every gate
passes:

- at least 100 definite references for Stage 3 and at least 100 for Stage 4;
- at least 50 ambiguous references for over-assertion audit;
- unjudgeable reference rate <= 0.10;
- positive-reference coverage >= 0.60;
- ambiguous unique-assertion rate <= 0.10;
- definite-negative false-positive rate = 0;
- each stage precision one-sided 95% Wilson lower bound >= 0.80;
- macro F1 no worse than R0 by more than 0.03;
- zero quantitative evidence-audit vetoes;
- one unique episode for every sampled context.

Only the frozen candidate and R0 are evaluated. No alternate R1 combination, calibration search,
R2 model, threshold repair, future target, or strategy result is admitted.

## 3. Partition authority

`config/p3-gates.v1.json` continues to list both `2022-2024-arbitration` and
`2025-permanent-blind` under general `unopened_partitions`. F is represented as one bounded
exception that binds:

- exact work item `P3-STAGE-001-F`;
- exact qualification path and qualification digest;
- exact candidate digest;
- one result-bearing access budget;
- read-only access;
- no parameter search;
- no permanent-blind access;
- claim ceiling `bounded_shadow_external_semantic_only`.

This exception is not a reusable development-data permission. The RX6800 Stage Worker enforces the
one-run budget with an atomic node-local reservation. A queued F Job owns the reservation; a cancel
or infrastructure/code-preparation failure before qualification execution releases it. Immediately
before the qualification subprocess can access arbitration data, the Worker irreversibly changes
the reservation to `consumed`. From that point, success, semantic failure, process failure, timeout,
or cancellation cannot authorize another F Job. A result-dependent code, sample, threshold, or
candidate change must create a new version and cannot describe the already exposed 2022-2024
partition as untouched.

The consumed marker is independent from the result artifact: a process that fails after access
starts cannot erase its holdout consumption merely by failing to publish a manifest.

The RX6800 node physically contains the full Base through 2026-07-31, so this qualification makes
no false physical-blind claim. Its independence is temporal/use-governance plus explicit access
accounting. 2025 is preserved for a later higher-evidence confirmation need.

## 4. Reference and claim ceiling

F uses the same versioned `SYNTHETIC_EXPERT_CONSENSUS` policy family as D/E on new time-held-out
T-only evidence. That tests temporal generalization of the frozen semantic contract but does not
turn synthetic role-isolated policy into human expert truth. Therefore even a complete hard-gate
PASS can at most produce bounded SHADOW external-semantic evidence.

This F run cannot publish a `QUALIFIED` LabelCard, open daily/as-known production inference, enable
strategy consumption, authorize notifications/orders, or claim human validation. Those remain
separate gates.

## 5. Pre-result readiness

Repository implementation owns a distinct arbitration qualification source adapter rather than
relaxing the accepted development-only B2 `InputPolicy`. It refuses report dates outside
2022-2024 and refuses any 2025 partition construction. The Stage Worker admits one new exact kind:
`quanthus.stage-qualification.v1`, bound to `qualification.v1.json` and the qualification runner,
and its reservation/consume marker blocks duplicate qualification submission before the result is
known.

No 2022-2024 result has been read by this implementation/pre-result freeze work. The next allowed
result-bearing action is one governed Stage Worker Run from the exact published revision that
contains this config, candidate, source guard, runner, tests, and freeze evidence.
