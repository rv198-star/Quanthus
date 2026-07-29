# P3 Capital-Matched Benchmark Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | passed; capital path reconciles exactly to average invested capital |
| Evidence date | 2026-07-29 |
| Scope | Runner 2.2.0 over the frozen 2012-2021 development partition |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-STRAT-001` |
| Prior evidence | `docs/acceptance/p3-baseline-benchmark-metrics-20260729.md` |

## 1. Decision

The full-capital CSI 300 comparison remains the primary opportunity-cost benchmark. Runner 2.2
adds a strategy-specific diagnostic benchmark that invests the strategy's close-known terminal
capital ratio in CSI 300 for the following close-to-close period and leaves the remainder in
zero-return cash. It never uses the following day's strategy weight for the preceding return.

This comparison answers a different question from full buy-and-hold: whether security selection
and timing added value under the same audited capital-use path. It does not excuse idle capital,
replace the full-capital benchmark, or claim a directly executable ETF portfolio.

## 2. Contract

For equity dates `d[0] ... d[n]`, strategy terminal invested ratios `w[0] ... w[n]`, and benchmark
closes `b[0] ... b[n]`, the diagnostic wealth path is:

```text
wealth[0] = 1
wealth[t + 1] = wealth[t] * (1 + w[t] * (b[t + 1] / b[t] - 1))
```

The initial weight is zero because the replay has not yet executed an entry. Every weight is in
`[0, 1]`, every date must exist in the checksum-bound `sh000300` curve, and the mean of weights
after the initial point must equal the strategy report's average invested-capital ratio. The exact
dated weight path has its own canonical SHA-256 digest.

## 3. Development Result

| Strategy | Total return | Full CSI 300 | Full excess | Capital-matched CSI 300 | Capital-matched excess |
| --- | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend v2 | +25.865810% | +114.915498% | -89.049688% | +24.827416% | +1.038394% |
| 20-session breakout v2 | +35.409620% | +114.915498% | -79.505878% | +31.648416% | +3.761204% |
| Oversold rebound v2 | -34.419370% | +114.915498% | -149.334868% | +5.936558% | -40.355928% |

The first two controls show small positive selection/timing increments while still carrying large
full-capital opportunity costs. The rebound control fails both comparisons. These observations do
not promote any baseline strategy and do not open arbitration or blind partitions.

## 4. Audit Bindings

| Bound artifact | Digest or SHA-256 |
| --- | --- |
| CSI 300 curve digest | `sha256:55a04023c356d539febe1ca3b851af6c4e2d1c21ee09e0371ba3225033ecb146` |
| Feature-panel report | `sha256:dfec044daf87a4df1d36e989893573da37268545171acdcd340b0806157eedf9` |
| Runner 2.2 suite digest | `sha256:78439797c02f35511718b49c936114efd6f3dfaf27523aac0ff807ae6f5e2a90` |
| Suite JSON file | `7e848a4adeb4c7bde8691229a29dacf14950a9db0d80ae63bb2a6ccb49ae8de6` |
| Scoreboard Markdown file | `6a2d30acbdf4a7edbda17dbe3b4f477ac99815ecbbda4c161581c3b3aa5366d9` |

| Strategy | Capital-path digest | Canonical result digest | Audit artifact SHA-256 |
| --- | --- | --- | --- |
| `baseline.ma-trend` 2.0.0 | `sha256:f3c83d21488499ac400eef665bde616e11b0cc11c7e7fefffa12118b6eaaf1cc` | `sha256:4e7e4de23a1dad968ea3e9d734a6948ef65f645120163a93b32b653c29e5523f` | `0cd171c139df2651ea02455e51fdf44c5165a7032a36a8397e4816355e6f661d` |
| `baseline.breakout20` 2.0.0 | `sha256:704ad8f977ac594716a06067285d0f2d801ed1ba0190605cc16fd4436b05cc48` | `sha256:68618599618099eb50bb413023b1ff206debad3981a653e2e6de21899385fff5` | `7ba5888aa81e97300e0c61064b73088ddd70f06488c58cc0ab6cf393003029b5` |
| `baseline.oversold-rebound` 2.0.0 | `sha256:f3c17aca64a72b5f19fe8d88602c92d3267a675e3dd0dbd232aa0ea6b5097ff6` | `sha256:527d58b60b06cfa51756970cd7b66bbaf6ebdfd7b63463aeb61d2833b34d621a` | `42542647d591b0695f6bac67d94b1512362f8d80eba3f76ee0b299d66af0cabd` |

Generated feature, benchmark, suite, scoreboard, and audit files remain outside Git.

## 5. Verification Boundary

Unit coverage locks weight bounds, date ordering and coverage, prior-close weight application,
capital-path digesting, average-weight reconciliation, null behavior for generic replays, and both
scoreboard comparisons. This increment changes no strategy rule, signal, fill, fee, or ledger.
