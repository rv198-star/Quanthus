# P3-STAGE-008-D Fresh Development Screen Preregistration

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-008-D |
| Precondition | P3-STAGE-008-C closed `PASS_DIAGNOSTIC_CONFORMANCE_ONLY` |
| Governing design | `../design/stage-directional-boundary-repair.md` |
| Machine contract | `../../config/stage-learning/directional-boundary-development-screen.v1.json` |
| Contract digest | `sha256:fff38395c6466db0305287125d66bc5ba2f5a4ea18d6eb7ee97dd13f0047f963` |
| D1 implementation | `../../src/quanthus/stage_learning/directional_boundary_calibration_prepare.py` |
| Candidate/evaluation logic | `../../src/quanthus/stage_learning/directional_boundary_development.py` |
| State | PREREGISTERED / D1 READY / NO D DATA READ YET |

## 1. Why D is now allowed

A fixed design was completed in 008-A, the two missing threshold-free Facts and primary-first review
protocol were implemented in 008-B, and 008-C reproduced the previously frozen diagnostic groups and
medians exactly without learning a threshold. The owner then explicitly approved continuing through
C and D sequentially.

D is therefore authorized as one bounded **fresh development screen**, not as a blind holdout or
formal market qualification. It remains same-origin simulated reference research and can at most
produce `PROMISING_EXPLORATORY_REPAIR`.

## 2. Fresh identity boundary

D reconstructs the same 3,917-security candidate population that Stage-007 used, but it excludes:

- all 515 economic identities already excluded by Stage-007; and
- the entire 512-identity Stage-007 isolated pool, not merely the 160 cases that were reviewed.

From the remaining identities, a deterministic hash freezes a new 512-identity pool. The first 256
ranked identities are calibration capacity and the next 256 are evaluation capacity. The two panels
have zero economic-identity overlap. Each selected identity may contribute at most one case.

This is stricter than merely changing dates: the new D screen cannot reuse any Stage-007 pool
identity as a fresh case.

## 3. Time and visibility split

The only authorized source years remain 2019-2021:

- 2019 is warmup/history only;
- 2020 owns calibration;
- 2021 owns evaluation.

D1 may read 2019-2020 market data for **calibration identities only**. It also freezes the 96
future evaluation identities and their assigned quarters, but does not read their 2021 market bars.
Evaluation market data stays unavailable until D2 has sealed calibration references and frozen one
candidate.

2022-2024 and 2025 remain closed. Future outcomes, arbitration, permanent blind partitions and
trading outputs remain forbidden.

## 4. Calibration sample and candidate grid

Calibration targets 64 cases:

- 16 hash-selected coverage cases, four per quarter;
- 32 cases enriched only by the old frozen Stage-0 instability seed;
- 16 order-control cases using the unchanged Stage-2 shape.

There is no cross-stratum backfill or result-dependent replacement. Any shortfall remains evidence.

The only candidate search is a 27-point relocation guard:

```text
abs_window_return20 >= R
AND range_traversal20 >= T
AND counter_direction_excursion_share20 <= C
```

where:

```text
R ∈ {0.10, 0.20, 0.30}
T ∈ {0.50, 0.70, 0.90}
C ∈ {0.20, 0.40, 0.60}
```

A zero-net path cannot satisfy the guard. Candidate Stage 0 is the old positive instability seed
AND NOT the relocation guard. No weighted score is permitted. Stage-2 thresholds are frozen and
cannot participate in the search.

Calibration references must be sealed before the 27 candidates are scored. If no candidate meets the
preregistered calibration gates, D stops `REVISE_OR_PARK_NO_EVALUATION`; evaluation is never opened.

## 5. Evaluation sample and hard gates

Only after a candidate is frozen may D3 read 2021 data for the 96 preplanned evaluation identities.
The evaluation sample uses no morphology: 24 identities are assigned to each quarter in hash order,
and each contributes the lowest-hash primary-available date inside its assigned quarter. There is no
identity or quarter backfill.

The frozen candidate must meet all of the following on evaluation:

- at least 80 complete cases;
- at least 12 definite Stage-0 positives and 30 definite Stage-0 negatives;
- at least 10 Stage-0 predictions;
- precision >= 0.80;
- positive-reference coverage >= 0.55;
- definite-negative FPR <= 0.10;
- precision not worse than old R0;
- FPR not worse than old R0;
- absolute coverage loss versus old R0 <= 0.15;
- if old R0 has at least one false positive, the candidate removes at least one;
- Stage-2 predictions are byte-for-byte logically unchanged because Stage 2 is not under repair.

Passing these gates yields only `PROMISING_EXPLORATORY_REPAIR`, not independent validation, an
official label or strategy/trading authority.

## 6. Review cost reduction without semantic weakening

D collects only Protocol-v2 **Primary** opinions. Explicit 10-session Recent modifier opinions are not
recollected because B/C have already mechanically proven that Recent cannot mutate Primary, while D's
research question is exclusively the 20-session Stage-0 repair. This avoids 320 redundant recent
judgements without weakening the primary semantic test.

Both short-term and fund primary roles remain required per case; disagreement remains `AMBIGUOUS`,
with no majority or adjudication.

## 7. Execution sequence and budgets

Exactly four result-bearing phases are preregistered:

1. D1 — prepare calibration + freeze evaluation identity plan;
2. D2 — seal calibration primary references + search 27 candidates + freeze at most one;
3. D3 — after candidate freeze, prepare the preplanned evaluation sample;
4. D4 — seal evaluation primary references + score the frozen candidate against old R0.

Maximums: 512 fresh pool identities, 64 calibration cases, 96 evaluation cases, 320 primary opinions,
27 parameter trials, four result-bearing Jobs, 2,700 seconds per Job, zero automatic supplement
rounds and zero semantic-answer retries.

At this preregistration checkpoint, **no D market data has yet been read and no D Job has been
submitted**. The next authorized action is D1 only.
