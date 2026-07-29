# P3 Baseline Benchmark And Robustness Metrics Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | metric expansion passed; all three research controls underperform the broad-market benchmark |
| Evidence date | 2026-07-29 |
| Scope | Runner 2.1.0 over the frozen 2012-2021 development partition |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Governing work | `P3-STRAT-001` |
| Prior evidence | `docs/acceptance/p3-baseline-strategy-suite-v2-20260729.md` |

## 1. Decision

The baseline report now includes time in market, average invested capital, a genuine broad-market
buy-and-hold comparison, excess return, and return after statically removing the largest three
closed-trade PnL contributions. The three v2 strategy rules, parameters, signals, fills, and ledgers
are unchanged. This increment changes the report and its audit contract only.

The moving-average and breakout controls remain positive in absolute PnL but materially
underperform CSI 300. Their high time in market does not mean full capital deployment: the v2
position cap leaves average invested capital near 20%. The top-three subtraction also shows that
their small absolute gains are concentrated. These controls remain research-only and do not
promote a P3 policy, open arbitration/blind data, or authorize notification or execution.

## 2. Benchmark contract

The required CLI input is an offline, checksum-verified JSON curve. This run used explicit
instrument identity `sh000300`; ambiguous `000300` fails the suite contract. The curve contains
2,431 Tencent daily close observations and covers every strategy-equity date from 2012-01-04
through the final T+1 execution date, 2021-12-31.

| Benchmark field | Value |
| --- | --- |
| Benchmark | CSI 300 price index |
| Instrument identity | `sh000300` |
| Source | `tencent.appstock.kline` |
| Source revision | `sh000300\|day\|annual-2012-2021\|limit=640\|fields=date-open-close-high-low-volume\|v1` |
| Start close | 2,298.750 |
| End close | 4,940.370 |
| Buy-and-hold price return | +114.915498% |
| Curve digest | `sha256:55a04023c356d539febe1ca3b851af6c4e2d1c21ee09e0371ba3225033ecb146` |
| Artifact file SHA-256 | `2c4ec37eec58f3418dd6ff4b6f4a630d34a9f7a6f32784d92ecb73cb988ad3b0` |

This is a close-to-close price-index control. It does not add ETF fees, tracking error, or
reinvested constituent dividends. It is therefore a conservative broad-market comparison rather
than a claim about a directly filled ETF trade. Backtest execution remains fully offline; the
runner never fetches this curve from the network.

The normal reproducible CLI path is:

```bash
python -m quanthus run-baseline-strategy-suite \
  --snapshot-dir <p1-historical-replay-20260729-v1> \
  --scope-start 2012-01-04 \
  --scope-end 2021-12-30 \
  --feature-panel <baseline-features.parquet> \
  --benchmark <csi300-sh000300-2012-2021.tencent-v1.json> \
  --output <baseline-suite.json> \
  --scoreboard-output <baseline-scoreboard.md> \
  --audit-output-dir <strategy-audits>
```

The runner 2.1 evidence reused the already checksum-matched feature-panel Parquet and report rather
than rebuilding 7,724,219 rows. It still invoked the production `run_baseline_strategy_suite`
function, loaded the same bundle, policies, catalogue, registry, and benchmark curve, and rewrote
all suite and per-strategy audit artifacts.

## 3. Metric definitions

- **Time in market** is the existing exposure rate: replay sessions during which the unit carried
  a position at any point, divided by all replay sessions.
- **Average invested capital** is the mean of terminal daily position market value divided by total
  system wealth including the external pool. Empty days contribute zero.
- **Excess return** is strategy cumulative total return minus CSI 300 cumulative price return over
  the same equity endpoints.
- **Return ex top 3** ranks closed trades by net PnL, subtracts at most the largest three from total
  system PnL, and divides the remainder by initial equity.

The final metric is static contribution subtraction. It does not delete decisions or rerun the
capital path and does not satisfy the counterfactual attribution work in `P3-ATTR-001`.

## 4. Scoreboard

| Strategy | Total return | Time in market | Avg invested | CSI 300 | Excess return | Top 3 closed-trade PnL | Return ex top 3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend v2 | +25.865810% | 96.460905% | 20.328597% | +114.915498% | -89.049688% | CNY 31,963.28 | -6.097470% |
| 20-session breakout v2 | +35.409620% | 95.514403% | 18.411054% | +114.915498% | -79.505878% | CNY 34,422.80 | +0.986820% |
| Oversold rebound v2 | -34.419370% | 91.152263% | 18.738525% | +114.915498% | -149.334868% | CNY 7,459.53 | -41.878900% |

The trend control becomes negative after removing its best three closed trades. Breakout retains
only 0.99% simple cumulative return over almost ten years. Rebound remains materially negative.
These are robustness observations, not post-hoc strategy-selection criteria.

## 5. Audit bindings

| Bound artifact | Digest or SHA-256 |
| --- | --- |
| Historical replay bundle | `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |
| Feature-panel report | `sha256:dfec044daf87a4df1d36e989893573da37268545171acdcd340b0806157eedf9` |
| Feature-panel Parquet | `cb10ee9a98a620c496da6a6683f48f930c7ce6707b9ed5d74736a4a1174c8039` |
| V2 strategy catalogue | `sha256:2c762c524440da8fd1b042f94b1bc8416a342f3e6acbda4a6ba182b21abfad31` |
| Runner 2.1 suite digest | `sha256:1bd62e8c4bb314ac35f7f7d50d11be1f2e822238e4d12732a1824252a8a4e9fb` |
| Suite JSON file | `894460bb4d8de5f8f07a8eb66d5d76aaa5950a7ce455c49f67393e3e9aee0b17` |
| Scoreboard Markdown file | `01efdd4d6f2d21ace37680debd156890ed119fbb86718ad2c061a8605cbcc3e6` |

| Strategy | Canonical result digest | Audit artifact SHA-256 |
| --- | --- | --- |
| `baseline.ma-trend` 2.0.0 | `sha256:b554e8348ad81395da2082ef5a8b807e15b08229a95d3f3bfdd43abf33314ee8` | `5f5107f1646be1171aa06a5c54cf19c3acd48e46ddbc1a854e78c022bf1a770f` |
| `baseline.breakout20` 2.0.0 | `sha256:f560d91a66746271aa44c0f843a95515e41dfca8962a9a6125734c16d5b11dbf` | `000f383dd84bd5d98c3b88b6c197a071e38db2c55c7e2f9db6d38d523291a980` |
| `baseline.oversold-rebound` 2.0.0 | `sha256:93b8b6eebc207969787445fd89366f3eaae4f43f32b720dd52648aed7389348f` | `970737b95b4a9e9b9fbd3aaeab19b5f4bbf520c2cbadc136590a171602483216` |

The runner 2.0 diagnostic pass and runner 2.1 official pass have byte-equivalent benchmark and
feature-panel payloads, performance fields, and activity counts. Only the runner version and
version-bound audit digests differ. Generated market data and run artifacts remain outside Git.

## 6. Verification boundary

Unit coverage locks curve digest verification, strict date coverage, explicit `sh000300` identity,
average invested-capital arithmetic, top-three subtraction, null benchmark behavior for generic
replays, suite benchmark/excess output, and the Markdown columns. Repository conformance still
requires the full `make check` result recorded at handoff.
