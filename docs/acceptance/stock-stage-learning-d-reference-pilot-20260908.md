# P3-STAGE-001-D development reference-pilot engineering evidence

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-D` / GitHub #241 |
| Evidence status | engineering pilot accepted; semantic reference remains open |
| Run revision | `41b96f7e92a247135798b69850c429cd84011468` |
| RX6800 Job | `stage_dbb4e1e983704a7b8e7c111365f0e88f` |
| Run | `stage-reference-pilot-20260908T101800Z` |
| Manifest | `sha256:8656ccf634bfce1d1a4343c61084d2b744bd2cedcddc7cb162d4aadf887cdf64` |
| Knowledge/data scope | `retrospective_dwd_record_view`; development only, 2020 pilot business rows |

## Result

The governed RX6800 stage worker completed the bounded development-only reference pilot successfully. The runtime identity matched the installed worker revision, the Job terminated with `state=succeeded` and `exit_code=0`, and the Worker verified the immutable result package before publishing the terminal status.

The final bounded pilot used a deterministic 32-security cohort. It read 11,808/11,808 admitted development business rows, built 10,026 StructureV2 episode observations, and produced exactly 120 T-only review contexts: 80 preregistered random contexts plus 40 preregistered boundary/rare contexts. The complete run took 52.208 seconds. Stage timing evidence attributes approximately 48 seconds to StructureV2 construction; cohort discovery, source projection, activity, sampling and settlement were each small relative to that cost.

The future ledger produced exactly 120 independent development settlements: 119 `settled` and 1 `censored`; settled outcomes were 91 `failure`, 19 `positive`, and 9 `neutral`. No `pending` or `unavailable` row was coerced into a successful outcome. These outcomes are a development distribution ledger only and are not semantic truth for stage 3 or stage 4.

## Reviewer-blind contract

The artifact contains 120 reviewer packets and a separate 120-row settlement ledger. Primary reviewer packets are generated from T-or-earlier Structure/Activity/history evidence and exclude engineering candidate verdicts, future prices, future settlement and strategy results by contract. The pilot keeps the reviewer pack and future ledger as separate artifact members and binds both into the immutable manifest.

The runner does **not** fabricate independent semantic judgement. Its terminal summary therefore correctly reports:

`INCONCLUSIVE_PENDING_INDEPENDENT_REVIEW`

with `resolved_contexts=0` and `required_contexts=120`. This is an intended semantic gate, not a failed engineering Run. Two distinct primary judgements per context are still required; disagreement requires one independent adjudicator. Rule weak labels, future outcomes, or this same assistant cannot be substituted for that reference authority.

## Runtime repair retained as evidence

The earlier 256-security/two-year pilot exceeded the 600-second Worker wall budget before publication. A bounded read-only timing probe showed that 32 securities already produced thousands of eligible contexts and that StructureV2 construction dominated elapsed time. The accepted pilot therefore reduced the feasibility cohort to 32 securities and calendar 2020 while preserving the frozen 120-context 80/40 sample budget. The final governed run reproduced the expected approximately 52-second end-to-end runtime.

A separate Worker Git-fetch blocker was traced to an invalid system OpenSSH config file reached only from the hardened systemd context. Quanthus did not weaken host SSH permissions. Worker Git now invokes SSH with `-F none` and explicit read-only deploy-key/known-host bindings, isolating governed Job code retrieval from system SSH configuration.

## Claim ceiling

This evidence accepts the D engineering substrate only: deterministic sampling, reviewer-blind T-only packets, development-only future settlement, artifact verification, runtime bounds, and review-ledger governance. It does not claim that any of the 120 contexts has an independent semantic label, does not qualify stage 3/4, does not authorize classifier training on fabricated labels, and does not open 2022-2024 arbitration or 2025 permanent-blind data.

`P3-STAGE-001-D` therefore remains open until the independent reference ledger is resolved. Downstream E implementation may be prepared fail-closed, but result-bearing semantic learning must refuse to execute without a complete accepted reference ledger.
