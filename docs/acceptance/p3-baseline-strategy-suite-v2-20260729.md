# P3 Moderated Research Baseline Strategy Suite Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | moderated research baseline passed; two weak positive comparison controls and one failed strategy |
| Evidence date | 2026-07-29 |
| Scope | Three one-unit baseline replays over the 2012-2021 development partition using the existing PIT feature panel |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md`, `docs/design/lld/03-label-feature-system.md`, `docs/design/lld/05-position-execution.md` |
| Governing work | `P3-FEAT-002`, `P3-STRAT-001` |
| Supersedes for current baseline use | `docs/acceptance/p3-baseline-strategy-suite-20260729.md` remains the rejected v1 extreme-control evidence |

## 1. Decision

Catalogue v2 is the current development-only research baseline. It corrects the v1 experiment's
full-capital, market-extreme candidate selection by limiting each entry to 30% of available unit
cash, reserving a 21% T-close sizing buffer for a possible 20% T+1 opening gap plus costs, excluding
very new, illiquid, highly volatile, and extreme-return candidates, and bounding each strategy's
entry region. V1 remains replayable and immutable as evidence of a rejected extreme control; it is
not the CLI default and must not be used as the current strategy-quality reference.

The moving-average and breakout results are weak positive comparison controls, not investable
strategies. The oversold-rebound strategy failed despite winning slightly more than half its closed
trades because its average win/loss payoff ratio is below one. None of these results promotes a P3
target policy, opens the arbitration or blind partitions, permits paper/live notification, or grants
execution authority. Every payload remains research-only with `execution_authorized=false`.

## 2. Frozen run

```bash
python -m quanthus run-baseline-strategy-suite \
  --snapshot-dir <p1-historical-replay-20260729-v1> \
  --scope-start 2012-01-04 \
  --scope-end 2021-12-30 \
  --feature-panel <baseline-features.parquet> \
  --output <baseline-suite.json> \
  --scoreboard-output <baseline-scoreboard.md> \
  --audit-output-dir <strategy-audits>
```

The catalogue argument is omitted intentionally: v2 is the command default. Strategy decisions end
on 2021-12-30 and continuity evidence includes the final 2021-12-31 T+1 session. The command did not
open the 2022-2024 arbitration partition or the 2025 permanent-blind partition.

| Bound input | Digest |
| --- | --- |
| Historical replay bundle | `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |
| Feature registry | `sha256:4bcf56d2b6d32741f212dccebd4fcdd44596b836a7f11691462ae7190abcd798` |
| V2 strategy catalogue | `sha256:2c762c524440da8fd1b042f94b1bc8416a342f3e6acbda4a6ba182b21abfad31` |
| Feature-panel report | `sha256:dfec044daf87a4df1d36e989893573da37268545171acdcd340b0806157eedf9` |
| Suite result | `sha256:b62f43bf53d3bb8ae3289b4e2182bed747a96e1454e79b357090e04f02fa8ddb` |

## 3. Moderation contract

| Control | Frozen v2 value |
| --- | ---: |
| Maximum entry budget | 30% of available unit cash |
| T-close sizing buffer | 21% |
| Minimum listing history | 120 sessions |
| Minimum average 20-session daily amount | CNY 100,000,000 |
| Maximum 20-session annualized realized volatility | 50% |
| Maximum absolute entry-day return | 4% |
| Moving-average 20-session momentum | 2%-12% |
| Breakout distance above prior 20-session high | 0.25%-3% |
| Breakout volume ratio | 1.2-3.0 |
| Breakout maximum 20-session momentum | 20% |
| Rebound prior-session RSI entry band | 20-30 |

Candidate ranking now prefers lower realized volatility before strategy score. A regression case
sizes from a CNY 10 T-close and proves that a 20% T+1 opening gap still leaves the 2,400-share gross
entry below the CNY 30,000 budget. The real run's maximum observed entry gross was CNY 25,850.

## 4. Strategy scoreboard

