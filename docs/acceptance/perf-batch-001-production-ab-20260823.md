# PERF-BATCH-001 Production A/B Acceptance - 2026-08-23

## Scope

This record closes `PERF-BATCH-001` / Issue #191 after two independent production Jobs proved the
job-local prerequisite-reuse contract on the complete 2012-2021 development scope. It accepts one
Direct-DWD projection plus two distinct scope-bound TMP Feature Panels per composite Job. It does
not authorize a cross-Job cache, a new ADM, multi-process Event scanning, arbitration, or sealed
partition access.

## Frozen execution identity

- Worker revision: `a582843eb75b8629c1d02c4ebbd3c6b9361475c0`
- Governed input digest:
  `sha256:4bf6f1182e439c947263eda2a7378fe4214cd93ae63c2df9e3941d79c0620505`
- DWD Base: `dwd-base-202607-v2.1`
- Applied correction: `dwd-base-202607-v2.1-correction-20260813-001`
- Development start: `2012-01-04`
- Decision end: `2021-12-30`
- Execution end: `2021-12-31`
- Cross-Job cache: disabled
- Event Worker count: one

The two TMP panels are intentionally different semantic identities. Label Corpus consumes the
execution-scope panel through `2021-12-31`; Comparison Report consumes the decision-scope panel
through `2021-12-30`. Both Jobs independently rebuilt all prerequisites from the governed DWD
input.

## Production Jobs

| Evidence | Run A | Run B |
| --- | --- | --- |
| Job | `job_cd5f51d475fa463daa68c8698bc6b849` | `job_bd7296ad3255433f8e17b4bb26fd3b85` |
| Run | `run_cd5f51d475fa463daa68c8698bc6b849` | `run_bd7296ad3255433f8e17b4bb26fd3b85` |
| State | `succeeded` | `succeeded` |
| Provider materializations | 1 | 1 |
| Feature Panel materializations | 2 | 2 |
| Runtime receipt total | 7,434.593 s | 7,433.624 s |
| PostgreSQL Job wall time | 7,442.590 s | 7,441.387 s |
| Event Library stage | 7,135.535 s | 7,123.325 s |
| Maximum RSS | 21,145,882,624 bytes | 21,036,838,912 bytes |
| Candidate events | 36,820 | 36,820 |
| Executable / not executable | 36,022 / 798 | 36,022 / 798 |
| Settled three-class events | 34,641 | 34,641 |
| Report acceptance | pass | pass |

Each runtime receipt records `provider_materializations=1`,
`feature_panel_materializations=2`, `cross_job_cache=false`, and `adm_published=false`. Label and
Report consumed their supplied scope-bound panels; neither rebuilt a panel internally.

## Deterministic artifact reconciliation

A recursive byte comparison inside the production Event Worker volume covered every file below
`event-library/`, `label-corpus/`, and `comparison-report/`. All three package trees were byte
identical between Run A and Run B. Runtime receipts, logs, and the Run wrapper were excluded because
they intentionally contain operational measurements rather than governed package content.

| Identity | Both Runs |
| --- | --- |
| Event content digest | `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac` |
| Event manifest SHA | `sha256:b53b418b09bf4dca998ad34f807f8cf9406bf0e1fc11a63ae52be3f18b2525d1` |
| Label content digest | `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580` |
| Label manifest SHA | `sha256:885fae78f427dfd80efaf8b6459274fbfbc82bd6b84fb027c7dba7bc11ff5cff` |
| Report content digest | `sha256:d4fe89b80d8d2b1322a99669935a9eff1758c7857cd5f2af06607347e99db158` |
| Report manifest SHA | `sha256:58989cf01dc03b3a63abbfc3cee70396751a5067a0fbad7ed0faaf21ac07b8fa` |

## Performance decision

The matched pre-change production Jobs on revision
`063724a1fb07f36d7975271966befb871d3cf378` took 7,739.274 s and 7,751.412 s by PostgreSQL wall
time, averaging 7,745.343 s. The accepted Jobs average 7,441.989 s, an improvement of 303.354 s or
3.92 percent. This satisfies the measured improvement gate, but the gain is deliberately described
as modest: the historical 7,241.599 s best remains faster than either accepted Job.

The new telemetry locates the remaining bottleneck. Event Library scanning averages about 7,129 s,
or 95.9 percent of receipt wall time, while each Feature Panel build takes about 24 s. Peak RSS also
remains above the desired 16 GiB operating ceiling. Issue #195 owns Event-scan algorithm and memory
optimization; those concerns do not invalidate the accepted job-local reuse boundary.

## Acceptance decision

`PERF-BATCH-001` is accepted because:

- each independent Job built one verified Direct-DWD projection and exactly two scope-bound TMP
  Feature Panels;
- cross-scope reuse and cross-Job caching remained disabled;
- Event, Label, and Report counts and governed identities were unchanged;
- all files in the three governed package trees reproduced byte identically; and
- matched recent production wall time improved without changing policy or data scope.

Issue #195 remains the active follow-up for material Event-scan speed and memory improvement.
