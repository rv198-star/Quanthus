# P3-STAGE-001-J development sample-capacity census — release-ready freeze (2026-09-09)

| Field | Value |
| --- | --- |
| Scope | Development-only structural sample-capacity census before any successor holdout selection |
| Data window | cohort evidence through 2019-12-31; structure warm-up from 2019-07-01; report 2020-01-02 through 2021-12-31 |
| Nested populations | 64 / 128 / 256 / 512 securities from one deterministic pre-2020 rank order |
| Holdout authority | NONE |
| Arbitration read | forbidden |
| 2025 permanent-blind read | forbidden |
| Candidate / R0 prediction | not executed |
| Synthetic semantic panel | not executed |
| Purpose | reverse-infer a reasonable future security population from structural episode density |

## 1. Why this census exists

F consumed the single 2022-2024 arbitration budget but stopped before semantic review or scoring because the final `qualification_mature_held` stratum could supply only 58 additional unique episodes against an exact target of 80 after earlier strata had already consumed overlapping episodes. That result proves the 64-security preregistered population/sampling interaction was not sufficiently sized; it does **not** show that the market contains only 58 mature-held episodes.

The successor protocol therefore separates target caps from minimum semantic sufficiency. Before selecting any untouched holdout, this census measures how structural capacity changes as the deterministic security population grows. It uses only already-consumed development data, so its output may guide future sample design without consuming a new holdout.

## 2. Frozen execution contract

`config/stage-learning/sample-capacity-census.v1.json` freezes one maximum 512-security development cohort and four nested projections: 64, 128, 256 and 512. Security membership is ranked only from evidence available through 2019-12-31. The maximum cohort's 2019 warm-up / 2020-2021 StructureV2 and activity facts are built once; smaller cohorts are projected from the same deterministic result instead of rescanning/rebuilding each scale.

For each population size the census reports:

- raw unique-episode capacity before cross-stratum consumption;
- realized counts after the exact protocol-v2 order and one-context-per-episode constraint;
- started-activity sub-bucket and early-held sub-bucket capacity;
- target-cap shortfalls;
- whether all structural target caps are met;
- the first tested population size meeting all target caps, if any.

No stock codes, prices, episode rows, candidate predictions, semantic judgements, future settlements or strategy results are published in the census artifact.

## 3. Authority ceiling

The machine plan explicitly authorizes only `development_capacity_census_authorized=true`. The following remain false/closed:

- bounded arbitration qualification;
- general sealed-partition access;
- 2025 permanent-blind qualification;
- market-result access;
- parameter search;
- holdout population selection;
- successor qualification submit;
- daily scheduling and trading.

The census cannot make 2022-2024 fresh again, cannot select 2025, and cannot prove that the frozen candidate is externally valid. It only answers the population-sizing question using development structure density.

## 4. Worker contract

The RX6800 Stage Worker admits a new fixed kind:

`quanthus.stage-capacity-census.v1`

It is bound to:

- config: `config/stage-learning/sample-capacity-census.v1.json`;
- module: `quanthus.stage_learning.capacity_census`;
- artifact: `stage-capacity-census-*`;
- per-kind wall budget: 1800 seconds;
- the same fixed repository/training-root/job schema as existing Stage Worker jobs.

It does not use or alter the one-time F qualification budget. Verification projects only the small capacity summary into `stage_worker_status.result_ref.capacity_census`, so the existing read-only status path is sufficient for post-run review.

## 5. Repository qualification

Focused gate:

- Ruff: PASS;
- `git diff --check`: PASS;
- capacity/worker/planning/traceability tests: 76 passed.

Full repository gate:

- `make check`: PASS;
- 1708 passed;
- 7 existing environment-dependent skips (PostgreSQL / production artifact conditions).

## 6. Disposition

**RELEASE-READY FOR DEVELOPMENT-ONLY CAPACITY CENSUS.**

This is not successor holdout authorization. After the census result is reviewed, J may freeze a future population size, but selecting or reading any untouched holdout remains a separate explicit gate. 2025 remains unopened/unselected.
