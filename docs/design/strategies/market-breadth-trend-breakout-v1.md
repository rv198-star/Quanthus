# Market-Breadth Trend-Breakout Candidate V1

| Field | Value |
| --- | --- |
| Parent | `README.md` |
| Phase | P3 |
| Design status | approved |
| Delivery gate | accepted |
| Implementation status | complete for candidate v1; development performance gates passed; independent full rerun byte-identical |
| Strategy id | `candidate.market-breadth-trend-breakout` |
| Strategy version | `1.0.0` |
| Governing config | `config/p3-governed-strategy.research.v1.json` |
| Governing work | `P3-LABEL-002`, `P3-EXP-002`, `P3-AGG-002`, `P3-POLICY-001`, `P3-POS-002/003`, `P3-ACCEPT-001` |
| Acceptance evidence | `docs/acceptance/p3-governed-strategy-development-20260729.md` |

## 1. Falsifiable Hypothesis

Moderate medium-term trend continuation with a bounded price-volume breakout adds selection value
when broad-market participation is supportive and extension risk is contained. The candidate
fails its initial development test if it cannot produce at least 100 closed trades, has negative
capital-matched excess, or becomes negative after statically removing its three largest closed-
trade PnL contributions. Maximum drawdown above 25% is a warning requiring rejection or a
predeclared policy revision; it is not repaired by opening later partitions.

This version does not claim that the optional twelve-state stock model, intraday timing, sector
expertise, news, fundamentals, or a model is required. It does not send notifications or place
real orders.

## 2. Scope And Information Boundary

- Research uses only the frozen 2012-2021 development partition. The 2022-2024 arbitration and
  2025 blind partitions remain inaccessible to tuning.
- Decisions use complete T-close PIT evidence and may first execute through the accepted P2
  T+1-open profile.
- Eligible identities come from the daily PIT universe. Economic `security_id` is never rebuilt
  from an exchange and stock code.
- Unresolved continuity evidence blocks entry for the complete requested development scope under
  the currently accepted conservative feature-panel policy.
- Static level-one industry membership is allowed only for concentration. Missing, ambiguous, or
  checksum-invalid membership blocks portfolio replay; it cannot enable an industry expert.

The external static artifact is resolved through the snapshot's existing `security_id` to
historical stock-code relationships. It never derives identity from a code and exchange. Its
source-declared industry hierarchy is used only to compare concentration membership, not as PIT
selection evidence.

## 3. Market And Entry Description

The decision chain evaluates five explicit descriptions:

1. market breadth is supportive when at least 45% of ready securities close above SMA60;
2. trend is moderate when total-return price is above SMA20, SMA20 is above SMA60, and 20-session
   momentum lies in `[3%, 15%]`;
3. breakout acceptance requires a prior-20-session breakout in `[0.25%, 2.5%]`, volume ratio in
   `[1.10, 2.50]`, and RSI14 in `[50, 72]`;
4. the extension label is contained only when realized volatility is at most 45% and RSI14 is at
   most 72;
5. entry readiness separately requires absolute entry-day total return at most 4%, at least 120
   listing sessions, CNY 100 million average 20-session amount, valid/executable evidence, and
   entry-policy permission.

The daily candidate scan first requires a ready feature row, the frozen listing-session and
average-amount minima, total-return price above SMA60, and positive momentum20. It then orders by
realized volatility ascending, momentum20 descending, and opaque `security_id` ascending before
retaining at most 20 securities for the governed chain. This is bounded preselection, not entry
approval: the complete labels and entry evidence remain visible downstream. System and strategy
vetoes run before scoring. Applicable trend, breakout, and risk-quality scoring experts contribute
equal-weight confidence-adjusted signals. An empty unit may request a trial entry only at aggregate
score `>= 0.35`; score cannot override a veto or conflicting constraint. This preselection contract
is part of candidate v1 and may change only with a strategy/evaluator version revision.

## 4. Capital And Position Policy

