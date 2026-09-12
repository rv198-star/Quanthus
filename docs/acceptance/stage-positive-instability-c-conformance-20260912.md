# P3-STAGE-009-C Consumed-Evidence Conformance Acceptance

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-009-C |
| Contract | `../../config/stage-learning/positive-instability-conformance.v1.json` |
| Result | `../../config/stage-learning/positive-instability-conformance-result.v1.json` |
| Runner | `../../scripts/run_stage_positive_instability_conformance.py` |
| Source | sealed P3-STAGE-008 D3 Primary-only evaluation evidence |
| Disposition | PASS_DIAGNOSTIC_CONFORMANCE_ONLY |

## 1. Boundary

C did not reopen P3-STAGE-008 or create a new development/qualification batch. It performed 13
read-only Stage Worker status calls: one D3 parent receipt plus twelve bounded Primary-only review
pages. The binding stayed exact to:

- D3 Job `stage_7617aafa1f3149c0a65935ef291fc521`;
- manifest `sha256:a77bc2b439da05da5d10b7291c64caf5d680148c4c69126ba31ea39ac7a99827`;
- evidence `sha256:912a11480d6e12a3dcc3862bcdb0900592623a419e5282e5bb2500e2ad5086da`;
- 95 cases;
- sealed evaluation review ledger.

No candidate output, future rows, explicit 10-session derived Facts or fresh market-data read entered
the diagnostic.

## 2. Fixed comparison groups

The groups were fixed from the already-frozen P3-STAGE-008 root-cause evidence before this C replay:

- center-shift-only definite Stage-0 references: indices `26, 48`;
- center-shift-only remaining false positives: indices `30, 44, 46, 50, 53, 67, 76, 93`.

C did not add, remove or replace cases based on the replay result.

## 3. Geometry result

The two definite Stage-0 cases have the following median geometry:

- range width: `0.2906161340`;
- absolute directional efficiency: `0.0465841862`;
- range traversal: `0.1839337315`;
- counter-direction excursion share: `0.8994413408`.

The eight false positives have median geometry:

- range width: `0.1349094546`;
- absolute directional efficiency: `0.1372872108`;
- range traversal: `0.3971423338`;
- counter-direction excursion share: `0.5553808526`.

All four comparisons point in the expected disorder direction:

- the true Stage-0 group is broader;
- it has lower directional efficiency;
- it has lower endpoint traversal;
- it has higher counter-direction excursion.

Stronger than the median-level comparison, **each of the two true Stage-0 cases individually lies on
the more-disorder-like side of the false-positive group median on all four dimensions**.

## 4. Why the second channel is necessary

Of the 15 definite Stage-0 references in the consumed evaluation:

- 13 have positive 20-vs-prior20 width change;
- 2 have negative width change;
- those two negative-width-change cases are exactly indices `26` and `48`.

Therefore a successor that simply removes center-shift-only authority and requires positive range
expansion for every Stage-0 case would discard two previously definite business-positive examples.
Those two cases are not semantically empty: they are the strongest broad/reversal-shaped examples in
the fixed center-only comparison. This supports the 009-A two-channel design:

1. `expanding_disorder_20` for material expanding disorder;
2. `broad_reversal_disorder_20` for broad two-sided disorder that may persist while width contracts.

This is the main conformance result of C.

## 5. What C does not prove

C does **not** choose quantitative thresholds for material width, expansion, low efficiency, low
traversal or high counter excursion. Comparing medians and ordering already-consumed examples is not
threshold learning.

C also does not:

- qualify P3-STAGE-009;
- retune the frozen P3-STAGE-008 relocation guard;
- change Stage 2;
- read 2022–2024 or 2025;
- authorize runtime labels, daily strategy use or trading.

## 6. Exit

P3-STAGE-009-C is closed as `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`.

The result is strong enough to authorize **D preregistration only**. Before any fresh data is opened,
D must separately freeze its source partition, economic-identity isolation, threshold candidate grid,
result-bearing Job budget, reference protocol, hard gates and claim ceiling.
