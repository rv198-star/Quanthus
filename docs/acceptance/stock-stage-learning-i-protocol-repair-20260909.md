# P3-STAGE-001-I external-semantic protocol repair — 2026-09-09

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-I` |
| Disposition | **ACCEPTED — protocol-only repair, no holdout authority** |
| Parent evidence | `stock-stage-learning-f-arbitration-inconclusive-20260909.md` |
| Protocol | `config/stage-learning/qualification-protocol.v2.json` |
| Market data read by this repair | none |
| 2022-2024 fresh reuse | forbidden |
| 2025 permanent blind | unopened / unselected |

## 1. Why a successor protocol is needed

The consumed F Run failed before panel review, candidate/R0 prediction, or scoring because the preregistered sampler treated every stratum target as an exact quota. The final `qualification_mature_held` target was 80 unique episodes, but only 58 additional unique episodes remained after earlier strata consumed episode identities in the frozen order.

The correct lesson is not to lower the mature-held target to the observed 58. That would be result-aware threshold adaptation. The defect is that a rare-stratum target was also used as a process-completion requirement, even though the already frozen semantic hard gates were intended to own whether the realized reference package was sufficient for a claim.

## 2. Frozen repair

Protocol v2 keeps the same five strata and the same numerical target caps from F v1:

- started activity: 200;
- early held: 180;
- near-anchor loss negative: 100;
- reclaimed ambiguity: 80;
- mature held: 80.

These values are now explicitly **target caps**, not exact minimum quotas. Selection remains deterministic and one-context-per-episode. If a stratum contains fewer eligible unique episodes than its cap, the protocol takes every available eligible episode up to that cap, records the realized count and a shortfall reason, and continues without result-dependent cross-stratum backfill.

The final sufficiency decision remains owned by the unchanged semantic hard gates from F v1, including per-stage definite reference counts, ambiguity audit size/rate, positive-reference coverage, definite-negative false-positive rate, Wilson precision lower bounds, R0 noninferiority, unique-episode enforcement, and zero quantitative-audit vetoes.

This means scarcity can still yield INCONCLUSIVE. The repair only prevents an exact rare-stratum quota from terminating the process before the evidence package reaches the gates designed to judge sufficiency.

## 3. No result-aware semantic relaxation

The repair does **not**:

- reduce the mature-held target from 80 to 58;
- change candidate parameters or the frozen candidate digest;
- change any F v1 semantic hard-gate threshold;
- permit R1 search, R2, future settlement, strategy results, or parameter mutation;
- reuse 2022-2024 as a fresh confirmatory partition;
- select or authorize 2025;
- publish SHADOW or QUALIFIED results.

The change is entirely in the control semantics of sample realization versus sample sufficiency.

## 4. Partition discipline

`2022-2024-arbitration` is recorded as consumed and forbidden for fresh confirmatory reuse. The protocol names no next holdout partition. `2025-permanent-blind` remains `unopened_unselected` and requires a separate explicit authorization before any future execution work can bind or read it.

Because the F failure itself revealed holdout sample-population information, protocol v2 is result-aware and may only govern a future untouched holdout or forward evidence stream. It cannot retroactively rehabilitate F.

## 5. Delivery decision

`P3-STAGE-001-I` closes as a protocol-only governance repair. No runtime submit capability or holdout access is opened by this closeout.

The next result-bearing step belongs to a separate blocked task (`P3-STAGE-001-J`) and must not become ready until a specific untouched partition/evidence source, access budget, evidence tier, and exact runtime contract are explicitly authorized.
