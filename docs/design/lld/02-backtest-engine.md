# LLD 02: Event-Driven Backtest Engine

| Field | Value |
| --- | --- |
| Phase | P2 |
| Design status | approved |
| Delivery gate | accepted-conditional |
| Implementation status | accepted for daily research; bounded P3 strategy replay accepted; target intraday activation blocked |
| Gate source | `config/p2-gates.v1.json` |
| Governing requirements | FR-BT-001 through FR-BT-007, FR-AUDIT-001/002/005 |

## 1. Goal

Act as a conservative and deterministic referee. Strategy code emits proposals; the engine
owns time, orders, fills, costs, capital, positions, and audit events.

### Scope and ownership boundary

P2 owns the dated event loop, order lifecycle, conservative matching, fill/cost calculation,
accounting ledger, run manifest, deterministic metrics, and golden replay. It consumes explicit
signals or proposals and must not implement P3 labels, experts, aggregation, strategy exits,
trial/add policy, concentration policy, or cooldown behavior. Those later decisions may call the
same engine but cannot be embedded in its matcher or ledger.

The engine is execution-profile aware. `close-to-next-open-v1` remains the active policy;
`operator-assisted-two-window-v1` is the approved target boundary but cannot activate before its
evaluation/cutoff schedule, intraday inputs, matching policy, and notification contract pass the
gates in `../execution-notification-profiles.md`. Its clock bounds are already approved as
`09:45-10:15` and `14:15-session close`. Profile support must not fork a second matcher or ledger.

The Pre-P2 conformance audit records that no P2 matcher, order, fill, or ledger implementation
exists at its evidence cutoff. `src/quanthus/contracts.py` contains shared `SnapshotRef`,
`CandidateSignal`, and `RunManifest` contracts only; the operational data-pipeline runner under
`src/quanthus/pipelines/` is not the trading event engine. P2 must establish one canonical
production ownership boundary under `src/quanthus` and must not create alternate notebook, script,
or test-only engines.

Evidence: `docs/acceptance/pre-p2-conformance-20260728.md`.

The first post-audit slice now establishes that production boundary under
`src/quanthus/backtest/`. It accepts one T-close buy signal, creates a next-session order, applies a
synthetic test-only open-price cost policy, records a fill and balanced monetary postings, marks a
daily snapshot, and emits a deterministic manifest. Its fixture values are not production policy,
and no work item below is complete merely because this bounded case passes.

First-slice evidence: `docs/acceptance/p2-first-vertical-slice-20260728.md`.

The second slice adds explicit open-execution states. A suspension or missing observation carries
no invented open/close price. Suspension, a limit-up buy with no buy-side liquidity, and missing
execution evidence cancel the buy for that session; an otherwise executable order with insufficient
unit cash is rejected. None of these paths may produce a fill or monetary posting.

Second-slice evidence: `docs/acceptance/p2-buy-no-fill-slice-20260728.md`.

The third slice carries one full-position sell through consecutive untradable sessions without
creating duplicate orders. Each session emits one status and one sourced mark; suspension or
missing evidence carries the prior mark instead of inventing a price. The eventual fill applies
synthetic sell costs, releases position cost through a balanced ledger, and settles profit or loss
against the finite external pool at unit close.

Third-slice evidence: `docs/acceptance/p2-carried-sell-slice-20260728.md`.

The fourth slice adds `aggregate_score` to the persisted Signal and freezes the same-snapshot
candidate order as score descending, then security code ascending. A batch cannot mix snapshot,
market date, execution profile, window, or side, and duplicate signal/security identities fail
before ranking. Candidate-to-unit allocation and concentration remain P3 responsibilities.

Fourth-slice evidence: `docs/acceptance/p2-candidate-order-slice-20260728.md`.

The fifth slice applies only PIT-resolved corporate-action facts: cash dividends produce balanced
cash/income postings, bonus shares change quantity without rewriting total cost, and the post-action
mark has its own source snapshot. A separate fixed-unit report reconciles entry/exit cash flows to
system wealth, capital-day return, external flow, and adjusted drawdown; compound NAV is forbidden.

