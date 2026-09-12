# P3-STAGE-010 — Positive Instability Quantitative Projection Repair

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | P3-STAGE-009 / `REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Design | `../design/stage-positive-instability-quantitative-projection-repair.md` |
| Review | `../reviews/stage-positive-instability-quantitative-projection-review-20260912.md` |
| Machine design | `../../config/stage-learning/positive-instability-quantitative-projection-design.v1.json` |
| Delivery state | `../../config/stage-learning/positive-instability-quantitative-projection-delivery.v1.json` |
| Current state | A/B/C closed; first D1 failed on non-trading cohort endpoint before artifact publication; exactly one technical retry authorized with 2018-12-28 correction |

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-010-A | Freeze separated quantitative projection topology and adversarial review | P3-STAGE-009 | closed; design approved; no data/runtime authority |
| P3-STAGE-010-B | Implement deterministic projection contract and synthetic tests | P3-STAGE-010-A | closed; engineering accepted; `../acceptance/stage-positive-instability-quantitative-projection-b-implementation-20260912.md` |
| P3-STAGE-010-C | Replay consumed Stage-009 evidence as diagnostic conformance | P3-STAGE-010-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; no threshold learning |
| P3-STAGE-010-D | Fresh 2019 development, freeze one candidate, then optionally open preserved 2021 evaluation | P3-STAGE-010-C | ready for exactly one D1 technical retry after `stage_b5e3feae...` failed before artifact publication; cohort endpoint corrected to 2018-12-28; preserved 2021 evaluation still closed; `../acceptance/stage-positive-instability-quantitative-d1-retry-20260912.md` |

## Hard boundaries

- No shared broad-width + very-low-efficiency hard core across both channels.
- No threshold is learned from P3-STAGE-009 2020 development.
- The P3-STAGE-009 2021 evaluation plan remains unread and may not be reselected.
- P3-STAGE-010 development identities must exclude all prior exposed pools including the sealed
  Stage-009 evaluation identities.
- Stage 2, relocation guard and center-shift truth authority remain unchanged.
- 2022–2025 stay closed.
- No implementation phase automatically opens market-data or runtime authority.
