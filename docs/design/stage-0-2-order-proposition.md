# Stage 0/2 结构秩序命题实验

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Accepted scope | completed experiment and INCONCLUSIVE closeout only; no Stage candidate accepted |
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

## 7. Runtime-only retry after the first attempt timed out

The first attempt `stage_64da9bea52984726ab32c10bfdd36d4c`, running revision
`35439da64e81f60aa50bfeb26e4a5d0531d6eb78`, reached terminal `failed` with
`stage_worker_wall_budget_exceeded` at `2026-09-10T06:39:21.724431+00:00`. No result reference was
published to its terminal status. This is a technical execution failure, not the semantic
`INCONCLUSIVE` exit in section 5; no completed reference/search results have been reviewed.

Under the owner's instruction to continue to this milestone's completion, one bounded technical
retry is permitted after a runtime-only repair. This is not an additional tuning round. It must
retain the exact development/panel/Fact contracts, 512-security cohort construction, source scope,
81+27 grids, hard gates, selection order and 2700-second Worker limit. Before a retry, inspect the
first Job for a sealed READY/manifest artifact: a recoverable result must be verified rather than
recomputed. The first failed Job and its logs remain intact. A second technical failure must be
reported rather than automatically starting further attempts.

The repair reuses `DailyInput.member_digest` only for the lifetime of each immutable input object.
Its calculation and exclusion of caller-only `session_index`/`cutoff` are unchanged. Cache storage
is not a dataclass field, a persisted cache, an ADM, or a new semantic identity component. Source
revisions, coordinate/listing changes and replacement objects must obtain their own exact digest.
Counterexample tests must compare complete assembled observation bytes with the original uncached
formula, not only compare observation counts.

A progress callback may report completed-security and observation counts every 16 securities and
at completion. The callback is observational: it cannot enter reference inputs, candidate scoring,
Fact identities or published semantic counts. Deployment and submission must use the new exact
Git revision through the approved node deployment route. Successful local tests are release
readiness only; C/D cannot close until the actual retry's terminal evidence is reviewed.

## 8. Completed experiment and current boundary

The permitted retry `stage_87d166eb3f4a4f7f858f9b4f3e238462`, revision
`1878c625ad04b23793507d92bf0e3008052a5a85`, succeeded in 660.726405 seconds and returned
`INCONCLUSIVE`. Its 224,346 usable observations supplied 160 sampled references: Stage 0=69,
Stage 2=9, ambiguous=65 and neither=17. The Stage-2 reference minimum is 20 and is a shared
prerequisite of both searches. Thus neither proposition can freeze; this does not establish
semantic inseparability or population-wide scarcity. Both selected parameter objects and the
selected-pair overlap count remain null.

The existing section-5 stop rule closes this milestone without another run, reference supplement,
grid change or lower gate. Continuous Trend Evolution and Structure Order Facts and the 0/2
vocabulary remain unchanged. The machine delivery plan's `runtime_accepted` records completed
execution/closeout only; it explicitly denies candidate acceptance, reference sufficiency and
further technical retry/search authority. This is not a new Projection Contract.

Evidence supports the Worker-verified terminal summary, a matching reconstruction of the null
candidate hash, and the frozen necessary-condition gate. The full remote trial arrays were not
separately fetched through the installed read adapter; no per-trial accuracy claim or full second
audit is made. This limitation does not justify selecting a candidate or rerunning the experiment.
Result and three-perspective disposition:
`../acceptance/stage-0-2-order-proposition-result-20260910.md` and
`../reviews/stage-0-2-order-proposition-result-review-20260910.md`.

Any later reference-protocol redesign requires a new owner decision. The current positive-reference
shortage cannot be addressed by silently loosening the 10/20-session criteria or count gates after
seeing this result. The 2022-2024 and 2025 boundaries and all official-label/daily/strategy/trading
gates remain closed.