Fifth-slice evidence: `docs/acceptance/p2-accounting-metrics-slice-20260728.md`.

The sixth slice atomically publishes immutable run event streams with manifest, whole-stream, and
per-event checksum evidence. Notification attempts form a separate append-only checksum chain,
must resolve to one persisted Signal, and cannot mutate the run stream or imply a Fill. The same
slice freezes synthetic-only decision cutoff, first-eligible, and expiry instants inside both
approved clock windows; it proves half-open expiry without enabling two-window matching. A
maximum-hold sell case proves that P2 preserves and executes an upstream exit reason without
deriving the strategy rule.

Sixth-slice evidence: `docs/acceptance/p2-audit-window-slice-20260728.md`.

The seventh slice composes entry, PIT corporate action, carried or filled exit, fixed-unit report,
and immutable publication into one historical round trip. It adds the frozen daily research cost
policy, maps resolved P1 daily/tradability/limit facts into directional execution states, persists
complete accepted/rejected/shadow Decision records, and bounds isolated hypothetical paths to 20
capital days with explicit non-official/non-aggregatable labels. Daily Signals now emit a distinct
OrderIntent before Order creation and preserve cancel-buy versus carry-sell policy.

Seventh-slice evidence: `docs/acceptance/p2-round-trip-observability-slice-20260728.md`.

The final conformance review accepts P2 conditionally for `close-to-next-open-v1` historical and
paper research. It opens P3 through that interface while keeping the target two-window profile and
forward email path fail-closed under `config/p2-gates.v1.json`.

Final evidence: `docs/acceptance/p2-conformance-acceptance-20260728.md`.

The bounded P3 replay layer now composes consecutive T-close reviews, T+1 P2 execution, held
position marking, portfolio corporate actions including actions during a carried sell, fixed-unit
state, and strategy-level performance into one deterministic research result. A strict adapter
maps already-resolved P1 daily evidence into frames without exposing execution sessions to the
strategy callback. The complete analysis result is atomically persisted with checksums bound to
the immutable P2 event stream. The layer defines no production strategy, label, score threshold,
holding period, or position size.

The upstream corpus bridge under `P1-DATA-004` now binds the action source, Eastmoney implementation
plans, raw bars, qfq corroboration, and PIT-visible factors. Its 2012-2025 v2 audit normalizes 35,157
rows and versions 272 per-security exceptions. Immutable snapshot
`p1-historical-replay-20260729-v1` now packages those rows with the authoritative calendar and
accepted daily execution inputs. It passes the real bundle gate for frame construction while
keeping execution authority false. Evidence:
`docs/acceptance/p1-corporate-action-normalization-audit-20260728.md`.

`load_historical_replay_bundle` now enforces that publication boundary. It validates the typed
snapshot manifest, rejects path traversal, verifies every materialized partition checksum, and
requires normalized actions plus an exact-count, same-lineage `security_fail_closed` exception
ledger. The older P1 source snapshot fails immediately on the three missing datasets; the loader
does not compose a calendar from another snapshot. The new replay snapshot passes the same loader
without weakening that rejection behavior. Evidence:
`docs/acceptance/p3-historical-replay-bundle-gate-slice-20260728.md`.

`HistoricalReplayFrameReader` now completes the real-corpus bridge. It exposes a lazy T-close
market view over the daily universe, keeps T+1 sessions and actions runner-only, maps action source
codes to economic identities, retains missing execution evidence as a fail-closed row, and enforces
the versioned exception ledger for both new entries and held positions. The full 2012-2025 frame
audit and a real daily round-trip smoke pass without granting execution authority. Evidence:
`docs/acceptance/p3-historical-frame-reader-slice-20260729.md`.

Strategy-replay design: `02-backtest-engine/strategy-replay.md`. Evidence:
`docs/acceptance/p3-strategy-replay-slice-20260728.md`.

