# R5 clock-response research acceptance — 2026-11-12

Parent: [frozen clock-response design](../design/research-r5-clock-response-v1.md).
Work item: `P3-RESEARCH-R5-CLOCK-001` / #642; selection #640; Epic #608; milestone19.
Business reading: [R5 value brief](../work-items/research-r5-value-brief.md).

## Accepted result and resource decision

The independently accepted result is `supported / advance` under the prospectively
frozen distribution-level screen. Retain this evidence in the research allocator and
close this finite batch. Any next investment first names the consumer and actual
decision it can change. R4's fixed single-position capital use remains parked.

| Common-cohort statistic | Result, bp |
| --- | ---: |
| Primary T+1 open to T+2 open increment | 5.4078345361423175 |
| Year-stratified 20-session circular-block 95% interval | [0.7661418269799773, 10.064702068053016] |
| 2020 / 2021 primary increment | 5.946328695559574 / 4.945310507099279 |
| Intraday / overnight / multiplicative interaction | 6.364523444328183 / -0.9992880562234115 / 0.0425991480375377 |
| Primary as a fraction of common-cohort intraday | 0.8496841253623741 |

All predeclared gates pass: full-window mean >=5bp, each year's mean >0 and
bootstrap lower bound >0. There is no separate 5bp threshold for each year.
This is the already consumed 2020–2021 retrospective unadjusted-price diagnostic.
The label does not include second-open sell qualification, fees or economic
company-action normalization; these remain the responsibility of a qualified consumer.

## Scope, denominators and independent acceptance

The original closed [2%,12%] momentum band, all R2 baseline filters and disjoint
control are fixed. Both future sessions use the same venue's authoritative calendar,
listing/security identity and effective intervals. The three labels share one cohort.
Decision dates end 2021-12-29, market reads end 2021-12-31; the original final decision
is explicitly right-censored rather than moving the boundary.

There are 448 paired dates, 207 in 2020 and 241 in 2021, and 896 paired venue cells.
The audit denominator is 346,830 rows. The reason ledger preserves 431 candidate and
893 control right-censored rows, and the separate missing-identity exclusions.
The accepted reference reconciles all annual/group/date denominators, rather than
silently dropping incomplete dates from the audit population.

One formal DuckDB construction and one separate pandas/numpy construction completed.
The independent script does not import producer SQL/aggregation logic. Identity,
integer counts, dates, groups and branch agree; 32,442 result-JSON nodes were compared and the
maximum floating error was 3.469446951953614e-17, below the fixed 1e-10 tolerance.
The read-only `r5_design_review` role separately hashed 27 original evidence files
and recomputed the mean, yearly values and 2,000 bootstrap replicates from sealed
daily result series. Its result and resource interpretation agree with the main agent.

## Frozen identities and engineering checks

| Identity | Value |
| --- | --- |
| Research Git | `b0e3ee717f80c9be5889c4b49db486ba9da2e18b` |
| Request semantic digest | `cf9ac6c4eba4306b2ec6f7224a01eee1c6479ac7e795da8afb7b6c86d0957244` |
| Result semantic digest | `ff5d7e44d1a1ce8bc41a4cc18433fc2df8413f5d1cd90cc9581617c585531951` |
| Formal result bytes SHA256 | `13b4bfc4580263e3e5d63830ebec41cfc5f18a74cb22c2b14be044e8fcf6cdae` |
| Independent result bytes SHA256 | `923feea700ad6d130007e67e6bfb181fd6802c9b9be1de78e53bc7052145ff06` |
| Input HF revision | `rv192/quanthus-base25-sealed@1923c874b43d47e69190a6dc9546e6fd8f3d1ca3` |
| Input closure | 56 members, 187,921,175 bytes; digest `a2fe359ecff110eab7a139a5a197b538a71889a38cd8d7530201fbcf4c5d954c` |
| Runtime | Linux Python3.10.12, exact 25-package lock, environment digest `12494b39251d792ea01b5f41a285515ee719af2fcb2cfae8e5da658d354ea48f` |

Research Git passed 120 focused Linux tests without skips and complete `make check`:
9,152 passed / 35 skipped. Changed-file Ruff passed. Three targeted mypy diagnostics
in `bound_read`, `verify_package` and `guard_job` are existing baseline diagnostics:
their function ASTs are identical to the exact base. This is local Linux acceptance;
hosted CI remains unavailable under the repository policy.

