# P3-STAGE-012 — Freshness-capacity failure and fail-closed closeout

| Field | Value |
| --- | --- |
| Parent | `../work-items/stage-atomic-proposition-successor.md` |
| Governing delegation | `../../config/stage-learning/stage-atomic-proposition-p012-delegated-authority.v1.json` |
| Frozen development contract | `../../config/stage-learning/stage-atomic-proposition-development-screen.v1.json` |
| Frozen execution contract | `../../config/stage-learning/stage-atomic-proposition-development-execution.v1.json` |
| Machine disposition | `../../config/stage-learning/stage-atomic-proposition-p012-closeout.v1.json` |
| Closeout | `CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY` |
| Capability delivery gate | blocked; no market or Stage qualification |
| Evidence date | 2026-09-14 project date; original execution timestamps below are UTC |

## Original execution, not a new run

The sole G1 Job is `stage_6cccde81c6f24fae949f947a3209cd25`, at exact revision
`0e689614c6a458c89934c02c23177eecf7142084`, with input digest
`sha256:369f3e68074dbb56fb7f5dee59a6a757feb6bc416ee782995da05e74e9b3974f`.
It started at `2026-09-13T20:28:16.580644+00:00` and terminated at
`2026-09-13T20:28:29.790732+00:00`: approximately 13.210 seconds, state `failed`,
exit code 1, outer error `stage_atomic_proposition_g1_process_failed`, and no `result_ref`.
The original G1 budget file was retained with physical SHA-256
`b4d68a468f8507ccf82a7607fd683819d4bbae1b433e043a7fd0738d09a1ba03`.

The installed helper upgrade and initial exact deployment succeeded. A different problem then
blocked diagnosis: the existing status receipt did not expose the failing subprocess traceback,
and AgentDock could not directly read the protected log. This is distinct from the cause of G1's
failure. Diagnostic-only revision `fa528f66e47bcff597cb377c9c694931ff9033f7` was
committed, pushed and deployed through the already-admitted restricted helper. It exposes a
bounded P012 failure tail through `doctor`; it does not change submission, selection or retry
rules. No root-helper update or privilege expansion was needed for this diagnostic deployment.

## Attribution and limits

The original traceback identifies `atomic_proposition_development_prepare.py:223` calling
`_fresh_pool`, and `positive_instability_development_prepare.py:114` raising
`PositiveInstabilityPrepareError("fresh_pool_capacity_insufficient")`.

At the original revision, `_fresh_pool` constructs unique code/economic-identity memberships,
excludes the frozen prior-identity closure, hash-ranks the survivors, and raises this reason only
when fewer than the requested 512 eligible identities remain. The prepare uses the configured
2015 discovery window and cohort date without a hidden population limit. The audited exclusion
chain binds Stage007, the entire Stage008/009/010/011 pools and the preserved Stage009 plan.
No demonstrated implementation deviation justifies a technical recovery.

This establishes **insufficient eligible identities for this frozen plan**, not an algorithm-quality
failure. The exact surviving count and the contribution of historical exclusions, source coverage
and identity ambiguity were not logged. They are unknown, not zero. No new market scan was run to
fill that reporting gap. Source corruption, global identity exhaustion and insufficiency of every
possible future cohort are not established by this evidence.

Control-flow review places the failure before `read_development_admissions`, threshold-basis
construction, candidate evaluation, case selection, blind evidence and envelope measurements.
The Job did execute 2015 discovery and the 2015–2016 identity-metadata projection. Therefore this
closeout does **not** claim that nothing related to 2016 was accessed. The narrower supported
statement is that the 2016 development price/evidence reader was not reached. This is inferred
from the original traceback and code order, not from a newly reconstructed source-access ledger.

The P011 `case_budget` traceback still shown by `doctor` is a separate historical diagnostic. It
must not be attributed to this G1 Job.

## Four-role simulated disposition review

This is one assistant's same-origin role simulation using the frozen contracts, original failed
receipt, original traceback and implementation. It is neither independent-model validation nor a
meeting with human experts. No Primary case packet was reviewed and no G2 labels were created.