Load `10-monitoring-attribution.md` for P2 decision/shadow-log and hypothetical-ledger contracts.
Load `05-position-execution.md` only to keep its P3 strategy-position lifecycle outside P2
accounting ownership.

## 2. Core event types

| Event | Required identity and payload |
| --- | --- |
| Signal | signal id, decision time/cutoff, profile/window id, security, action, unit, size, reason, decision version |
| Order intent | signal id, profile/window id, eligible interval, side, quantity, expiry, policy version |
| Order | order id, intent/signal ids, profile/window id, eligible session/time, side, quantity, state, policy version |
| Order status | transition, time, reason code, source snapshot |
| Fill | order id, price, quantity, costs, slippage rule, market state |
| Ledger entry | unit, cash/position/external-pool delta, fill/event reference |
| Daily snapshot | unit balances, positions, pending sells, marked value, manifest |
| Notification | notification/signal ids, profile/window id, content hash, attempt, terminal delivery state |

All ids are stable under replay. Append-only event records are the accounting source of truth.

## 3. Execution profiles and processing

### Active close-to-next-open profile

1. Apply corporate actions and session tradability.
2. Process carried sells and T-open eligible orders.
3. Emit fill, cancel, reject, or carry events.
4. Settle unit and external-pool ledger entries.
5. Mark positions and publish the daily ledger snapshot.
6. Consume the T-close decision snapshot.
7. Rank signals and create T+1 eligible orders.

No T-close signal may fill at the T close.

### Target two-window profile

On each eligible session the target profile exposes exactly two configured, non-overlapping action
windows: half-open `09:45-10:15` and `14:15-session close` intervals in `Asia/Shanghai`. Each
evaluation consumes only a snapshot no later than its decision cutoff, creates a window-bound
intent, and may simulate execution only at the policy's first eligible observation. An intent
expires or carries only as the versioned policy states; matcher code cannot choose this implicitly.

P2 acceptance uses a synthetic test-only policy with the approved clock bounds to prove generic
boundary handling. Its two windows also declare synthetic decision cutoff, first-eligible, and
expiry instants so PIT rejection and half-open expiry can be tested. These values are fixture
inputs, not the target profile's production cadence or fill policy. This does not activate the
target profile or authorize forward email until the evaluation schedule, matching semantics,
complete intraday source contract, and latency SLA are frozen.

## 4. Matching policy

- Under the active profile, buy fill reference is the next executable open plus versioned buy
  slippage and costs; sell uses the next executable open minus its versioned slippage and costs.
- A target-profile fill uses only the separately frozen window observation and may not reuse the
  bar that supplied information beyond the signal cutoff.
- An unfilled buy is cancelled that session and reconsidered only after a new close decision.
- An unfilled sell is carried until tradable and records exactly one status event per session.
- Orders use whole A-share lots for entry; exit odd-lot behavior must be an explicit exchange rule.
- Partial fills are outside the first engine version and must be represented as a rejected
  unsupported mode rather than silently rounded.

Fees, taxes, slippage, lot rules, and limit regimes are versioned inputs. Their values are not
hard-coded in strategy modules.

`config/p2-execution-policy.research.v1.json` freezes the accepted daily historical/paper baseline:
5 bps slippage per side, 0.03% commission with CNY 5.00 minimum, seller-side 0.05% stamp duty, and
0.001% transfer fee. These are explicit conservative research assumptions, not a claim about the
operator's future broker bill; P7 must calibrate and version actual-account costs before comparing
paper with real operations.

The P1 adapter consumes already PIT-resolved `bars_daily`, `tradability`, and price-limit facts.
Suspension discards placeholder prices; invalid, missing, non-executable, or policy-unknown rows
fail closed; an open at the upper/lower limit becomes a directional buy/sell block. The adapter
does not query vendors or infer state inside strategy code.

A `ZERO_ACTIVITY_OBSERVATION` may retain a static close for valuation while remaining
non-executable. P2 represents it as price-bearing `NON_TRADING_OBSERVED`: a new buy is cancelled
with `security_not_trading_observed`, while a held sell is carried and may mark the position from
the observed close. This does not convert the static observation into a fill price.

