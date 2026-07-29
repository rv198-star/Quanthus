# Strategy Historical Replay

| Field | Value |
| --- | --- |
| Parent | `../02-backtest-engine.md` |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | strategy-neutral replay plus research-only indicator panel and three baseline suite accepted |
| Continuous corpus gate | accepted by `p1-historical-replay-20260729-v1` |
| Governing work | `P3-REPLAY-001` |
| Governing requirements | FR-BT-001/002/003/004/006, FR-AUDIT-001/002 |

## 1. Purpose

Run a versioned strategy over consecutive historical PIT snapshots before any concrete P3 policy is
promoted. The replay layer owns daily orchestration and analysis only. P2 remains the sole owner of
Signal to OrderIntent to Order to Fill conversion, matching, fees, ledger entries, and settlement.

## 2. Information boundary

Each `StrategyReplayFrame` pairs one T-close `SnapshotRef` with the eligible T+1 execution date.
The `HistoricalStrategy` callback receives only:

- the T snapshot reference;
- the T decision time;
- the current immutable `UnitState`;
- a read-only `StrategyDecisionView` whose current rows and history end at T close.

It never receives T+1 open/close prices, execution state, corporate-action application output, or
future frames. The runner validates strictly increasing frames and requires each execution date to
equal the next frame's decision date. This makes look-ahead a contract failure rather than a
convention inside each strategy.

`P1StrategyReplayFrameEvidence` and `strategy_replay_frame_from_p1` form the strict typed adapter
from already-resolved P1 daily evidence. They reuse `market_session_from_p1`, sort securities
deterministically, reject execution-date drift and duplicate securities, and never expose the T+1
evidence object to the strategy callback. They are not a raw Parquet query layer and perform no
coverage, suspension, limit, or tradability inference.

The continuous PIT frame reader, `HistoricalReplayFrameReader`, consumes only a single immutable
bundle that includes the
authoritative exchange calendar, daily execution/tradability facts, normalized corporate actions,
the same-version corporate-action exception ledger, and their exact source revisions. It verifies
required schemas and identity bounds, enumerates complete SSE/SZSE calendar sessions rather than
inferring them from bars, and returns a lazy frame sequence instead of materializing the full market
into Python objects. `build_replay_spec` binds the run to the exact bundle snapshot id.

The decision view is keyed by the bundle's opaque economic `security_id`; a strategy must use that
value and must not synthesize an id from exchange plus stock code. The daily universe is the source
of candidate rows. Missing execution evidence remains a visible, non-executable row rather than
being dropped. T+1 sessions and corporate actions are loaded only by the runner-owned frame source.

Corporate-action normalization completes before frame construction. Source stock codes map through
the versioned code-validity table to one economic identity. For a held position, the frame applies
cash and bonus actions before the open and derives the ex-right mark as
`(prior_mark - cash_per_share) / (1 + bonus_ratio)`, rounded to the price tick. A ledger exception
blocks a new entry for that security/date. If a held security reaches a blocked interval, replay
aborts instead of inventing an action, carrying an incorrect quantity, or converting missing
evidence to a sell decision.

The first action vocabulary is deliberately smaller than the future position policy:
`no_action`, `enter`, and full-position `exit`. An entry while already positioned fails explicitly;
adds, reductions, multi-unit allocation, concentration, cooldown, and numeric hold rules remain
owned by `P3-POLICY-001`, `P3-POS-002`, and `P3-POS-003`.

## 3. Runtime sequence

For each decision frame the runner:

1. calls the exact strategy and rule version against the PIT context;
2. applies declared held-portfolio corporate actions before the eligible open;
3. converts an actionable decision to a quantity-resolved `BacktestSignal`;
4. sends the signal through the accepted P2 daily execution path;
5. marks a held position at the session close or carries the last sourced mark when unavailable;
6. preserves one sell order across untradable sessions without invoking new strategy decisions;
7. advances the same fixed-capital unit, external pool, cumulative expenses, and realized result;
8. emits one canonical result with decision trace, P2 events, equity curve, trades, and metrics.

A company action in the first sell-attempt frame is supported before the order quantity is
resolved. A company action after the exit has begun carrying now interleaves inside the same P2
sell sequence: cash dividends update the unit ledger, bonus shares emit an explicit pending-order
quantity adjustment, and the existing order id continues to the eventual fill. No retry or action
creates a second sell order.

## 4. Performance contract

All monetary results come from P2 fills, ledger-derived state, and sourced close marks. The report
contains initial and ending system equity, total PnL and return, fixed-unit capital-day return,
maximum drawdown, 252-period annualized volatility, zero-risk-free Sharpe ratio, closed/winning/
losing trades, win rate, payoff ratio, profit factor, gross turnover, total fees, exposure rate,
and average holding sessions.

Total return and drawdown use system wealth including the external pool. Fixed-unit capital-day
return is total PnL divided by target notional and replay-session count. Gross turnover is the sum
of absolute fill gross amounts divided by initial system equity. Exposure is the share of replay
sessions in which the unit carried capital at any point. Volatility uses the sample standard
deviation of consecutive system-equity returns; Sharpe uses the same returns, 252 periods, and the
declared zero risk-free assumption.

