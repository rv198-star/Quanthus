# P3-STAGE-001-F arbitration qualification — INCONCLUSIVE (2026-09-09)

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-F` / #243 |
| Disposition | **INCONCLUSIVE — preregistered sample-feasibility shortfall** |
| Job | `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7` |
| Code revision | `75b0d3eb8fb14e1f04b6a4520aae31f29a80297e` |
| Qualification config | `config/stage-learning/qualification.v1.json` |
| Qualification config digest | `sha256:e235bd53ff7226358dc19eb6b80a08348cfeacf19455dd3bfa37626df43d15ac` |
| Candidate digest | `sha256:d492d12686be5f5ac84f87ba278afd873d9999aadc5fa5dc79a6f5f5985f176c` |
| Partition accessed | `2022-2024-arbitration` |
| Permanent blind | 2025 **not read / remains unopened** |
| Result artifact | none; `result_ref=null` |

## 1. Frozen execution identity

The single preregistered F Job was submitted only after the RX6800 Stage Worker had been deployed and verified at the exact revision above. The Worker admitted `quanthus.stage-qualification.v1`, reported `doctor_ready=true`, and the Job entered `executing` under that exact code revision.

The Job started at `2026-09-09T06:29:52.103089+00:00` and terminated at `2026-09-09T06:34:02.566483+00:00` with exit code `1` and Worker error `stage_qualification_process_failed`.

Because the process entered execution, the Worker-level one-time qualification budget moved from reservation to consumed. Under the frozen contract this budget is not reusable after success, failure, timeout, or cancellation once execution has begun.

## 2. Exact failure

The retained Worker log terminates in `select_qualification_contexts()` before reviewer-panel execution, candidate/R0 prediction, hard-gate scoring, or qualification publication. The failing preregistered stratum is:

```text
qualification_mature_held
```

Its frozen requirement was 80 unique episodes. After the earlier strata had consumed their episode identities in the preregistered selection order, only 58 additional unique mature-held episodes remained:

```text
StageQualificationError: qualification stratum has 58 unique episodes; need 80
```

The failing call is the `qualification_mature_held` `_take_unique_episodes(...)` branch in `src/quanthus/stage_learning/qualification.py`.

## 3. What this result means

This is **not** evidence that the frozen R1 candidate failed its semantic precision, recall, ambiguity, negative-control, or noninferiority gates. None of those scores were reached.

It is also not a successful bounded-SHADOW qualification. No complete 640-context sample, resolved synthetic reference package, candidate score, R0 score, hard-gate result, manifest, READY marker, or immutable qualification artifact was published.

The only valid F conclusion is:

> The exact preregistered 640-context external sample was infeasible under the frozen one-context-per-episode and selection-order contract because the final mature-held stratum could supply only 58 of the required 80 additional unique episodes.

Therefore `P3-STAGE-001-F` closes as **INCONCLUSIVE / sample-feasibility failure**. It does not become REJECTED on model quality, SHADOW_EXTERNAL_SEMANTIC_ACCEPTED, or QUALIFIED.

## 4. Holdout consumption and claim ceiling

The 2022-2024 arbitration partition has been accessed by the result-bearing F process and its one-time qualification budget is consumed. The project must not repair the sampler and rerun the same partition while claiming a fresh blind or confirmatory evaluation.

The failure occurred before candidate/R0 scoring, but the holdout still revealed sample-population information (`58 < 80`) and therefore is no longer untouched for this protocol.

2025 permanent-blind was not read by the F source contract and remains unopened. This closeout does not authorize reading it.

No future settlement, strategy result, R1 search, R2 model, parameter mutation, daily schedule, strategy consumer, notification, or trading authority was opened.

## 5. Protocol lesson for any future untouched holdout

The defect is in the **confirmation-sample feasibility contract**, not in the fail-closed behavior. Exact quotas for rare semantic strata can cause a holdout run to terminate before evaluation even when the eventual semantic hard gates would have been the correct sufficiency referee.

Any successor protocol must be frozen before touching a new holdout and must separate:

1. **sampling targets/caps** used to increase coverage of important strata; from
2. **minimum semantic hard gates** that decide whether the realized sample is sufficient for a claim.

A future protocol may preregister deterministic `take_up_to` caps for rare strata and then remain INCONCLUSIVE if the resulting reference package fails frozen per-stage, ambiguity, coverage, precision, or effective-episode minimums. It must not backfill from another stratum after observing holdout scarcity, change the candidate, or use 2022-2024 again as a fresh confirmatory set.

This lesson is result-aware and therefore cannot be applied retroactively to this consumed F Run.

## 6. Final disposition

`P3-STAGE-001-F`: **CLOSED / INCONCLUSIVE — SAMPLE FEASIBILITY**.

`P3-STAGE-001-G` and `P3-STAGE-001-H` remain blocked because F produced no qualified or bounded-SHADOW external-semantic label version. Any successor external validation requires a new preregistered protocol and separate authorization; 2025 remains untouched until such authority is explicitly opened.
