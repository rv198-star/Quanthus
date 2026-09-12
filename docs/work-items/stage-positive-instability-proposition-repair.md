# P3-STAGE-009 — Positive Instability Proposition Repair

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | P3-STAGE-008 / `REVISE_OR_PARK_EVALUATION_FAIL` |
| Root-cause evidence | `../acceptance/stage-directional-boundary-root-cause-validation-20260912.md` |
| Current state | A/B/C closed; D ready for preregistration only; fresh-data/runtime/threshold authority closed |

P3-STAGE-009 repairs the positive `structural_instability_20` proposition itself. It does **not**
retune the P3-STAGE-008 relocation guard and does not reopen the consumed 2021 evaluation as a
parameter-selection surface.

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-009-A | Freeze positive-instability semantics and adversarial counterexamples | P3-STAGE-008 | closed; design approved; `../design/stage-positive-instability-proposition-repair.md`; no market read or runtime |
| P3-STAGE-009-B | Implement the approved positive-instability contract | P3-STAGE-009-A | closed; engineering accepted; `../acceptance/stage-positive-instability-b-implementation-20260912.md` |
| P3-STAGE-009-C | Synthetic + consumed-evidence conformance diagnostics | P3-STAGE-009-B | closed; `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`; `../acceptance/stage-positive-instability-c-conformance-20260912.md` |
| P3-STAGE-009-D | Fresh task-disjoint validation | P3-STAGE-009-C | ready for preregistration only; no data access until source/identity/grid/job/gate contract is frozen |

## Frozen successor direction

- Stage 0 remains a positive direction-neutral instability proposition, never a residual bucket.
- `center_shift` remains a continuous descriptive/modifier Fact and **cannot independently establish
  `structural_instability_20`**.
- Positive instability must have separately explainable evidence of disorder/expansion/path
  instability at the primary 20-session scale.
- The exact replacement predicate is **not yet frozen**. In particular, this work item does not turn
  `width_change20 >= 0.20` into the final definition merely because that post-hoc counterfactual
  passed the consumed evaluation.
- The P3-STAGE-008 relocation candidate (`10% / 0.50 / 0.60`) is not retuned on consumed evaluation.
- Stage 2 remains unchanged.
- P3-STAGE-008 2021 evaluation is permanently diagnostic-only for P3-STAGE-009.
- 2022–2024 and 2025 remain closed unless a later D preregistration explicitly obtains authority.

## A-stage review questions

P3-STAGE-009-A must answer, before implementation:

1. What positive observation distinguishes true structural instability from a migrating but orderly
   range?
2. Is range expansion necessary, merely supporting, or one member of a small positive-evidence set?
3. How should strong contraction plus large center migration resolve without becoming Stage 0?
4. How should broad two-sided oscillation with small net displacement resolve?
5. How should mixed `instability + coherent relocation` remain outside Stage 0 without forcing a
   different Stage label?
6. Can the positive instability contract be expressed with existing Structure Facts, or is one
   additional threshold-free Fact actually necessary?

009-A answered these questions and passed same-origin four-responsibility adversarial review with zero open directional blocker. 009-B then implemented the channel truth contract and reason ledger without numeric thresholds or runtime/data authority. 009-C closed `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`: consumed evidence supports the need for both expansion and broad-reversal channels without selecting any numeric cutoff. 009-D may now be preregistered, but no fresh market data or runtime Job is authorized until that preregistration is frozen.
