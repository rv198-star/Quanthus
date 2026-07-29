# P3 Backtest Sanity Diagnostic Record

| Field | Value |
| --- | --- |
| Evidence status | targeted data/engine sanity checks passed; v1 extreme losses reproduced without costs |
| Evidence date | 2026-07-29 |
| Scope | 2012-01-04 through 2021-12-30 decisions and final 2021-12-31 execution/mark |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md` |
| Trigger | Investigate whether the rejected v1 near-total losses came from data, execution costs, repeated orders, or equity calculation |
| Bound snapshot | `p1-historical-replay-20260729-v1`, `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |

## 1. Decision

Three targeted diagnostics found no evidence that the rejected v1 near-total losses were created by
the P1 historical input, P2 cost-unit interpretation, repeated per-bar entry orders, corporate-
action accounting, or the strategy-replay performance calculation:

1. A single-security buy-and-hold sentinel passed through the same continuous P1 frame reader, P2
   matcher, company-action application, ledger, daily marking, and performance reporter and gained
   rather than collapsed.
2. All v1 costs and slippage were set to zero and all three strategies still lost 94%-100%.
3. Original-cost and zero-cost equity curves contained no day with absolute account return above
   20%. The largest days reconciled to raw unadjusted OHLC and contained no same-day company action.

The evidence instead supports the simpler explanation: v1 repeatedly put almost the complete
remaining unit cash into one market-extreme candidate. Its trend and breakout trade distributions
had strongly negative means and medians. More than one third of their closed trades lost at least
10%, so repeated capital depletion can produce near ruin without one invalid daily jump.

This diagnostic does not prove every engine path or historical row correct. It does not promote v2,
turn one stock into a market benchmark, or authorize target-policy, paper, notification, or live
use. Every run used `official=false`; the suite payloads remained research-only with
`execution_authorized=false`.

## 2. Buy-and-hold engine sentinel

Economic identity `600519` was selected after the fact because it existed on the first replay date,
had no corporate-action exception, and exercised normalized company actions during the interval.
The diagnostic entered once from the first eligible T-close decision, sized with the same 21%
T-close buffer and 100-share lot, then emitted only hold decisions. It did not force an exit after
the development boundary.

| Measure | Result |
| --- | ---: |
| Fill operations | 1 buy, 0 sells |
| Entry gross | CNY 73,636.00 |
| Normalized company actions applied | 9 |
| Starting equity | CNY 100,000.00 |
| Ending equity | CNY 1,053,254.64 |
| Cumulative non-compounded return | +953.2546% |
| Maximum drawdown | 41.3533% |
| Total fees | CNY 22.09 |
| Final quantity | 484 shares |
| Dividend income | CNY 34,712.73 |
| Minimum/maximum daily account return | -8.6578% / +8.8454% |
| Days with `abs(return) > 20%` | 0 |

The exact run repeated with identical output. This is a data/engine sentinel, not a fair A-share
market comparator: `600519` is a post-selected winner and no PIT broad-index series is present in
the accepted bundle. A future strategy scoreboard still needs a genuine broad-market benchmark.

## 3. Zero-cost v1 counterfactual

The original v1 catalogue, feature registry, snapshot, dates, strategy code, matcher, ledger, and
position rules were retained. Only buy/sell slippage, commission rates, minimum commission, stamp
duty, and transfer fee were set to zero.

| Strategy | Original operations (B/S) | Original return | Original fees | Zero-cost operations (B/S) | Zero-cost return | Zero-cost fees |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend v1 | 270 (135/135) | -99.5779% | CNY 4,157.50 | 338 (169/169) | -99.3918% | CNY 0.00 |
| 20-session breakout v1 | 256 (128/128) | -99.7703% | CNY 2,572.35 | 286 (143/143) | -99.6159% | CNY 0.00 |
| Oversold rebound v1 | 557 (279/278) | -89.5632% | CNY 11,709.35 | 551 (276/275) | -94.5435% | CNY 0.00 |

Operation counts are path-dependent because removing costs changes remaining cash, lot sizing, and
whether a depleted unit can enter later candidates. This is the requested full rerun, not a fixed-
fill fee subtraction. Its conclusion remains decisive: zero costs do not restore any v1 strategy
to a normal range. The rebound run becomes worse because it survives along a different sequence of
negative-edge trades, not because zero fees directly reduce wealth.