| Responsibility | Challenge considered | Disposition |
| --- | --- | --- |
| Short-term practitioner | Does the failure disprove compact disorder or require changing the U-L-U/L-U-L channel? | No candidate or path evidence was produced. Keep the synthetic semantics and mixed-state resolver; do not infer a trading-quality result. |
| Fund manager | Can a smaller sample be accepted to keep the project moving? | It would alter the already-frozen capacity requirement after observing its failure. Preserve the gate; no generalizability or deployment claim is supportable. |
| Quantitative researcher | Is this a technical retry, or a legitimate no-result stop? | The exact gate rejected the available selection. No conformance defect is demonstrated. Reject a recovery contract, reseeding, replacement thresholds or silent cohort substitution. |
| Warehouse engineer | Is the physical source broken, and should discovery be rerun for counts? | The log proves a capacity condition, not corruption. Exact counts are missing. A future capacity-only preregistration should capture counts and exclusion reasons before results; rerunning G1 is not an admissible diagnostic. |

Consensus: use the delegated `freeze_or_fail_closeout` authority to close P012 as
`CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY`. This is a fail-closed completion, not a PASS or a claim
that the overall Stage-recognition objective is complete.

## Retained assets and downstream stops

A–E9 retain their respective bounded dispositions; F remains frozen. G engineering remains
accepted, but its tests do not prove source/sample sufficiency. The reserved
`structure.geometry.close-envelope-resolution-trace` measurement remains a naming/boundary
registration only, without a semantic runtime binding. The P008 relocation guard is unchanged.

G1 is terminally failed. G2/G3/H/I are **not executed because of the upstream stop**, not passed
or independently failed. J has no evaluation score; this document and its machine result record
only the fail-closeout. No instability candidate or order profile was selected, no projection was
frozen, and no market/Stage qualification was obtained. Exact preserved-2021 identities and plan
remain unchanged and their evaluation market data remain unread. Permanent-blind 2025, daily,
strategy, trading and production activation remain closed. No job or sealed ledger was rewritten.

## Successor design, parked rather than silently authorized

The next resource question is whether a genuinely fresh and authority-compatible identity/time
surface exists. A future **separately preregistered capacity-only stage** should freeze its allowed
metadata/source scope, identity exclusions, admission conditions and stop rules first. It should
publish candidate count, unique-identity count, ambiguity losses, exclusion losses and eligible
capacity, plus immutable source bindings, without morphology, labels, thresholds or candidate
scores. Only a feasible resource may justify a new development preregistration.

This is a resource-first design recommendation, not an executable recovery contract and not a
new data-access authorization. This closeout does not reduce 512, weaken exclusions, reseed,
replace the cohort, open another year, or consume preserved 2021/2025. It does not automatically
start another issue or experimental series merely to rename a retry.

## Verification

The diagnostic-only commit completed `make check`: **2349 passed, 7 skipped**, doctor, compile and
planning sync passed. Targeted Ruff and `git diff --check` passed. It was deployed successfully
with `doctor.ready=true` and no reported problems, and returned the original Job's traceback.

The closeout adds seven synthetic capacity-boundary, stop-before-data-access, frozen-binding,
no-promotion and current-navigation checks in
`../../tests/contract/test_stage_atomic_proposition_p012_closeout.py`. They use fabricated identities,
not market data or another G1 execution. Focused verification passed **87 tests**. Final `make check`
passed **2356 tests with 7 skipped** in 246.96 seconds; doctor, compile and planning sync passed.
The skips are the existing unavailable production-artifact/PostgreSQL integration surfaces, not
market qualification successes. Targeted Ruff and `git diff --check` passed.

The final read-only RX6800 check still reports the same failed original Job and input digest,
`result_ref=null`, diagnostic revision `fa528f66e47bcff597cb377c9c694931ff9033f7`, and
`doctor.ready=true` with no problems. The original budget file hash remains exact. Direct Git
worktree-cleanliness reinspection as AgentDock could not read a newly fetched loose Git object;
this permission limitation is not evidence of source corruption. No permissions were changed and
no new deployment, submission, cancellation or retry was performed for the document/test closeout.
