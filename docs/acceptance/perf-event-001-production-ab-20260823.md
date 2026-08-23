# PERF-EVENT-001 Production A/B Acceptance - 2026-08-23

## Scope

This record closes `PERF-EVENT-001` / Issue #195 after two independent production Jobs proved the
optimized exact P4 Event scan on the complete 2012-2021 development scope. It accepts the
single-process scan optimization and generation-fenced internal progress path. It changes no
candidate, PIT, isolation, concentration, T+1, label, report, or governed identity semantics.

## Execution identity

- Worker revision: `120c6acf219fb62e0622333bfc3cc4ae66a78b78`
- Image: `quanthus-event:git-120c6ac`
- Governed input digest:
  `sha256:c889ec735a3537648bf28eef50736fca4a903a787ffe117110a30e094f0cdb9b`
- DWD Base: `dwd-base-202607-v2.1`
- Applied correction: `dwd-base-202607-v2.1-correction-20260813-001`
- Scope: `2012-01-04` through decision `2021-12-30`, execution `2021-12-31`
- Event scan worker count: 1
- Cross-Job cache: disabled

Both Jobs independently rebuilt the Direct-DWD projection, Event Research ADM, Event Library, two
scope-bound TMP Feature Panels, Label Corpus, and Comparison Report.

## Production Jobs

| Evidence | Run A | Run B |
| --- | --- | --- |
| Job | `job_0469e877393546fdb6ea41011e5972c0` | `job_8ba9423bd51e48ba952bab6ebd308994` |
| Run | `run_0469e877393546fdb6ea41011e5972c0` | `run_8ba9423bd51e48ba952bab6ebd308994` |
| State | `succeeded` | `succeeded` |
| Runtime receipt total | 2,074.582 s | 2,043.157 s |
| PostgreSQL Job wall time | 2,082.060 s | 2,051.034 s |
| Event Library stage | 1,764.741 s | 1,750.943 s |
| Maximum RSS | 20,854,906,880 bytes | 20,925,919,232 bytes |
| Candidate events | 36,820 | 36,820 |
| Executable / not executable | 36,022 / 798 | 36,022 / 798 |
| Settled three-class events | 34,641 | 34,641 |
| Report acceptance | pass | pass |

## Internal Event timings

| Stage | Run A | Run B |
| --- | ---: | ---: |
| ADM materialization | 72.329 s | 88.771 s |
| Candidate-row decode | 689.246 s | 684.629 s |
| Exact per-security scan | 393.218 s | 388.616 s |
| Global candidate finalization | 42.664 s | 42.720 s |
| T+1 entry partition | 563.131 s | 542.137 s |
| Package publication | 3.491 s | 3.430 s |
| Package verification | 0.213 s | 0.212 s |

PostgreSQL received bounded progress from ADM materialization through candidate scan, candidate
finalization, entry partition, publication, and verification. Candidate-scan progress reconciled
7,724,219 decoded rows, 7,626,395 evaluated rows, 4,679 security series, and 53,527 raw triggers in
both Jobs. Every accepted progress update remained conditioned on the active claim generation; the
temporary receipt was removed before Run sealing.

## Deterministic reconciliation

A recursive byte comparison inside the production Event Worker volume covered every file below
`event-library/`, `label-corpus/`, and `comparison-report/`. All three complete package trees,
including manifest and READY files, were byte identical between optimized Run A and Run B.

| Identity | Both Runs |
| --- | --- |
| Event content digest | `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac` |
| Event manifest SHA | `sha256:b505d0afc21a78f21cb5ed0181545ff1e084c612ea212d28a4dfc58e73bc6222` |
| Label content digest | `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580` |
| Label manifest SHA | `sha256:60446a00c9eb6d9cfa8d07782c284c8972cf688e2c76fb4bce3b20e765e7be5a` |
| Report content digest | `sha256:d4fe89b80d8d2b1322a99669935a9eff1758c7857cd5f2af06607347e99db158` |
| Report manifest SHA | `sha256:7d26f0e3d0cc1e122cd98c14dd59f3c8ee30c6975fbea85e2acf81de1566a30e` |

The three content digests and every non-manifest package file also equal the accepted revision
`a582843` baseline. Manifest and READY identities differ from that older revision only because the
governed code revision changed, as required.

Both optimized Runs selected the same leading features in the same order:

1. `risk.realized_volatility20`
2. `price.breakout20`
3. `price.sma20_to_sma60`

## Performance decision

The `PERF-BATCH-001` baseline Runs averaged 7,434.109 s by runtime receipt and 7,441.989 s by
PostgreSQL wall time. The optimized Runs average 2,058.870 s and 2,066.547 s respectively. Receipt
wall time decreased by 72.3 percent, a 3.61x speedup. Event Library time decreased from a 7,129.430 s
baseline average to 1,757.842 s, a 75.3 percent reduction and 4.06x speedup.

The speed result is material, but memory is not accepted as improved. Peak RSS remains about
19.5 GiB and therefore exceeds the desired 16 GiB deployment ceiling. Live Event candidate scanning
stayed below 1 GiB; the process-wide peak remained in shared provider or Feature Panel work. A 16
GiB cgroup limit must not be imposed until that separate path is measured and reduced or a fail-safe
resource policy is approved.

The optimized profile also exposes T+1 entry partition as the next Event hotspot at an average
552.634 s, about 26.8 percent of total runtime. Any follow-up must preserve the accepted P2-backed
entry evidence and manifest identities.

## Acceptance decision

`PERF-EVENT-001` is accepted because:

- exact `Fraction` thresholds, PIT checks, reason precedence, global isolation/concentration, and
  ordered publication remain unchanged;
- independent cold Jobs reproduced every file in all three governed package trees byte identically;
- accepted counts, content digests, outcome distribution, leading features, and report acceptance
  remain unchanged;
- production PostgreSQL progress now exposes bounded internal Event stages under generation
  fencing; and
- complete production runtime improved by 72.3 percent without adding processes, a cross-Job cache,
  Redis, Celery, or a new ADM.

Peak-memory reduction and any T+1 binding optimization remain separate follow-up work.
