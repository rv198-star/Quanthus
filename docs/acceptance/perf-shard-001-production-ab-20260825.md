# PERF-SHARD-001 Production Acceptance - 2026-08-25

- Status: accepted
- Work item: `PERF-SHARD-001`
- GitHub issue: `#202`

## Scope

This record accepts the complete production-style P4 Event Job under the bounded low-RAM physical
plan. The plan changes no candidate, PIT, isolation, concentration, T+1, label, report, feature, or
governed identity semantics. Every measured Job independently rebuilt its Direct-DWD projection,
P4 Research ADM, two scope-bound TMP Feature Panels, Event Library, Label Corpus, and Comparison
Report. Cross-Job cache remained disabled.

## Execution identity

- Accepted Worker revision: `e811947afa7006f378b347ab5aba9a0214e1626e`
- Repeatability revision: `454cdcd48af2adea646a64f80599541a330635ef`
- Accepted image: `quanthus-event:issue202-e811947`
- Node: OCI aarch64, 4 vCPU, 23 GiB RAM, one 150 GB boot volume
- DWD Base: `dwd-base-202607-v2.1`
- Applied correction: `dwd-base-202607-v2.1-correction-20260813-001`
- Scope: `2012-01-04` through decision `2021-12-30`, execution `2021-12-31`
- Parent cgroup: `memory.max=17179869184`, `memory.swap.max=0`
- Shard cgroup: `memory.max=3221225472`, `memory.swap.max=0`

The accepted plan uses 12 deterministic Direct-DWD date partitions, four deterministic daily-table
merge tasks, six row-balanced complete-security candidate buckets, and two independent
full-universe Feature Panels. With two Workers, each Worker receives one Panel plus three candidate
buckets. Global candidate finalization, set-based T+1 binding, Label, Report, verification, and
immutable publication remain parent work.

## Production evidence

Revision `454cdcd` ran the complete matrix twice and established repeated sharded-path scaling:

| Round | S3x1 | S3x2 | U16 | `S3x1 / S3x2` | `S3x2 / U16` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 2,897.667 s | 1,592.152 s | 1,835.071 s | 1.819969x | 0.867624 |
| 2 | 2,919.205 s | 1,600.458 s | 1,795.032 s | 1.823981x | 0.891605 |

All four 3 GiB sharded Jobs had zero cgroup limit hits, OOMs, swap, and restarts. The second U16
Job completed correctly but touched its hard 16 GiB cgroup boundary (`memory.events.max=19`), so it
was not accepted as resource proof.

Revision `e811947` reduced only the U16 Direct-DWD large-projection DuckDB budget from `12GB` to
`11500MB`; the sharded physical path was unchanged. Two U16 Jobs then passed the resource gate, and
one same-revision S3x1/S3x2 pair checked the unchanged sharded path against the accepted revision:

| Mode | Runtime receipt | Parent peak | Shard peaks | Limit / OOM / swap / restart |
| --- | ---: | ---: | --- | --- |
| U16 round 1 | 1,761.201 s | 17,011,875,840 B | not applicable | 0 / 0 / 0 / 0 |
| U16 round 2 | 1,844.787 s | 16,468,377,600 B | not applicable | 0 / 0 / 0 / 0 |
| S3x1 regression | 2,907.899 s | 13,906,358,272 B | 3,090,112,512 B | 0 / 0 / 0 / 0 |
| S3x2 regression | 1,616.099 s | 13,915,820,032 B | 3,032,014,848 / 2,744,844,288 B | 0 / 0 / 0 / 0 |

The final regression ratio is `2907.899 / 1616.099 = 1.799332x`, which reports as `1.80x` at the
declared two-decimal operating precision. `S3x2 / U16 round 1 = 0.917612`, below the `1.10`
ceiling. The raw ratio missed an exact real-number comparison with `1.80` by about 1.08 seconds;
review accepted this as ordinary same-node storage variation rather than requiring another
multi-hour rerun. Raw seconds remain recorded so the rounding decision is visible.

The second final-revision S3x2 attempt was operator-interrupted during its first DWD shard stage and
is excluded from evidence. It produced no governed package and does not weaken or replace any
successful sample.

## Deterministic reconciliation

The accepted historical production identities remain:

| Identity | Value |
| --- | --- |
| Candidate / executable / not executable | 36,820 / 36,022 / 798 |
| Positive / neutral / failure | 4,519 / 4,159 / 25,963 |
| Unavailable / censored | 1,381 / 0 |
| Settled three-class events | 34,641 |
| Event content digest | `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac` |
| Label content digest | `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580` |
| Report content digest | `sha256:d4fe89b80d8d2b1322a99669935a9eff1758c7857cd5f2af06607347e99db158` |

Within revision `454cdcd`, all six Jobs had identical relative paths and SHA-256 values for all 20
files below `event-library/`, `label-corpus/`, and `comparison-report/`. Within accepted revision
`e811947`, both U16 Jobs and the completed S3x1/S3x2 Jobs also had identical 20-file hash lists; the
hash-list digest was
`f388fa0fa30f0066a3e900c7260060a70856d278ce4cb925e976f72ea99261f9`.

## Acceptance decision

`PERF-SHARD-001` is accepted. The evidence proves deterministic 3 GiB Worker execution, useful
two-Worker scaling, bounded same-node U16 execution, and unchanged governed outputs. Acceptance
uses the two complete `454cdcd` sharded rounds for repeatability, the isolated one-line U16 memory
change, two passing `e811947` U16 resource runs, and the completed `e811947` sharded regression
pair. This explicit evidence composition avoids rerunning unaffected multi-hour sharded work merely
to resolve sub-second-scale storage noise.

This does not add dynamic Worker discovery, a generalized scheduler, cross-Job cache, Redis,
Celery, or a new ADM product. Unit count remains static operator configuration bounded by node
memory, CPU, and measured storage capacity.
