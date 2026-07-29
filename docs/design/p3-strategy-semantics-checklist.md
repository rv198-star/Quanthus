# P3 Terminology and Strategy Design Checklist

| Field | Value |
| --- | --- |
| Phase | Post-P3 formal-strategy discussion input |
| Design status | draft |
| Delivery gate | blocked |
| Authority | Non-governing checklist; no item authorizes implementation until its governing LLD/config is versioned and approved |
| Parent route | `lld/README.md` |

## 1. Use

This is the progressive discussion surface after the strategy-neutral infrastructure is accepted.
It separates already-frozen platform language from semantic choices that require product judgment.
Checking an item here records discussion progress only. Approved decisions must move to the owning
LLD, policy/config, contract tests, and acceptance evidence in the same change.

The candidate twelve-state stock-stage model remains optional. It is not the default P3 direction,
not a prerequisite for the first baseline strategies, and gains no authority from appearing here.

## 2. Terminology to confirm

### Data and time

- [ ] `security_id`: opaque economic identity from the snapshot; never reconstructed from a code.
- [ ] `stock_code`: exchange-visible code valid only in a declared date interval.
- [ ] `market_date` and `session`: exchange trading date and its authoritative calendar session.
- [ ] `knowledge_cutoff`: latest information time allowed into one snapshot or decision.
- [ ] `PIT`: point-in-time evidence whose knowledge cutoff is no later than the decision.
- [ ] `T-close view`: strategy-visible daily universe and history ending at T close.
- [ ] `execution date`: later session on which a T decision first becomes eligible.
- [ ] `replay frame`: one T snapshot plus hidden runner-owned execution source for its later session.

### Availability and execution

- [ ] `coverage status`: trading, suspended, missing, not listed, or delisted observation state.
- [ ] `data valid`: bar fields exist and satisfy type, positivity, and OHLC consistency rules.
- [ ] `is executable`: accepted source evidence can support matching for that session.
- [ ] `entry policy allows`: policy permits a new position; it is not the same as sellability.
- [ ] `missing evidence`: fail-closed uncertainty, not a zero price, suspension, or negative signal.
- [ ] `limit block`: directional inability to buy at limit-up or sell at limit-down.
- [ ] `corporate action`: PIT-visible cash/quantity change applied before matching.
- [ ] `exception interval`: versioned security/date range where unresolved action evidence blocks
  entry and aborts an affected held-position replay.

### Research and decision artifacts

- [ ] `feature`: versioned deterministic numeric/categorical input with exact source lineage.
- [ ] `label`: versioned deterministic description of market state; not an action.
- [ ] `stage state`: optional security market-state label, distinct from a position state.
- [ ] `expert opinion`: typed score, constraint, veto, or abstention with evidence.
- [ ] `veto`: governance result that can force liquidate, reduce only, or block new entry.
- [ ] `constraint`: allowed action/position bound; intersected before score mapping.
- [ ] `aggregate score`: ranked strategy evidence after governance, not expected return.
- [ ] `candidate`: security that survived declared upstream filters at one snapshot.
- [ ] `decision`: persisted proposed/final action and reasons at one snapshot.

### Position and execution artifacts

- [ ] `capital unit`: fixed accounting budget and lifecycle carrier; not a security or order.
- [ ] `position state`: empty/trial/dynamic/exiting/closed lifecycle owned by P3.
- [ ] `strategy action`: current replay vocabulary of no-action, empty-unit entry, or full exit.
- [ ] `signal`: quantity-resolved actionable P2 input created from an accepted decision.
- [ ] `order intent`, `order`, and `fill`: eligibility, matcher lifecycle, and executed accounting
  fact; these are three different records.
- [ ] `notification`: delivery of persisted advice; never an order or fill.
- [ ] `actual-operation report`: operator-entered fill/change/no-action fact for later P7
  reconciliation.

### Evaluation modes

- [ ] `historical replay/backtest`: strategy decisions over immutable historical PIT evidence using
  simulated execution.