Before market execution, independent review found the reference parent's missing
terminal resource check and a same-HEAD dirty-source identity seam. The targeted
replacement, three red-to-green terminal-overrun cases and dirty-source rejection
passed independent re-review at the frozen research Git. All failed probes remain
in the task evidence; no additional scientific attempt was spent.

## Publication recovery and retention

The first publication failed at its 180.1975s stage wall cap. The default command
environment lacked the existing HF credential and proxy settings; a bounded connection
probe timed out. Loading `/srv/agentdock/.config/quanthus/hf.env` and `hf-proxy.env`
restored private-repository access in 1.0805s. Credential values remain node-local.

The original computation Job's 1,800s clock had expired during review/recovery.
It was preserved. TPlan applied a reviewed, prospective transfer-only A2 with a
180s guard; it consumed the already frozen package and did not resume that Job.
The read-only recovery review accepted operator-command SHA256
`9b21096100c43de109426e7c158afac301dc0aac1d085c345455b9fc01658df1`.
The transfer used the versioned package/readback verifiers, a private 40hex parent
OID, CAS creation or matching-content verification, and an isolated A2 readback cache.

A2 completed in 49.0147s, peak RSS45,133,824B and scratch1,466,653B.
Live and terminal limits passed; decoded market rows were zero. Before/after SHA256
comparison preserved `job.json`, `attempts.json` and all ten frozen package files.
Both publication attempts remain in the cumulative cost; no third attempt was used.

| Published identity | Value |
| --- | --- |
| Private dataset and immutable commit | `rv192/quanthus-research-r1@1f4778a9eeda7e7c993878ed0d2760bfbbae258d` |
| Prefix | `runs/RESEARCH-R5-CLOCK-001/RESEARCH-R5-CLOCK-001-T1/A1` |
| Complete byte readback | 10 files, 1,461,246B |
| Package semantic digest | `48ec166284e5beb3737629e6e8cd21fc6f6a172103b59c8465e5799f228258a4` |
| Readback receipt SHA256 | `6d36bd6b931106724d1be9afffcb93ad6ee056d04ec7a865aa498bddb2f21413` |
| Long-term CN02 root | `/srv/agentdock/quanthus-ops/research-r5-clock-20261112/retained` |

The retained root owns the minimum durable result/reference, input/environment
identities, check logs, independent reviews, failed-publication diagnosis, recovery
receipts and final lifecycle sidecar. The immutable HF prefix keeps its original
ten-file inventory. Retention review date remains 2026-12-12, owner CN02 research
task maintainer; the HF review date is not an instruction to delete durable evidence.

## Cost, usefulness and source handoff

Formal64.2713s and independent62.4892s passed their frozen resource ceilings;
formal peak RSS3,389,853,696B, reference2,091,307,008B. Input network download was0B.
The original full check took877.4288s and focused check17.8649s. Initial transfer
failure and recovery are recorded separately above. Existing R1/R2/R4 attempts,
repairs and sample exposure remain cumulative; complete active work, Token and
billing cost are `unknown_not_zero`.

The useful change is a resolved population clock question and a retained evidence
branch. Compute reuse worked; design, semantic review, guard repair and delivery
dominated this small diagnostic's work. Reuse these assets for a named consumer
before spending on more parameters or Workers.

PR#643 owns source delivery. Research acceptance stays bound to the original Git;
current-main integration and its exact complete/focused-check closure are recorded
in the task's final source/lifecycle receipts. The current value brief and live
issue/milestone carry the final lifecycle state.

## Integrated-source complete-check receipt

Current main21ffd6b74638f57052942ca53e8c15e4b09eb6e5 was merged normally
into candidate3c0c39be5836ae94dc7224ce082fdf6a66296b58. A fresh complete Linux
`make check` at that candidate passed: **9,154 passed / 35 skipped**, exit0,
871.6814s. Log SHA256: `e7c96b20c7f6c1e6f4bf755932153cc2c60200a2759521c14b93716628dd0d05`.
The complete tested executable/config/test closure is retained as
`final-tested-closure.json`, SHA256
`9a026cde1d91b1e941540a73a133b28e69532fdabf8a714896a20524cb293b06`.

The nine frozen research entrypoints, request and test files remain byte-identical
to research Gitb0e3ee71. Final documentation/ledger-only commits reuse that complete
check after exact closure comparison and rerun the affected planning, append-only
and documentation checks. Final receipts name the actual source head and ordinary
merge identity; no market calculation is repeated for source bookkeeping.
