# P3-STAGE-011 — Stage 0/2 Joint Boundary and Orderly-Consolidation Repair

| Field | Value |
| --- | --- |
| Parent | P3 stock-stage recognition |
| Predecessor | P3-STAGE-010 / `REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Design | `../design/stage-orderly-consolidation-boundary-repair.md` |
| Review | `../reviews/stage-orderly-consolidation-boundary-repair-review-20260913.md`; post-closeout expert meeting: `../reviews/stage-orderly-consolidation-p011-post-closeout-expert-review-20260913.md` |
| Machine design | `../../config/stage-learning/orderly-consolidation-boundary-repair-design.v1.json` |
| Root-cause diagnostic | `../../config/stage-learning/orderly-consolidation-boundary-diagnostic.v1.json` |
| Delivery state | `../../config/stage-learning/orderly-consolidation-boundary-repair-delivery.v1.json` |
| Current state | CLOSED `REVISE_OR_PARK_DEVELOPMENT_FAIL`; D1 recovery succeeded and D2 sealed/scored the exact 27 pairs with 0 eligible; Stage2 repair is strong, but every pair misses frozen Stage0 coverage; no pair frozen and preserved 2021 remains unread |

| ID | Work | Dependency | Status / exit |
| --- | --- | --- | --- |
| P3-STAGE-011-A | Freeze Stage2 topology and joint 0/2 boundary | P3-STAGE-010 | closed; design approved; consumed P010 evidence diagnostic-only |
| P3-STAGE-011-B | Implement deterministic orderly-consolidation projection and synthetic counterexamples | P3-STAGE-011-A | closed; engineering accepted; `../acceptance/stage-orderly-consolidation-boundary-b-implementation-20260913.md` |
| P3-STAGE-011-C | Run consumed-evidence topology conformance | P3-STAGE-011-B | closed; `PASS_TOPOLOGY_CONFORMANCE_ONLY`; `../acceptance/stage-orderly-consolidation-boundary-c-conformance-20260913.md` |
| P3-STAGE-011-D | Fresh joint Stage0/Stage2 development on a new disjoint cohort | P3-STAGE-011-C | closed `REVISE_OR_PARK_DEVELOPMENT_FAIL`; exact 27 pairs / 0 eligible; Stage2 balanced reaches 20/21 TP with precision/coverage 0.9524, but all pairs fail Stage0 coverage and the best is 58/83=0.6988 vs 0.70; 2021 unread; `../acceptance/stage-orderly-consolidation-joint-d2-development-closeout-20260913.md` |

## Hard boundaries

- P010 remains immutable; no tenth Stage0 candidate is created.
- P010 2019 and P009 2020 evidence are diagnostic-only for P011 threshold selection.
- Stage2 becomes positive low-efficiency + narrowness + contraction + negative relocation evidence;
  center shift is modifier context, not a truth gate.
- No generic path-churn/counter-excursion order threshold is introduced.
- P3-STAGE-008 relocation guard is reused exactly and cannot be retuned here.
- Any future Stage2 development surface is exactly three bundled profiles; future joint surface is at
  most 27 preregistered Stage0/Stage2 pairs.
- Simultaneous Stage0/Stage2 assertion remains a hard semantic conflict, not a precedence decision.
- Preserved 2021 remains unread until a pair is frozen on a new disjoint development cohort.
- 2022–2025, official labels, daily activation, strategy and trading remain closed.
- The successor route is `../work-items/stage-atomic-proposition-successor.md`. P3-STAGE-012 A/B/C
  are closed as consumed-evidence diagnostics only; D expert review is next. No implementation,
  fresh-data or preserved-2021 authority has been created.