- [ ] `shadow`: recorded alternative decision with no execution authority.
- [ ] `paper operation`: forward decisions and simulated outcomes with no real fill claim.
- [ ] `operator-assisted operation`: email advice followed by manual action and explicit reporting.
- [ ] `official result`: versioned run whose data, code, policy, audit, and acceptance gates all
  permit the claim; `execution_authorized=false` inputs cannot authorize live execution.

## 3. Strategy decisions to freeze

### Baseline scope

- [ ] State the first strategy's falsifiable hypothesis and non-goals in one paragraph.
- [ ] Choose benchmark(s), evaluation horizon, and whether returns are absolute or benchmark-relative.
- [ ] Freeze eligible exchanges, boards, listing age, ST/unknown-status handling, and exclusions.
- [ ] Decide whether version one remains one fixed capital unit with enter/full-exit only.
- [ ] Keep the active daily T-close to T+1-open profile; treat two-window rules as a separate future
  version rather than mixing cadences.

### Inputs and market description

- [ ] Select the smallest daily features needed by the hypothesis, with lookback and null policy.
- [ ] Define required warm-up history and behavior on insufficient history.
- [ ] Decide whether the first baseline uses no stage label, a small direct rule label, or a shadow
  stage label.
- [ ] For the twelve-state candidate, choose explicitly among excluded, shadow-only, or proposed for
  later activation; active use requires its separate promotion gates.
- [ ] Freeze cross-sectional completeness rules for ranks and percentiles.

### Entry and ranking

- [ ] Define entry eligibility separately from ranking score.
- [ ] Define each hard veto and its evidence/reason code.
- [ ] Define candidate score formula, scale, tie-break, threshold, and abstention behavior.
- [ ] Define how many candidates may compete each day and how empty units are reserved.
- [ ] Freeze quantity/sizing, lot rounding, cash insufficiency, industry cap, and daily-open cap.

### Hold, add, and exit

- [ ] Define the minimum hold, review cadence, and maximum hold in trading sessions.
- [ ] Choose whether add/reduce remain unsupported in version one; if enabled later, freeze their
  triggers, sizes, and precedence before engine expansion.
- [ ] Define structural, risk, maximum-hold, and governance exits as separate sources.
- [ ] Freeze exit precedence, conflict behavior, unavailable-evidence behavior, and reason codes.
- [ ] Define cooldown and re-entry rules by economic identity, not current stock code.
- [ ] Confirm that execution failures carry/cancel under P2 policy and do not rewrite the strategy
  decision after the fact.

### Costs, metrics, and validation

- [ ] Bind the exact research execution policy, fees, slippage, price limits, and corporate actions.
- [ ] Select primary metrics before testing: total return, drawdown, capital-day return, exposure,
  turnover, fees, win rate, payoff ratio, and profit factor.
- [ ] Define benchmark, market-era, board, liquidity, and holding-duration breakdowns.
- [ ] Freeze train/design, validation, and untouched test periods; avoid tuning on the final period.
- [ ] Define parameter-change budget, walk-forward or rolling validation, and minimum trade count.
- [ ] Set acceptance, warning, and stop thresholds before viewing final results.
- [ ] Require deterministic rerun, complete decision trace, opportunity funnel, and failure-case
  review, not only aggregate return.

### Promotion and forward use

- [ ] Version strategy, features, labels, experts, parameters, and policy as one run manifest.
- [ ] Define baseline-to-shadow-to-active promotion and rollback criteria.
- [ ] Define what an email advice record contains and explicitly excludes.
- [ ] Defer actual-operation reporting and advice-to-fill reconciliation to the approved P7 boundary.

## 4. Suggested first discussion order

1. Freeze the vocabulary differences that affect implementation: identity, PIT, label versus
   action, capital unit versus position, and advice versus fill.
2. Define a formal strategy hypothesis independently of the frozen P3 capability fixture, with no
   automatic dependency on the twelve-state model.
3. Freeze its universe, minimal features, entry, exit, hold, and sizing rules only after that
   hypothesis is agreed.
4. Freeze evaluation periods, metrics, thresholds, and stop rules before running the baseline.
5. Compare later label/expert/state-machine additions against that baseline in shadow form.