Profits transferred to the external pool remain part of total system wealth but are not redeployed
implicitly. The report declares `compound_nav=false`; it does not publish CAGR or imply compounded
capital. Undefined ratios remain null rather than zero.

## 5. External framework boundary

Mature event and vectorized frameworks may be used as design references or independent metric
oracles. They cannot produce an official Quanthus fill, ledger, or performance result unless their
data visibility, matching, cost, fixed-unit, and audit semantics first pass the same contracts.
The accepted P2 event stream remains the single official execution fact source; this avoids a
second backtester with different fill assumptions.

## 6. Research baseline suite

`P3-STRAT-001` supplies three deliberately simple comparison strategies: moving-average trend,
20-session price/volume breakout, and RSI oversold rebound. Their exact features, thresholds,
liquidity floor, lot size, cash buffer, holding limits, period boundaries, and versions live in
`config/p3-baseline-features.research.v1.json` and
`config/p3-baseline-strategies.research.v1.json`. They use one CNY 100,000 fixed unit, one security
at a time, whole-position entry/exit, T-close decisions, and the accepted T+1-open P2 matcher.

The baseline runner ranks eligible securities deterministically and resolves quantity from T-close
price with a configured cash buffer and 100-share lot. Current T executability is evidence for
candidate eligibility, not a claim that T+1 will fill; the P2 matcher still owns the later result.
Missing held-position features produce hold, while maximum holding remains an independent exit.
No add/reduce, label, expert, state model, sector filter, email transport, or broker interface is
enabled.

The CLI `run-baseline-strategy-suite` first materializes one lineage-bound vectorized feature panel,
then runs each strategy through `run_strategy_replay`. It accepts only the frozen 2012-2021
development partition, and the final T+1 execution must also remain inside that partition. The
panel's full-scope continuity filter must extend through that final T+1 execution session even
though strategy decisions stop at T; a narrower panel fails before replay. The 2022-2024
arbitration and 2025 permanent-blind partitions remain unopened by this command. Results are
research-only, declare `execution_authorized=false`, and compare with a zero-return cash baseline;
profitability is not a delivery gate for this infrastructure slice.

The first real corpus also contains versioned action exceptions and action-like price-scale changes
without normalized economic evidence. The v1 catalogue therefore excludes any affected identity
for the complete requested development scope before strategy ranking. This run-scope completeness
filter is versioned and reported as selection bias; it is not presented as a point-in-time signal
or an acceptable rule for later official evaluation. The execution reader still blocks the exact
anomalous date so bypassing the feature panel cannot create a false held-position mark.

## 7. Accepted implementation

| Concern | Governing path |
| --- | --- |
| Strategy/frame/result contracts and runner | `src/quanthus/backtest/strategy_replay.py` |
| Stateful buy and held-position mark reuse | `src/quanthus/backtest/engine.py` |
| Multi-trade metrics and failure boundaries | `tests/unit/test_strategy_replay.py` |
| Hand-reconciled oracle | `tests/fixtures/golden/p3_strategy_replay.v1.json` |
| Acceptance evidence | `docs/acceptance/p3-strategy-replay-slice-20260728.md` |
| Upstream corporate-action contract and corpus audit | `src/quanthus/corporate_action_normalization.py`, `src/quanthus/corporate_action_corpus.py`, `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |
| Continuous replay-bundle completeness and integrity gate | `src/quanthus/backtest/historical_bundle.py`, `tests/unit/test_historical_replay_bundle.py`, `docs/acceptance/p3-historical-replay-bundle-gate-slice-20260728.md` |
| Continuous PIT frame reader and real-corpus audit | `src/quanthus/backtest/historical_frames.py`, `tests/unit/test_historical_frame_reader.py`, `docs/acceptance/p3-historical-frame-reader-slice-20260729.md` |
| Baseline feature panel, strategies, and development suite | `src/quanthus/backtest/baseline_research.py`, `tests/unit/test_baseline_research.py`, `docs/acceptance/p3-baseline-strategy-suite-20260729.md` |

The integration slice additionally publishes `StrategyReplayResult.audit_dict()` together with
the immutable P2 run through `AuditEventStore.persist_strategy_replay`. The replay manifest binds
strategy/version, decision/equity/trade counts, analysis checksum, run-manifest checksum, and event-
stream checksum under one atomic READY boundary. A replay package cannot silently point to a
different execution stream. Evidence:
`docs/acceptance/p3-strategy-replay-integration-slice-20260728.md`.

## 8. Residual work

The accepted baselines are comparison controls, not the promoted P3 vertical-slice decision path.
`P3-LABEL-002`, `P3-EXP-002`, and `P3-AGG-002` may later provide governed strategy decisions. The
accepted evaluator implements only its named registry; it is not a generic formula language for
arbitrary registered features. Candidate-to-unit reservation competition is accepted separately
in LLD 05, but a multi-unit strategy replay,
partial/add/reduce actions, concrete P3 `DecisionEvent` production, opportunity funnels, and
multi-year acceptance remain open.
