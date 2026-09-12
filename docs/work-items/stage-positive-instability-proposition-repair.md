# P3-STAGE-009 — Positive Instability Proposition Repair

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | P3-STAGE-008 / `REVISE_OR_PARK_EVALUATION_FAIL` |
| Root-cause evidence | `../acceptance/stage-directional-boundary-root-cause-validation-20260912.md` |
| Current state | A/B/C/D closed; D=`REVISE_OR_PARK_DEVELOPMENT_FAIL`; evaluation remained unread |

P3-STAGE-009 repairs the positive `structural_instability_20` proposition itself. It does **not**
retune the P3-STAGE-008 relocation guard and does not reopen the consumed 2021 evaluation as a
parameter-selection surface.

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-009-A | Freeze positive-instability semantics and adversarial counterexamples | P3-STAGE-008 | closed; design approved; `../design/stage-positive-instability-proposition-repair.md`; no market read or runtime |
| P3-STAGE-009-B | Implement the approved positive-instability contract | P3-STAGE-009-A | closed; engineering accepted; `../acceptance/stage-positive-instability-b-implementation-20260912.md` |
| P3-STAGE-009-C | Synthetic + consumed-evidence conformance diagnostics | P3-STAGE-009-B | closed; `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`; `../acceptance/stage-positive-instability-c-conformance-20260912.md` |
| P3-STAGE-009-D | Fresh task-disjoint validation | P3-STAGE-009-C | closed; `REVISE_OR_PARK_DEVELOPMENT_FAIL`; 122 development cases / 27 frozen candidates / 0 eligible; 2021 evaluation unread; `../acceptance/stage-positive-instability-d-development-closeout-20260912.md` |

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

009-A answered these questions and passed same-origin four-responsibility adversarial review with zero open directional blocker. 009-B implemented the channel truth contract and reason ledger without numeric thresholds. 009-C closed `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`. 009-D then consumed one preregistered 2020 development batch: 122 morphology-blind cases, 244 sealed same-origin Primary opinions and the exact 27-candidate grid. No candidate met the frozen coverage gates, so D closed `REVISE_OR_PARK_DEVELOPMENT_FAIL` before evaluation. The 160-identity 2021 evaluation plan remains unread and cannot be consumed by this failed version. No grid expansion, threshold relaxation, supplement, relocation retuning, Stage2 change or future-partition access is authorized.