No run entered on every bar. Zero-cost average holding periods were 12.03, 14.09, and 8.60 sessions,
and their respective closed-trade counts were 169, 143, and 275 over 2,430 account-return days.
The state machine allowed one position at a time and every completed trade reconciled one buy with
one sell; only rebound ended with one unmatched open buy and no pending exit.

## 4. Daily-return extremes and raw checks

Each distribution includes 2,430 consecutive account returns. Percentiles below are over absolute
daily returns.

| Cost case | Strategy | Minimum | Maximum | P99 absolute | P99.9 absolute | Days above 20% absolute |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Original | Moving-average trend | -11.4229% | +11.6440% | 9.7217% | 10.1151% | 0 |
| Original | 20-session breakout | -9.9711% | +10.1445% | 9.6359% | 9.9415% | 0 |
| Original | Oversold rebound | -9.9000% | +18.5088% | 9.4443% | 9.9470% | 0 |
| Zero cost | Moving-average trend | -13.1534% | +12.8563% | 9.8502% | 11.4124% | 0 |
| Zero cost | 20-session breakout | -10.0818% | +10.2787% | 9.7976% | 9.9755% | 0 |
| Zero cost | Oversold rebound | -9.7942% | +15.4916% | 9.3983% | 9.9891% | 0 |

Because no account day crossed 20%, there was no threshold-breach list to investigate. The ten
largest absolute days for each run were still joined back to raw unadjusted bars and normalized
actions. Representative checks:

- Original rebound on 2021-09-08 held `300220`: prior close CNY 12.91, open CNY 13.04, high/close
  CNY 15.49, raw close return +19.9845%, account return +18.5088%, no action.
- Original trend on 2012-07-06 held `300181`: prior close CNY 36.00, open CNY 35.29, high/close
  CNY 39.60, raw close return +10.0000%, account return +11.6440% after entering from the lower
  open, no action.
- Zero-cost trend on 2020-09-10 held `300337`: prior close CNY 10.90, open CNY 10.25, low/close
  CNY 8.72, raw close return -20.0000%, account return -13.1534%, no action.

All inspected marks used `observed_close` and matched the raw bar. No diagnostic extreme required an
inferred price, future adjustment, or hidden corporate action.

## 5. Trade-distribution explanation

| Strategy | Closed trades | Mean trade return | Median trade return | Trades at or below -10% | Trades at or above +10% |
| --- | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend v1 | 135 | -3.5461% | -6.7404% | 50 | 22 |
| 20-session breakout v1 | 128 | -4.4060% | -8.1238% | 51 | 17 |
| Oversold rebound v1 | 278 | -0.4374% | +0.1207% | 34 | 24 |

These realized trade distributions, combined with v1 full-capital sizing and single-name exposure,
are sufficient to explain the observed capital depletion. The original profit factors of 0.6228,
0.3490, and 0.7427 independently agree that gross winning PnL did not cover gross losing PnL.

## 6. Artifact bindings

| Artifact | SHA-256 |
| --- | --- |
| Buy-and-hold diagnostic JSON | `1ec7e4f55db0beadad76a04b3c410a75762cb282481caa43fa4b0105fa121850` |
| Zero-cost policy JSON | `f76a4ab88d19e1df361757928be0292cd5e530f2d1531faa5ca1dae31596d57f` |
| Zero-cost suite JSON | `350a52fc31a6195e90055558edf951f301a5336a6d968acab0a9d8a6ef77fbd0` |
| Zero-cost scoreboard Markdown | `abd2e65c242ad2207ae5c49cdf92d0933986da9d19b067c57b258d95d6fca862` |
| Original/zero-cost return-extreme JSON | `1fd88fe8e0232b4f32fe8b37f380e54de171a3a3880aabc7400065dd548a9bc3` |
| Zero-cost trend audit | `150ea5daa87dbc9be609971d1df0f78915f9c2e68c08ce4bf82970afee7d95a0` |
| Zero-cost breakout audit | `20bed151cc008271fac080db3d4d4dc38add9267c9f720307e75b8df6b10f6d2` |
| Zero-cost rebound audit | `55415291463583b1930cef3dded1ba642aa976cef624b86a5bd632a862b6d5f7` |

Generated diagnostics, replay audits, feature Parquet, and market data remain outside Git.
