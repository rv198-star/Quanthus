# Strategy Historical Replay

| Field | Value |
| --- | --- |
| Parent | `../02-backtest-engine.md` |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | strategy-neutral replay accepted; three baseline suite accepted; governed five-unit development accepted; normal research boundary and formal historical bootstrap migrated to role-scoped offline research bundles |
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

The continuous PIT frame reader consumes only a single immutable bundle that includes the
authoritative exchange calendar, daily execution/tradability facts, normalized corporate actions,
the same-version corporate-action exception ledger, and their exact source revisions. It verifies
required schemas and identity bounds, enumerates complete SSE/SZSE calendar sessions rather than
inferring them from bars, and returns a lazy frame sequence instead of materializing the full market
into Python objects. `build_replay_spec` binds the run to the exact bundle snapshot id.

The decision view is keyed by the bundle's opaque economic `security_id`; a strategy must use that
value and must not synthesize an id from exchange plus stock code. The daily universe is the source
of candidate rows. Missing execution evidence remains a visible, non-executable row rather than
being dropped. T+1 sessions and corporate actions are loaded only by the runner-owned frame source.

Normal research uses `StrategyReplayADSFrameReader`, which is authorized against one physical ADS
scope and also supplies its checksum-bound benchmark and policy reference. It binds the ADS content
digest and exact manifest SHA into feature panels and run manifests. A decision callback receives
no paths; any access to a runner-only table during `decide()` fails the capability check. The legacy
`HistoricalReplayFrameReader` snapshot entry remains hidden and warning-bound solely for frozen P3
acceptance reproduction.

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
average invested-capital ratio, average holding sessions, the net PnL contribution of the largest
three closed trades, and return after statically removing that contribution. A benchmark-bound
research suite additionally reports the same-period buy-and-hold return and strategy excess return.

Total return and drawdown use system wealth including the external pool. Fixed-unit capital-day
return is total PnL divided by target notional and replay-session count. Gross turnover is the sum
of absolute fill gross amounts divided by initial system equity. Exposure is the share of replay
sessions in which the unit carried capital at any point. Volatility uses the sample standard
deviation of consecutive system-equity returns; Sharpe uses the same returns, 252 periods, and the
declared zero risk-free assumption. Average invested capital is the arithmetic mean, across every
replay execution day including empty days, of terminal position market value divided by total
system wealth including the external pool.

The top-three robustness metric ranks closed trades by net PnL, sums at most the largest three, and
subtracts that amount from total system PnL before dividing by initial equity. It reports the
removed count and amount. This is a static contribution subtraction; it does not delete decisions,
rerun the capital path, or replace the constrained counterfactual work owned by `P3-ATTR-001`.

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
`config/p3-baseline-features.research.v1.json` and the versioned strategy catalogues. Catalogue v2,
`config/p3-baseline-strategies.research.v2.json`, is the CLI default. It uses one CNY 100,000 fixed
unit, one security at a time, at most 30% of available cash per entry, whole-position entry/exit,
T-close decisions, and the accepted T+1-open P2 matcher. Its 21% sizing buffer covers a possible
20% T+1 price-limit opening gap plus transaction costs.

Catalogue v1 remains explicitly loadable and digest-locked as a rejected extreme comparison. Its
full-capital sizing and selection of market-wide momentum/breakout extremes caused near-total loss
in the first development run; it is evidence of what was tested, not an alternate current default.
V2 additionally bounds momentum, breakout distance and volume, and rebound RSI; filters listing
history, liquidity, volatility, and entry-day return; and prefers lower-volatility candidates.

The baseline runner ranks eligible securities deterministically and resolves quantity from T-close
price with a configured cash buffer and 100-share lot. Current T executability is evidence for
candidate eligibility, not a claim that T+1 will fill; the P2 matcher still owns the later result.
Missing held-position features produce hold, while maximum holding remains an independent exit.
No add/reduce, label, expert, state model, sector filter, email transport, or broker interface is
enabled.