## 5. Capital units

P2 records accounting facts for each unit: available and reserved cash, settled and pending
quantity, cost basis, receivables, and external-pool transfers. `trial`, `dynamic`, `exiting`,
`closed`, cooldown, and their transition rules belong to the P3 position engine in LLD 05 and may
appear only as opaque upstream context in P2. Profit transfers to the finite external pool and loss
is replenished at close settlement if funds exist. If the pool is empty, the unit continues with
reduced notional and records the funding event.

Performance reports separate trade return, unit-capital-day return, total PnL, external cash
flow, and cash-flow-adjusted drawdown. They do not present a fixed-unit run as compound NAV.

## 6. Determinism

- Same-day candidates sort by aggregate score descending, then security code ascending.
- State transitions use explicit precedence and reason codes.
- Decimal/currency rounding rules are centralized and tested.
- Input snapshots and policy versions are immutable during a run.
- Execution profile, schedule version, and window ids are immutable and present in ordered events.
- Repeated execution produces identical ordered events and ledger balances.

## 7. Audit and counterfactual hooks

The engine records accepted and rejected signals from day one. Rejected signals feed a diagnostic
hypothetical ledger, but official risk/opportunity amounts come from a separate capital-constrained
strategy rerun using the same engine. A notification is linked to the persisted signal and remains
separate from Order/Fill state; manual execution cannot be inferred from email delivery.

Completed run streams publish atomically into an immutable run directory. The storage manifest
binds the original run manifest to the ordered event count and stream checksum; each event also
binds to its own content and predecessor checksum. Reusing a run id with different contents fails.
Notification identity and retry attempts publish separately and append-only; a retry keeps one
logical notification id and cannot replace an earlier attempt.

Accepted, rejected, and shadow Decisions share one immutable schema containing upstream expert
outputs, aggregate score, vetoes, constraints, proposed/final action, reason codes, snapshot and
policy versions, and any linked orders. Rejected and shadow records cannot link execution orders.
The diagnostic round-trip hook consumes explicit baseline entry/exit facts, is capped at 20 capital
days, ignores capital competition, and is marked non-official and non-aggregatable. Official
counterfactual money still requires a later full capital-constrained strategy rerun.

## 8. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P2-ENG-001 | Define profile-aware event schemas and deterministic event loop | P1 acceptance | Replayable daily and synthetic-window sessions |
| P2-POLICY-001 | Freeze fees, slippage, lot, matching, and execution-profile parameters | P1 acceptance | Versioned execution policy and schedule schema |
| P2-MATCH-001 | Implement profile-aware conservative matching and tradability handling | P2-ENG-001 | Open/window limit and suspension golden cases |
| P2-LEDGER-001 | Implement unit and external-pool double-entry ledger | P2-ENG-001 | Exact balance reconciliation |
| P2-ORDER-001 | Implement cancel-buy and carry-sell lifecycle | Matcher | No duplicate or lost order events |
| P2-AUDIT-001 | Implement run manifest and decision/notification/hypothetical logs | Event schemas | Full provenance and no-implied-fill query |
| P2-METRIC-001 | Implement fixed-unit performance reports | Ledger | Hand-calculated report fixture |
| P2-ACCEPT-001 | Build daily and synthetic two-window golden scenarios | All P2 work | Cent-exact and deterministic rerun |
| P3-REPLAY-001 | Implement strategy-neutral daily historical replay | P2-ACCEPT-001 | Multi-trade golden replay and metric reconciliation |

## 9. Acceptance

The minimum corpus covers a normal trade, unfilled limit-up buy, carried limit-down sell,
suspension, cash shortage, same-day candidate tie, corporate action, a maximum-hold sell intent
generated outside P2, window cutoff/expiry, and notification without fill. Every output is
traceable to its signal, profile/window, and snapshot and reconciles to the expected ledger.

