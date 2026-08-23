# PERF-ENTRY-001 and PERF-MEM-001 Production A/B Acceptance - 2026-08-23

## Scope

This record closes the remaining P4 Event performance work after two independent complete cold
Jobs proved both the set-based T+1 evidence path and the hard 16 GiB memory boundary. It changes no
candidate, PIT, isolation, concentration, entry-evidence, label, report, feature, or governed
identity semantics.

## Accepted implementation

- Worker revision: `e7a29457208f2491b9459f7c467f4618d7aa4ca3`
- Image: `quanthus-event:git-e7a2945`
- DWD Base: `dwd-base-202607-v2.1`
- Applied correction: `dwd-base-202607-v2.1-correction-20260813-001`
- Scope: `2012-01-04` through decision `2021-12-30`, execution `2021-12-31`
- Event Worker: one PostgreSQL Worker and one child process
- Cgroup: `memory.max=17179869184`, `memory.swap.max=0`
- Cross-Job cache: disabled

T+1 requests are deduplicated into one Job-local DuckDB relation and resolved by one set-based join.
Direct-DWD uses four sequential DuckDB connection lifetimes: OHLCV, universe, execution limits,
then small projections and validation. Closing a connection also removes its private spill
directory. OHLCV and finalization use a 6 GB DuckDB budget; the measured large sort and window
projections use 12 GB; all use one thread and disable insertion-order preservation while retaining
the governed SQL ordering.

## Production Jobs

| Evidence | Cold Run A | Cold Run B |
| --- | ---: | ---: |
| Job | `job_40c9f8666f964e47b713bcb5e399d5b8` | `job_fc6b6a16db394b62a3c6dec5dd8b896e` |
| Run | `run_40c9f8666f964e47b713bcb5e399d5b8` | `run_fc6b6a16db394b62a3c6dec5dd8b896e` |
| State | succeeded | succeeded |
| Runtime receipt | 1,757.633 s | 1,842.631 s |
| PostgreSQL wall time | 1,765.611 s | 1,850.088 s |
| Direct-DWD projection | 410.326 s | 454.113 s |
| Event Library | 1,178.970 s | 1,212.651 s |
| T+1 entry partition | 31.730 s | 32.736 s |
| Process peak RSS | 13,893,562,368 B | 13,905,760,256 B |
| Cgroup memory peak | 16,144,474,112 B | 16,558,063,616 B |
| Cgroup limit hits / OOM / OOM kill | 0 / 0 / 0 | 0 / 0 / 0 |
| Swap peak | 0 B | 0 B |
| Container restarts | 0 | 0 |

The worst observed cgroup margin was 621,805,568 bytes. Process RSS retained about 3.27 GB of
decimal headroom at that point; the remaining cgroup charge was primarily reclaimable file cache.
Both cold Jobs recorded zero `memory.events max` increments. This acceptance applies to the frozen
single-Job topology and scope only; concurrency or a wider scope requires a new resource pilot.

## Deterministic reconciliation

Both Jobs independently rebuilt Direct-DWD, Event Research ADM, Event Library, two scope-bound TMP
Feature Panels, Label Corpus, and Comparison Report. A recursive SHA-256 comparison covered all 20
files under `event-library/`, `label-corpus/`, and `comparison-report/`: file sets and every file
byte were identical, with no differences.

| Identity | Both Runs and accepted baseline |
| --- | --- |
| Candidate / executable / not executable | 36,820 / 36,022 / 798 |
| Settled three-class events | 34,641 |
| Event content digest | `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac` |
| Label content digest | `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580` |
| Report content digest | `sha256:d4fe89b80d8d2b1322a99669935a9eff1758c7857cd5f2af06607347e99db158` |
| Result seal SHA | `sha256:60f6b9594415f6ecf9e185bde1b64f73aca787dad8c5921264dd0f00e91a9f88` |
| Label manifest SHA | `sha256:3410b9cb77618b245f1064cd55d3b61881fcc264ee3338e7a61c1ae79f1adf38` |
| Report manifest SHA | `sha256:eebb2dcd27c635409517621220085a25c71d59d2cfa6a17bfe30abc5f6ca1a6b` |

Both Runs retained the same leading features in the same order:

1. `risk.realized_volatility20`
2. `price.breakout20`
3. `price.sma20_to_sma60`

## Performance decision

T+1 entry partition averaged 32.233 seconds versus the preceding 552.634-second optimized average,
a 94.2 percent reduction. Complete runtime averaged 1,800.132 seconds versus 2,058.870 seconds,
a further 12.6 percent reduction despite serial bounded Direct-DWD projections. The memory change
trades additional provider time for a deterministic hard deployment bound and does not add a cache,
ADM, process, Worker, Redis, or Celery.

## Acceptance decision

`PERF-ENTRY-001` and `PERF-MEM-001` are accepted because focused tests and the full repository check
pass; two complete cold Jobs succeeded under the exact hard cgroup without swap, OOM, limit hits, or
restarts; all accepted counts and content identities remain unchanged; and all three governed
package trees reproduce byte identically.