The CLI `run-baseline-strategy-suite` first opens one explicitly authorized development ADS bundle,
materializes one lineage-bound vectorized feature panel, then runs each strategy through
`run_strategy_replay`. The frozen P3 catalogue still rejects every date outside 2012-2021;
building isolated arbitration and permanent-blind bundles does not grant this fixture permission to
open them. A future approved evaluation command would additionally require the distinct OS
principal, reader role, and physical path; no command may infer or combine scopes. The final T+1
execution must remain inside the authorized development partition. The panel's full-scope
continuity filter must extend through that final T+1 execution session even though strategy
decisions stop at T; a narrower panel fails before replay. Results are research-only, declare
`execution_authorized=false`, and retain a zero-return cash baseline; profitability is not a
delivery gate for this infrastructure slice. Its canonical JSON includes
fill-operation counts split into buys and sells plus closed-trade counts. A co-generated Markdown
scoreboard shows those counts with win rate, cumulative return, PnL, ending equity, maximum
drawdown, annualized volatility, fees, turnover, time in market, average invested capital, average
hold, payoff ratio, profit factor, Sharpe, top-three robustness, benchmark return, and excess
return. Total return is non-compounded and is never labeled as CAGR.

The ADS profile supplies an offline, checksum-verified benchmark curve; the hidden `--benchmark`
argument exists only for frozen snapshot reproduction. The current broad-market control is CSI 300
with explicit instrument identity `sh000300`; ambiguous `000300` is rejected because it can route
to the wrong market identity. The artifact records source, source revision, acquisition time, close
observations, and its canonical digest. The runner does not fetch market data. It requires a
benchmark observation for every strategy-equity date and
computes close-to-close buy-and-hold return from the initial equity date through the last T+1
execution date. Excess return is strategy total return minus that benchmark total return.

The full-capital comparison measures the opportunity cost of leaving wealth in cash. A separate
capital-matched diagnostic uses each strategy's terminal invested-capital ratio known at one close
for the following benchmark close-to-close return and assigns zero return to the remaining cash.
Its dated weight path is checksum-bound and its post-initial mean must reconcile exactly to average
invested capital. Capital-matched excess diagnoses selection and timing under the same capital-use
path; it never replaces the full-capital benchmark or uses a future strategy weight.

The first real corpus also contains versioned action exceptions and action-like price-scale changes
without normalized economic evidence. The research suite therefore excludes any affected identity
for the complete requested development scope before strategy ranking. This run-scope completeness
filter is versioned and reported as selection bias; it is not presented as a point-in-time signal
or an acceptable rule for later official evaluation. The execution reader still blocks the exact
anomalous date so bypassing the feature panel cannot create a false held-position mark.

## 7. Governed five-unit replay

`run-governed-portfolio-replay` composes the accepted modules without creating another matcher.
It initializes five fixed CNY 100,000 P2 ledgers and corresponding P3 Empty lifecycles. For every
T-close frame it reviews active positions, creates at most one immutable exit order per position,
evaluates every bounded candidate through facts, labels, experts, governance, and action mapping,
reserves available units under P0 concentration limits, and then consumes T+1 P2 outcomes. Adds
and reductions remain unsupported.

A sell that cannot execute is evaluated once with all consecutive future sessions and cached as
one order path. Other units continue through the global daily replay while that unit remains
Exiting. Each carried session consumes its own order-status evidence; the eventual fill consumes
the P2 fill and enters Closed. Five following authoritative sessions are required before
Closed-to-Empty. A bonus-share action during a carried sell currently fails closed because the
lifecycle's carried transition requires an unchanged quantity; cash dividends remain supported.

Portfolio equity sums each unit's cash, marked position value, and external pool. Time in market is
active unit-days divided by total available unit-days. Average invested capital is the arithmetic
mean of aggregate close-marked position value divided by aggregate system wealth on every replay
execution day. The report preserves all one-unit diagnostics and adds operation counts, full and
capital-matched CSI 300 comparisons, top-three contribution ratio, static return after removing
the top three closed trades, four required breakdowns, validation gates, and a reconciled
opportunity funnel. The calendar-year cut uses actual year-start/year-end portfolio equity; the
other cuts classify closed trades by entry breadth, holding duration, and exit source.

