# Golden Acceptance Fixtures

P1 will add dated data reconciliation fixtures for D1, D2, and D3. P2 will add hand-calculated
trades covering normal fills, limit-up buy rejection, carried limit-down sale, suspension,
cash shortage, candidate ties, corporate actions, and maximum-hold exit.

Fixtures must contain source references and expected outputs, never private credentials or bulk
licensed market data.

`p2_normal_buy.v1.json` is the first P2 hand-calculated vertical slice. It uses a clearly labeled
synthetic policy to prove event order, next-session timing, costs, balanced postings, marking, and
deterministic replay without claiming that production fees or action windows have been frozen.

`p2_buy_no_fill.v1.json` covers the second P2 slice: suspended, limit-up buy-blocked, missing
execution evidence, and insufficient-cash cases. It distinguishes market cancellation from order
rejection and requires exact cash preservation with no fill or ledger entry.

`p2_carried_sell.v1.json` covers one full-position sell carried through limit-down and suspension
before execution. It hand-reconciles sell fees, daily mark provenance, realized PnL, and fixed-unit
profit transfer to the finite external pool without treating synthetic costs as production policy.

`p2_candidate_tie.v1.json` freezes same-snapshot candidate ordering as aggregate score descending,
then security code ascending, independent of input order. Allocation and concentration remain
outside this P2 fixture.

`p2_corporate_action.v1.json` applies a known cash dividend on its effective date and reconciles the
cash/income ledger to an explicit ex-right mark. `p2_fixed_unit_report.v1.json` reconciles one closed
unit's cash flows, wealth, capital days, and drawdown without a compound-NAV claim.

`p2_audit_notification.v1.json` binds one persisted synthetic window signal to an append-only
failed-then-delivered notification retry chain and requires the run stream to retain zero fills.
`p2_window_cutoff_expiry.v1.json` freezes synthetic-only cutoff, eligibility, and half-open expiry
expectations for both approved clock windows without activating target matching.
`p2_maximum_hold_sell.v1.json` proves that an upstream maximum-hold exit reason uses the generic P2
sell path; P2 does not calculate the strategy holding period.

`p2_round_trip_replay.v1.json` composes the daily entry, cash-dividend, exit, settlement, fixed-unit
metric, diagnostic, and immutable-publication contracts into one ordered replay.
`p2_p1_execution_adapter.v1.json` freezes fail-closed mapping from approved P1 daily/tradability/
limit semantics to P2 execution states. `p2_decision_log.v1.json` covers complete opaque upstream
evidence for accepted, rejected, and shadow decisions without granting non-accepted records order
authority.

`p3_strategy_replay.v1.json` composes two sequential complete trades over one fixed-capital unit,
including a cash dividend, one winning and one losing trade, external-pool settlement, daily
equity, fees, drawdown, volatility, Sharpe, win/loss quality, turnover, and exposure. It is a
scripted strategy oracle, not a production rule or return claim.
