# P3 Research Baseline Strategy Suite Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded research infrastructure passed; all three strategies failed the cash comparison |
| Evidence date | 2026-07-29 |
| Scope | PIT daily feature panel and three one-unit baseline replays over the 2012-2021 development partition |
| Governing design | `docs/design/lld/02-backtest-engine/strategy-replay.md`, `docs/design/lld/03-label-feature-system.md`, `docs/design/lld/05-position-execution.md` |
| Governing work | `P3-FEAT-002`, `P3-STRAT-001` |
| Dependency evidence | `docs/acceptance/p3-historical-frame-reader-slice-20260729.md`, `docs/acceptance/p3-strategy-replay-slice-20260728.md` |

## 1. Decision

`P3-FEAT-002` and the bounded `P3-STRAT-001` research-control implementation pass. The accepted
result is a reproducible indicator and strategy-replay path, not an investable policy. All three
simple rules lost heavily and underperformed the declared zero-return cash baseline. They cannot
be described as profitable, promoted to paper or live use, or used alone as a meaningful future
model-performance hurdle.

Every suite and strategy payload is research-only and declares `execution_authorized=false`.

Profitability was explicitly not an infrastructure acceptance condition. The negative results are
retained rather than tuned after observation. Any strategy revision requires a new catalogue
version and a new development-run record. `P3-ACCEPT-001`, concrete labels and experts, the target
position policy, email transport, and execution authority remain open or blocked by their own
gates.

## 2. Frozen run

```bash
python -m quanthus run-baseline-strategy-suite \
  --snapshot-dir <p1-historical-replay-20260729-v1> \
  --scope-start 2012-01-04 \
  --scope-end 2021-12-30 \
  --feature-panel <baseline-features.parquet> \
  --output <baseline-suite.json> \
  --audit-output-dir <strategy-audits>
```

The requested dates are strategy decision dates. The materialized feature and continuity scope is
2012-01-04 through 2021-12-31 so the last 2021-12-30 decision's T+1 execution evidence participates
in full-scope quality exclusion. A narrower panel now fails closed. The command did not read the
2022-2024 arbitration partition or the 2025 permanent-blind partition.

| Bound input | Digest |
| --- | --- |
| Historical replay bundle | `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |
| Feature registry | `sha256:4bcf56d2b6d32741f212dccebd4fcdd44596b836a7f11691462ae7190abcd798` |
| Strategy catalogue | `sha256:573d53b0046823af0f613e2efbf2a51b4393686de63dc4ae67a6af35f7210c4c` |
| Feature-panel report | `sha256:dfec044daf87a4df1d36e989893573da37268545171acdcd340b0806157eedf9` |
| Suite result | `sha256:b062d9a741b796b02fbc40f081dad13beb9fe97f452a384b48750f43f9f3bcc8` |

## 3. Feature-panel evidence

| Measure | Result |
| --- | ---: |
| Observations | 7,724,219 |
| Feature-ready observations | 7,182,297 |
| Entry-eligible observations | 5,109,817 |
| Exception-active observations | 77,271 |
| Unexplained price transitions | 13,226 |
| Distinct economic identities | 4,677 |
| Full-scope continuity-excluded identities | 1,242 |
| Maximum absolute eligible daily economic return | 20.1794% |

The full-scope filter excludes any identity with a versioned action exception or an unexplained
transition anywhere in the run through the final execution session. It is a conservative way to
prevent a future unknown quantity change from corrupting an earlier position, but it uses
run-complete data-quality evidence. The report therefore declares
`data_quality_scope_selection_bias=true`. These returns are conditional research results, not PIT
all-universe performance estimates. Corpus repair, not inferred actions, is required to remove the
filter.

## 4. Strategy results

All runs use one CNY 100,000 unit, one security at a time, whole-position entry and exit, 100-share
lots, T-close decisions, T+1-open matching, and the accepted P2 costs. Return, drawdown, exposure,
win rate, and volatility are shown as percentages.

| Strategy | Return | Max drawdown | Ending equity | Closed trades | Fees | Exposure | Win rate | Profit factor | Sharpe | Volatility | Avg hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Moving-average trend | -99.5779% | 99.6037% | CNY 422.10 | 135 | CNY 4,157.50 | 66.0494% | 24.4444% | 0.6228 | -0.7640 | 54.6506% | 11.89 sessions |
| 20-session breakout | -99.7703% | 99.7703% | CNY 229.75 | 128 | CNY 2,572.35 | 73.4568% | 26.5625% | 0.3490 | -0.9529 | 51.9246% | 13.95 sessions |
| Oversold rebound | -89.5632% | 94.0276% | CNY 10,436.85 | 278 | CNY 11,709.35 | 97.2840% | 51.4388% | 0.7427 | -0.3584 | 41.4545% | 8.49 sessions |

The trend and breakout units end empty. The oversold-rebound unit ends with 500 shares of economic
identity `000829`, marked from the 2021-12-31 execution snapshot, and has no pending exit. This is a
normal finite-scope open position, not an implied sale after the development boundary.

An independent post-run scan found no daily system-equity move at or beyond 20%: observed extrema
across the three runs were -11.4229% and +18.5088%. The worst closed-trade returns accumulated over
multiple sessions rather than one discontinuous mark. This supports, but does not replace, the
reader's exact-date continuity gate.

## 5. Complete audit bindings

| Strategy | Canonical result digest | Audit artifact SHA-256 |
| --- | --- | --- |
| `baseline.ma-trend` 1.0.0 | `sha256:b2005c8a22888ee39218b9d71323cb0e4c260d1f9f19b87465e32aa6a56ac50a` | `bf0d3cef22adaaf2f865e373c130f85b4b7609f941b3d4fb4e8954d8bd15dfaf` |
| `baseline.breakout20` 1.0.0 | `sha256:556cdc54d8b52e395342a6691e7ec1f191b3f0be8c9b7def59f3fcf448665cd5` | `5c57d1d4f9f021e9f42e6bf78a0c4d2e4fda894f39dc87da2f102105b4e431fb` |
| `baseline.oversold-rebound` 1.0.0 | `sha256:3b367cce2f9f22d5135ea96dd45a7e287c9d57ae1ce8bf0e7c43fb26b2b5aab2` | `4c285cd9f12a134e9bf14a6821c7ec4e27312bd9fb54055e0e36d2f52628e69f` |

Each complete audit contains the bound P2 run manifest and ordered events plus daily decisions,
equity curve, closed trades, final state, pending-exit state, and performance report. The suite JSON
artifact SHA-256 is `69812ab955d0f3aef8d163f383414f28463837846c180e0ff65f3ee99056a342`;
the Parquet feature artifact SHA-256 is
`cb10ee9a98a620c496da6a6683f48f930c7ce6707b9ed5d74736a4a1174c8039`.
Run artifacts and market data remain outside Git.

## 6. Verification

- Synthetic cases prove split-date total-return continuity, future-known-action rejection,
  full-scope identity exclusion, final-execution-scope enforcement, development-partition locking,
  and deterministic three-strategy replay.
- The real panel and all three complete replay audits bind the same immutable bundle, feature
  registry, catalogue, P2 execution policy, and development dates.
- Focused unit tests, Ruff, and strict mypy pass after the final-execution boundary fix.
- Repository-wide `make check PYTHON=.venv/bin/python` passes doctor, compilation, all 342 tests,
  and the planning synchronization preview. Negative strategy returns do not invalidate the
  infrastructure evidence or establish strategy quality.
