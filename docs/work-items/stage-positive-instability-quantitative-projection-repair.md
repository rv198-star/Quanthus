# P3-STAGE-010 — Positive Instability Quantitative Projection Repair

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | P3-STAGE-009 / `REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Design | `../design/stage-positive-instability-quantitative-projection-repair.md` |
| Review | `../reviews/stage-positive-instability-quantitative-projection-review-20260912.md` |
| Machine design | `../../config/stage-learning/positive-instability-quantitative-projection-design.v1.json` |
| Delivery state | `../../config/stage-learning/positive-instability-quantitative-projection-delivery.v1.json` |
| Current state | A/B/C closed; D1 independently verified; D2 sealed 160-case review then scored the exact 9-candidate grid with `eligible_count=0`; P010 closes `REVISE_OR_PARK_DEVELOPMENT_FAIL`, preserved 2021 remains unread; post-result diagnosis isolates a Stage0/Stage2 truth-boundary conflict rather than the old shared-core failure |

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-010-A | Freeze separated quantitative projection topology and adversarial review | P3-STAGE-009 | closed; design approved; no data/runtime authority |
| P3-STAGE-010-B | Implement deterministic projection contract and synthetic tests | P3-STAGE-010-A | closed; engineering accepted; `../acceptance/stage-positive-instability-quantitative-projection-b-implementation-20260912.md` |
| P3-STAGE-010-C | Replay consumed Stage-009 evidence as diagnostic conformance | P3-STAGE-010-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; no threshold learning |
| P3-STAGE-010-D | Fresh 2019 development, freeze one candidate, then optionally open preserved 2021 evaluation | P3-STAGE-010-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; exact 9 trials / 0 eligible; lenient×lenient passes precision/coverage/FPR but has 2 frozen-Stage2 overlaps, while lenient×balanced removes overlap but coverage=0.6296; 2021 unread; `../acceptance/stage-positive-instability-quantitative-d2-development-closeout-20260913.md` |

## Hard boundaries

- No shared broad-width + very-low-efficiency hard core across both channels.
- No threshold is learned from P3-STAGE-009 2020 development.
- The P3-STAGE-009 2021 evaluation plan remains unread and may not be reselected.
- P3-STAGE-010 development identities must exclude all prior exposed pools including the sealed
  Stage-009 evaluation identities.
- Stage 2, relocation guard and center-shift truth authority remain unchanged.
- 2022–2025 stay closed.
- No implementation phase automatically opens market-data or runtime authority.
