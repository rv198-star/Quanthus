# Replay Worker production loop acceptance (2026-08-13)

| Field | Value |
| --- | --- |
| Status | accepted |
| Work items | `PK-WORKER-000` through `PK-WORKER-008` |
| Product URL | `https://quanthus.oci.061718.xyz/` |
| Production topology | Web, API, PostgreSQL, and one Replay Worker at concurrency one |
| DWD authority | `dwd-base-202607-v2.1` plus `dwd-base-202607-v2.1-correction-20260813-001` |
| Successful Job | `job_305422ed75964f3cac1e325c28aabb77` |
| Immutable Run | `run_305422ed75964f3cac1e325c28aabb77` |
| Run digest | `sha256:6acc41583875759e362dba6480e802b222aacbcc9b2fe5f74596a9efa753a89b` |

## Accepted producer loop

The production Job ran the current five candidates over the 2012-01-04 through 2021-12-30
development scope. It read the exact DWD Base and correction through the direct historical
provider, built only disposable Job-local projections, and executed fills through Quanthus P2.
No maintained per-strategy ADS or QuantDinger fill engine was introduced.

The Job completed at 100 percent with five of five candidates, registered one immutable Run, and
atomically switched five candidate active-evidence pointers. `active_switch_applied` is true. Each
new digest starts at `pending-review`; no review, governance, or execution authority is inherited
from an older digest. The sealed Run occupies approximately 401 MB.

| Candidate | Total return | Closed trades | Operations | Maximum drawdown |
| --- | ---: | ---: | ---: | ---: |
| MA trend | 34.466990% | 233 | 467 | 12.567365% |
| Breakout 20 | 40.618410% | 182 | 365 | 16.343125% |
| Oversold rebound | -28.630530% | 246 | 493 | 35.664118% |
| Governed candidate | 33.911738% | 235 | 472 | 5.344081% |
| QuantDinger bullish three lines | 59.628843% | 18 | 36 | 11.274136% |

These are fixed-unit Quanthus results and do not claim equivalence to QuantDinger's
total-equity compounding return.

## First production finding and correction

The first full-window Job failed atomically after recording three candidate successes and two
deterministic validation failures. It created no `platform_candidate_runs` row and changed no
active candidate pointer, proving that a partial five-candidate result cannot become active.

The governed candidate saw security `300315` in the direct DWD projection but not in its frozen
1,687-security static concentration mapping. DWD contained a current CSRC `I64` membership; the
defect was an implicit expansion of the versioned strategy universe. The corrected runner applies
the strategy-bound mapping identity set before candidate ranking while retaining direct DWD for
other strategies.

The QuantDinger candidate reached a price-bearing, non-trading execution record. The corrected
adapter preserves valid OHLC as signal and sizing evidence while mapping valid zero-activity rows
to `NON_TRADING_OBSERVED`; P2 then cancels execution with
`security_not_trading_observed`. Missing, invalid, company-action-blocked, and unexplained
transition evidence remain fail-closed as `MISSING_EVIDENCE`. Governed carried-sell schedules also
retain the `NON_TRADING_OBSERVED` state.

## Recovery and concurrency evidence

A controlled post-seal recovery drill created a generation-one sealed Run, simulated Worker loss,
and allowed the lease to expire. Generation two reclaimed the Job, validated all five sealed
evidence files, and published exactly once. The terminal Job was `succeeded` with `attempt=2` and
`claim_generation=2`. The isolated recovery database and artifact volume were deleted after the
drill; the formal production Run remained intact.

Real PostgreSQL acceptance also covered populated migration, input-aware idempotency, active-work
concurrency, two-consumer `FOR UPDATE SKIP LOCKED` competition, lease reclaim, cancellation,
bounded progress/errors, and stale-generation rejection. Production returned to one Worker after
the temporary concurrency exercise. The API has no production replay handler, the Replay Worker
has no public HTTP surface, and existing CN01/Hermes/OCI warehouse schedules retain node-local
`systemd` or operator-CLI ownership.

## Serving and deployment evidence

The API now serves catalogue summaries from PostgreSQL Run metadata instead of parsing the
approximately 380 MB governed evidence artifact. Immutable evidence reads are digest-cached and
active evidence is warmed before readiness. Observed production response times after this change
were approximately 0.22 seconds for the catalogue, 0.06 seconds for Replay Jobs, and 0.14 seconds
for QuantDinger detail; the catalogue had previously taken approximately 5.8 seconds.

| Image | Identity |
| --- | --- |
| API | `sha256:5132c4d764ecac945c5f02f285ac185762fff20f8336c5247498631c59cc8c4e` |
| Web | `sha256:4351c21ec918da1c43a306e14101fbd40a40cf8ae73f2d59ca23065158496976` |
| Replay Worker | `sha256:96d4bdfbf2d5d5dd0f2ed0e6dedf4373453158e36ca33ce905dcb93e533cfb1f` |

The public product root returned HTTP 200 after final deployment. PostgreSQL, API, Web, and the
single Replay Worker remained running; API and Web health checks were healthy.

## Verification

- Full Platform suite: 56 passed, 3 skipped. The skips require
  `QUANTHUS_TEST_POSTGRES_DSN`; the equivalent real-PostgreSQL paths were exercised separately.
- Focused serving suite: 31 passed.
- Ruff and mypy: passed.
- Public Playwright desktop Chromium and Pixel 7: 4 passed, including candidate detail, Run
  history, nonblank chart pixels, and responsive layout.
- Immutable Run validation, active-pointer compare-and-swap, failed-attempt atomicity, post-seal
  recovery, and temporary two-Worker competition: passed.

This acceptance closes the lean Replay Worker producer loop only. PK6 recovery packaging and final
platform cutover remain separate work under `PK-RECOVERY-001` and `PK-CUTOVER-001`. Worker
registry, event streams, notification wakeups, multiple always-on Workers, generalized priority
routing, and warehouse schedule migration remain deferred until measured need.
