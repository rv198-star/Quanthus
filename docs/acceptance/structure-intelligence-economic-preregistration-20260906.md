# Structure Intelligence economic-value preregistration (2026-09-06)

| Field | Value |
| --- | --- |
| Status | frozen before result access |
| Work item | `P3-STRUCT-002` |
| Predecessor | `P3-STRUCT-001` accepted technical MVP |
| Strategy | `candidate.p4-trend-risk@1.0.1` |
| Frozen run config | `config/structure-economic-run.development.v1.json` |
| Run-config digest | `sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de` |
| Result access before persistence | none |
| Sealed partitions | forbidden |

## 1. Frozen question

This preregistration freezes one complete-P2 development experiment before any C1/C2 economic
result is produced or inspected.

The experiment asks two ordered questions:

1. **Primary:** does coarse structure-state representation (C2) add material economic/decision
   information beyond continuous structure facts (C1) when both receive the same historical
   information and episode memory?
2. **Secondary:** does either C1 or C2 materially change the accepted native formal strategy (C0)?

No alternate date window, security subset, score mapping, action threshold, model, or candidate
population may be selected after results are opened under this work item.

## 2. Exact variants

Three and only three complete capital-constrained P2 variants are admitted:

- **C0 `variant.native-baseline`** — exact accepted formal strategy, no structure score;
- **C1 `variant.structure-continuous`** — native strategy plus the frozen continuous structure
  `ScoringOutput`;
- **C2 `variant.structure-coarse`** — native strategy plus the frozen coarse-state structure
  `ScoringOutput`.

All variants preserve the accepted position-risk expert, system guardians, candidate order,
industry concentration, five fixed capital units, P2 matcher, fees/costs, corporate actions,
settlement, held-position review, reductions/exits, and cooldown. Structure affects **entry scoring
only**. It does not gain veto, constraint, position-management, funding, or execution authority.

If structure is unavailable, C1/C2 abstain and retain the C0 action. Missing data never implies
structural damage or forced sale.

## 3. Information-budget control

C1 and C2 are built from one shared Structure ADM generated from the same Direct-DWD session. Both
receive the same 20-session structural boundary and the same sequential breakout-episode memory.
C2 is only a compression of those facts into the accepted MVP coarse outcomes; it cannot consume
extra lookback or future evidence.

The structure scan begins `2019-10-08` solely to establish prior causal history. Economic decision
scope remains `2020-01-01` through `2021-12-30` with T+1 execution through `2021-12-31`.
Arbitration 2022-2024 and permanent-blind 2025 remain sealed.

## 4. Baseline gate

Before C1 or C2 is interpreted, C0 must exactly reproduce the accepted formal-P2 baseline:

- net total return: `0.06704618`;
- maximum drawdown: `0.07102827`.

A mismatch is a stop condition, not a reason to update the expected values.

## 5. Frozen score mapping

The executable mapping is checksum-covered by the run-config digest above.

C1 continuous score:

- range intact `0`;
- breach `-1`;
- threatened linearly from `-1` at support to `-0.5` at +2%;
- breakout base `+0.5`, plus at most `+0.25` for distance above the frozen attempt level and
  `+0.25` per post-attempt holding close, capped at `+1`;
- unavailable => abstain.

C2 coarse score:

- range intact `0`;
- breakout attempt `+0.5`;
- breakout accepted `+1`;
- support threatened `-0.5`;
- support breach `-1`;
- unavailable => abstain.

There is no grid search, automatic tuning, or threshold repair in this work item.

## 6. Metrics and materiality

Primary comparison is C2-C1. Secondary comparisons are C1-C0 and C2-C0.

A comparison is material if at least one is true:

- absolute capital-constrained net-total-return delta >= `0.01`;
- absolute maximum-drawdown delta >= `0.01`;
- final-action change rate >= `0.02`.

The report also includes tail loss, average invested capital, time in market, turnover, fees,
missed opportunities, and decision-change counts. Every value comes from a complete P2 capital
rerun; independent trade deltas are not summed as portfolio ROI.

Paired uncertainty uses non-overlapping 22-session blocks, 2,000 deterministic bootstrap resamples,
and 95% intervals over daily-return differences. Shared dates/securities/event windows/capital
units are explicitly treated as dependent observations.

## 7. Resource and publication budget

One result-bearing Job executes exactly three complete-P2 variants inside one shared runtime. At
most two independent result-bearing Jobs (A/B) are admitted for this preregistered request before a
new human decision. Event Worker concurrency remains one and its existing 16 GiB hard container
boundary remains unchanged. No new DWS, ADS, cache authority, GPU path, or Worker family is opened.

Each Job must publish one immutable Structure-economic package and pass deep Worker verification.
Acceptance requires A/B equality of at least:

- semantic result digest;
- Structure ADM manifest digest;
- each variant replay digest and metrics;
- C1-C0, C2-C0, and C2-C1 comparison results;
- lifecycle disposition.

## 8. Frozen lifecycle outcomes

Only these dispositions are admitted:

- `no_material_structure_signal`;
- `prefer_continuous_structure_facts`;
- `continuous_facts_sufficient_no_material_coarse_increment`;
- `coarse_state_increment_detected_requires_human_review`.

Even the last outcome authorizes only a new bounded design/review decision. None authorizes the
thirteen-state draft, model training, Shadow, paper, live execution, or access to sealed years.

## 9. Stop conditions

Stop and publish no positive claim on:

- C0 reproduction mismatch;
- P2 ledger/reconciliation failure;
- request or score-mapping drift;
- candidate/population drift;
- PIT or DWD identity failure;
- sealed-partition access attempt;
- undeclared intervention;
- artifact/digest verification failure; or
- resource failure that prevents all three variants from completing under the frozen environment.

A failed or economically weak result is a valid outcome. It must not trigger same-work-item tuning.