The research portfolio contains five fixed CNY 100,000 units. Each accepted entry targets 30% of
its unit and uses a 21% T+1 price/cost sizing buffer with whole 100-share lots. Adds and reductions
are unsupported in v1. At most five units may be active; the frozen P0 limits additionally allow at
most two active/reserved units in one static level-one industry and two new reservations per day.

An entry with no funded lot is rejected after allocation with an explicit funding reason. A buy
cancelled by P2 does not create a Trial lifecycle or consume a cooldown. Every filled entry creates
one opportunity-bound Trial lifecycle. Profit is not implicitly compounded into another unit.

## 5. Four-Source Exit And Cooldown

Every positioned review supplies all four frozen sources to exit arbitration:

- structural break: total-return price is below SMA60 while SMA20 is not above SMA60;
- constraint stop: marked return from the entry fill is at or below -8%;
- consecutive deterioration: after the three-session minimum hold, two consecutive reviews show
  price below SMA20 or non-positive momentum20;
- maximum hold: 40 trading sessions.

Unavailable feature evidence produces hold-with-alert, never a fabricated structural exit. A
triggered sell enters Exiting and P2 carries the same order through untradable sessions. The
maximum-hold trigger is evaluated at the 40th T close; a normal T+1-open fill therefore appears as
41 holding sessions in the closed-trade metric. A fully closed unit waits five authoritative
trading sessions before returning to Empty. No security may re-enter through that unit during
cooldown.

## 6. Evaluation Contract

Both full-capital CSI 300 excess and prior-close capital-matched excess are reported. Full-capital
excess measures opportunity cost and remains a warning rather than the initial strategy gate;
capital-matched excess diagnoses selection/timing under the same capital-use path. Required
breakdowns are calendar year, market-breadth regime, holding duration, and exit source.

For the five-unit portfolio, time in market is the mean occupied-unit share: active unit-days
divided by five times replay days. Average invested capital is different: on each replay close it
divides aggregate marked position value by aggregate system wealth, including each unit's cash and
external pool, then averages those daily ratios. A 30% trial target can therefore produce a lower
portfolio invested ratio when fewer than five units are occupied.

The first run is immutable evidence. At most two policy revisions may follow while the development
partition is open, and each revision changes the strategy version and records a pre-run rationale.
No threshold may be changed after viewing arbitration or blind results.

### Initial development result

The frozen first run produced 230 closed trades and passed every declared gate: 31.91% total
return, 22.49% capital-matched excess, 19.11% return after statically removing the three largest
closed-trade contributions, and 4.75% maximum drawdown. Its 37.39% win rate is paired with a 3.73
payoff ratio and 2.23 profit factor. Average invested capital was only 6.88% and time in market was
31.76%; full-capital CSI 300 returned 114.92%, leaving -83.01% full-capital excess. This candidate
therefore passes its predeclared selection/timing gates but does not solve portfolio capital
deployment or justify promotion beyond development research.

One fifth unit remained in Trial at the final 2021-12-31 mark, so 231 buys reconcile to 230 sells
and 230 closed trades. The five P2 ledgers still reconcile exactly to CNY 659,547.35. An
independent full invocation reproduced the feature panel, result JSON, and scoreboard
byte-identically and closed this candidate's development delivery check.

## 7. Traceability

| Concern | Owner |
| --- | --- |
| Frozen policy and thresholds | `config/p3-governed-strategy.research.v1.json`, `src/quanthus/strategy_policy.py` |
| PIT features and labels | LLD 03 and versioned feature/label registries |
| Experts, vetoes, score/action map | LLD 04 |
| Unit lifecycle, concentration, exits, cooldown | LLD 05 |
| Matching, fills, costs, ledger, benchmark metrics | LLD 02 |
| Decision log, funnel, and breakdowns | LLD 10 |
| First development result | `docs/acceptance/p3-governed-strategy-development-20260729.md` |

The reusable command is `quanthus run-governed-portfolio-replay`. It refuses an execution session
outside the development boundary and loads the benchmark and static industry map only from
checksum-verified local artifacts.

The accepted development evidence includes a deterministic rerun, complete candidate and
held-review decisions, exact unit-ledger reconciliation, a populated opportunity funnel, and the
named development metrics.
