# Stage 0/2 结构秩序命题实验

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Work item | `P3-STAGE-004` |
| Inputs | accepted `P3-STRUCT-006` Trend Evolution Facts; accepted `P3-STRUCT-007` Structure Order Facts |
| Runtime config | `../../config/stage-learning/order-proposition-development.v1.json` |
| Synthetic panel | `../../config/stage-learning/order-proposition-synthetic-panel.v1.json` |
| Scope | development-only finite Stage-0/2 proposition experiment; no official LabelCard or primary-stage output |

## 1. Working semantics

Stage codes are retained, but the observable working names are tightened:

- **Stage 0 — 结构无序 / structural disorder**. It requires positive evidence that a low-directional-
  efficiency path is geometrically unstable through material range expansion or range-center drift.
  Stage 0 is never the residual class after every other proposition fails.
- **Stage 2 — 有序整理 / orderly consolidation**. It requires positive evidence of low directional
  efficiency, bounded range width, contraction and center stability. It does not imply accumulation,
  future breakout or a buy setup.

Both propositions may be false. They may not both be true for the selected development pair.
`primary_stage` is outside this milestone.

## 2. Formal Facts only

Candidate rules consume accepted formal Facts rather than recomputing anonymous indicators:

- Trend Evolution: `structure.geometry.directional-efficiency@20`;
- Structure Order: `range-width@20`, `width-change@20_vs_20`,
  `range-center-shift@20_vs_20`.

The synthetic reference panel may additionally inspect the accepted 10-session width-change and
center-shift Facts so its judgement is not identical to the candidate rule surface.

## 3. Reference protocol

One frozen 512-security 2020-2021 development cohort is used. Reference sampling is stratified by
preregistered T-only morphology bands into directional controls, order-like, disorder-like and mixed
low-efficiency contexts. At most one reference context is selected per security.

Two deterministic primary reviewer archetypes are mutually hidden. Their disagreement is resolved by
a third deterministic adjudicator. Reviewers never receive candidate parameters, algorithm
predictions or future outcomes. The evidence tier remains `SYNTHETIC_EXPERT_CONSENSUS`; it is not
human-expert validation.

## 4. Finite search

Stage 2 is searched first over 81 preregistered combinations:

- absolute 20-session directional-efficiency maximum: `0.25 / 0.33 / 0.40`;
- 20-session range-width maximum: `0.15 / 0.18 / 0.22`;
- 20-vs-prior-20 width-change maximum: `-0.10 / -0.20 / -0.30`;
- absolute 20-vs-prior-20 center-shift maximum: `0.03 / 0.04 / 0.06`.

Stage 0 is then searched over 27 preregistered combinations:

- absolute 20-session directional-efficiency maximum: `0.30 / 0.40 / 0.50`;
- 20-vs-prior-20 width expansion minimum: `0.10 / 0.20 / 0.30`;
- absolute center-shift minimum: `0.05 / 0.07 / 0.09`.

Stage 0 asserts when the low-efficiency prerequisite holds and either expansion or center drift
crosses its threshold. If Stage 2 freezes, every Stage-0 trial is additionally gated on zero overlap
with that frozen Stage-2 rule across the complete development observation set. No Stage-2×Stage-0
Cartesian pair search is allowed.

## 5. Hard gates and stop rule

Each proposition must have sufficient positive/negative/ambiguous reference support, positive
coverage at least 55%, definite-negative false-positive rate at most 10%, ambiguous assertion at most
20%, one-sided 95% Wilson precision lower bound at least 65%, and F1 no worse than R0 by more than
5 points. The final pair must have zero overlap.

If either proposition has no eligible candidate, the milestone closes `INCONCLUSIVE`; it does not
expand the grid, relax a hard gate or create a third result-dependent reference round.

## 6. Authority boundary

The experiment may read only 2020-2021 development data after 2019 warm-up for the frozen cohort.
It may produce a development proposition candidate, but not an official label. It does not reread the
consumed 2022-2024 arbitration partition and does not touch 2025. No daily schedule, consumer,
strategy, trading or primary-stage authority opens.