### Current accepted slices

#### Normal buy

`P2-NORMAL-BUY-001` covers one whole-lot buy under `close-to-next-open-v1`. The governing code is
`src/quanthus/backtest/`; its strict synthetic policy and hand-calculated expected values live in
`tests/fixtures/p2/execution-policy.synthetic.v1.json` and
`tests/fixtures/golden/p2_normal_buy.v1.json`. Unit tests prove T/T+1 separation, cent-exact costs
and balances, zero-sum monetary postings, byte-identical replay, explicit non-lot rejection, and
the prohibition on labeling a synthetic run official.

#### Buy no-fill

`P2-BUY-SUSPENDED-001`, `P2-BUY-LIMIT-UP-001`, `P2-BUY-MISSING-EVIDENCE-001`, and
`P2-BUY-CASH-SHORTAGE-001` cover conservative buy termination. Market unavailability produces
`created_to_cancelled`; order/capital invalidity produces `created_to_rejected`. All cases publish
a zero-position daily snapshot, preserve cash exactly, and emit no Fill or LedgerEntry. The golden
oracle is `tests/fixtures/golden/p2_buy_no_fill.v1.json`.

#### Carried sell and unit settlement

`P2-CARRIED-SELL-001` covers `created_to_carried`, `carried_to_carried`, and
`carried_to_filled` on one stable order id. It proves observed versus carried-forward marks,
synthetic commission/stamp/transfer fees, balanced trade and external-pool postings, profit
transfer, and explicit underfunding when a loss exceeds the available pool. The golden oracle is
`tests/fixtures/golden/p2_carried_sell.v1.json`.

#### Candidate tie

`P2-CANDIDATE-TIE-001` proves that equal-score candidates use security code ascending and that
permuting input order cannot change the ranked result. The golden oracle is
`tests/fixtures/golden/p2_candidate_tie.v1.json`; P2 does not use this contract to implement P3
industry, daily-open, or position-allocation policy.

#### Corporate actions and fixed-unit report

`P2-CORPORATE-ACTION-001` proves cash-dividend PIT timing, balanced postings, explicit ex-right mark
lineage, and equity continuity; a bonus-share case preserves total cost while changing quantity.
`P2-FIXED-UNIT-REPORT-001` reconciles trade PnL and total wealth exactly and reports trade return,
capital-day return, external cash flow, and cash-flow-adjusted drawdown without compound NAV.

#### Audit, notification, window boundary, and maximum-hold exit

`P2-AUDIT-NOTIFICATION-001` proves atomic idempotent run publication, checksum failure on changed
evidence, persisted-Signal linkage, ordered notification retries, and zero implied fills.
`P2-WINDOW-CUTOFF-EXPIRY-001` proves that knowledge newer than the synthetic decision cutoff fails,
that the intent becomes eligible only at its configured instant, and that `10:15`/session close are
expired rather than eligible. `P2-MAXIMUM-HOLD-SELL-001` passes an upstream `maximum_hold_exit`
reason through the generic sell path and hand-reconciles the resulting fill and settlement.

#### Integrated daily replay, P1 adapter, Decision log, and hypothetical path

`P2-ROUND-TRIP-REPLAY-001` produces one 18-event entry/dividend/exit stream, atomically persists it,
and reconciles CNY 27.48 total PnL to fixed-unit metrics. `P2-P1-EXECUTION-ADAPTER-001` freezes six
normal, suspension, directional-limit, invalid, and zero-liquidity mappings. `P2-DECISION-LOG-001`
proves accepted/rejected/shadow immutability and no shadow order authority. The diagnostic variant
uses the same explicit path but is capped at 20 capital days and cannot be official or aggregated.

Conditional P2 acceptance excludes target two-window matching, forward/live intraday inputs,
partial fills, an email-provider adapter, and actual operator-report reconciliation. It also does
not implement P3 candidate allocation, position lifecycle, exit decisions, or capital-constrained
attribution. These remain governed by their activation blockers and later-phase work items.