Operations count actual P2 fills; `B/S` separates buys and sells. Closed trades require a matched
entry and exit. Each run ends with one open position, so it has one more buy than sell and no pending
exit. Return is cumulative over almost ten years and `compound_nav=false`; it is not CAGR or an
annualized-return claim. Exposure is the fraction of replay days carrying any position, not average
capital utilization. Turnover is cumulative gross turnover divided by initial equity.

| Strategy | Operations (B/S) | Closed trades | Win rate | Total return | Total PnL | Ending equity | Max drawdown | Ann. volatility | Fees | Turnover | Exposure | Avg hold | Payoff ratio | Profit factor | Sharpe |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend v2 | 481 (241/240) | 240 | 33.33% | +25.87% | CNY 25,865.81 | CNY 125,865.81 | 14.32% | 7.73% | CNY 6,245.47 | 11,241.09% | 96.46% | 9.73 | 2.492 | 1.246 | 0.347 |
| 20-session breakout v2 | 369 (185/184) | 184 | 33.15% | +35.41% | CNY 35,409.62 | CNY 135,409.62 | 15.31% | 7.91% | CNY 4,943.38 | 8,900.11% | 95.51% | 12.60 | 2.659 | 1.319 | 0.437 |
| Oversold rebound v2 | 515 (258/257) | 257 | 51.36% | -34.42% | CNY -34,419.37 | CNY 65,580.63 | 40.83% | 6.75% | CNY 5,367.06 | 9,540.07% | 91.15% | 8.60 | 0.669 | 0.706 | -0.614 |

The trend and breakout controls earn only about 2.6% and 3.5% simple cumulative return per year if
their total returns are divided by ten for orientation; that arithmetic is not CAGR. With no index
benchmark yet, high time exposure, and Sharpe below 0.5, neither establishes useful alpha. The
oversold result shows why win rate cannot be read alone: frequent small wins do not offset larger
losses and costs when payoff and profit factor are below one.

## 5. Risk and audit bindings

No strategy produced an absolute daily account return of 3% or more or a daily move of 10% or more.
Maximum observed entry gross was CNY 25,200 for trend, CNY 25,850 for breakout, and CNY 24,696 for
rebound. The open ending economic identities are `002926`, `601398`, and `688819`, respectively.

| Strategy | Canonical result digest | Audit artifact SHA-256 |
| --- | --- | --- |
| `baseline.ma-trend` 2.0.0 | `sha256:b5034776fb0b19de3506712ab351ec4ab7c102f6b87e450e50dc8777524d1be7` | `b83ce535fec4b188b6911f58c953212ff286b186fe9987744fd7443b3b8304d9` |
| `baseline.breakout20` 2.0.0 | `sha256:5ca682a93be7ad0f6486433e0bb6bd0350cdda2347aaa64417f6817f99217ac2` | `6c420b66ff91e7f250b8fa33e0fdd1bc91fc4f476d1270f2fbad1898aa3c7edb` |
| `baseline.oversold-rebound` 2.0.0 | `sha256:35885fd79ba5c8cf3bb8faff2c69f713d31f1f3b3008ae717130049cd134c8aa` | `da53e48fc8b60321e175dd2071977a60c4ec63397e419369666d0bf922744bbb` |

The canonical suite JSON artifact SHA-256 is
`16c2f0fc55ca062f63f53934ce1c775bc2fc49b5488cd276acdd674bd51df182`;
the shared Parquet feature artifact SHA-256 is
`cb10ee9a98a620c496da6a6683f48f930c7ce6707b9ed5d74736a4a1174c8039`.
Generated run artifacts and market data remain outside Git.

## 6. Verification

- V1 catalogue replay compatibility and its frozen digest remain covered.
- V2 schema bounds, moderated candidate filters, operation reconciliation, scorecard columns, and
  the 20% T+1 opening-gap sizing case have focused tests.
- The real feature-panel digest is unchanged, so the V2 comparison changes strategy selection and
  sizing rather than silently changing historical inputs.
- This acceptance is bounded to research controls. Benchmark comparison, robustness testing, and
  target-policy validation remain future work and must not reuse these development results as blind
  evidence.