The frozen 2012-2021 first run produced 230 closed trades, 31.91% return, 22.49%
capital-matched excess, 19.11% return after removing the top-three closed-trade contribution, and
4.75% maximum drawdown. It also reports -83.01% full-capital excess because average invested
capital was only 6.88%. All five ledgers reconcile to CNY 659,547.35; one final Trial position
explains the 231 buys versus 230 sells. These are candidate results, not acceptance of a new
matching engine or authority to access later validation partitions.

## 8. Accepted implementation

| Concern | Governing path |
| --- | --- |
| Strategy/frame/result contracts and runner | `src/quanthus/backtest/strategy_replay.py` |
| Versioned benchmark curve and comparison | `src/quanthus/backtest/benchmark.py`, `tests/unit/test_benchmark.py` |
| Stateful buy and held-position mark reuse | `src/quanthus/backtest/engine.py` |
| Multi-trade metrics and failure boundaries | `tests/unit/test_strategy_replay.py` |
| Hand-reconciled oracle | `tests/fixtures/golden/p3_strategy_replay.v1.json` |
| Acceptance evidence | `docs/acceptance/p3-strategy-replay-slice-20260728.md` |
| Upstream corporate-action contract and corpus audit | `src/quanthus/corporate_action_normalization.py`, `src/quanthus/corporate_action_corpus.py`, `docs/acceptance/p1-corporate-action-normalization-audit-20260728.md` |
| Continuous replay-bundle completeness and integrity gate | `src/quanthus/backtest/historical_bundle.py`, `tests/unit/test_historical_replay_bundle.py`, `docs/acceptance/p3-historical-replay-bundle-gate-slice-20260728.md` |
| Continuous PIT frame reader and real-corpus audit | `src/quanthus/backtest/historical_frames.py`, `tests/unit/test_historical_frame_reader.py`, `docs/acceptance/p3-historical-frame-reader-slice-20260729.md` |
| Baseline feature panel, strategies, and development suite | `src/quanthus/backtest/baseline_research.py`, `tests/unit/test_baseline_research.py`, `docs/acceptance/p3-baseline-strategy-suite-20260729.md`, `docs/acceptance/p3-baseline-strategy-suite-v2-20260729.md` |
| Baseline benchmark and robustness metrics | `docs/acceptance/p3-baseline-benchmark-metrics-20260729.md` |
| Capital-matched benchmark | `docs/acceptance/p3-capital-matched-benchmark-20260729.md` |
| Backtest sanity diagnostics | `docs/acceptance/p3-backtest-sanity-diagnostics-20260729.md` |
| Governed five-unit orchestration and metrics | `src/quanthus/backtest/portfolio_replay.py`, `tests/unit/test_portfolio_replay.py` |
| Governed policy, labels, experts, industry input, and first development result | `config/p3-governed-strategy.research.v1.json`, `src/quanthus/governed_strategy.py`, `src/quanthus/industry_mapping.py`, `docs/acceptance/p3-governed-strategy-development-20260729.md` |
| Offline research-bundle publication, role authorization, and normal replay boundary | `src/quanthus/strategy_replay_ads.py`, `config/strategy-replay-ads.v1.json`, `tests/unit/test_strategy_replay_ads.py`, `tests/contract/test_strategy_ads_boundary.py`, `../01-data-foundation/strategy-replay-ads.md` |

The integration slice additionally publishes `StrategyReplayResult.audit_dict()` together with
the immutable P2 run through `AuditEventStore.persist_strategy_replay`. The replay manifest binds
strategy/version, decision/equity/trade counts, analysis checksum, run-manifest checksum, and event-
stream checksum under one atomic READY boundary. A replay package cannot silently point to a
different execution stream. Evidence:
`docs/acceptance/p3-strategy-replay-integration-slice-20260728.md`.

## 9. Residual work

The accepted baselines remain comparison controls, not a promoted P3 policy. Candidate v1's first
immutable 2012-2021 run passed its predeclared performance gates, and an independent full
invocation reproduced its panel, result, and scoreboard byte-identically. This closes only its
development delivery gate. The evaluator implements only its named registry; it is not a generic
formula language. Partial/add/reduce actions, intraday matching, valid new blind evidence,
constrained contribution reruns, shadow decision production, and production promotion remain
open.
