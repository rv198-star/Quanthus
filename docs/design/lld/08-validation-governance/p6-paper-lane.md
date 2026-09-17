# P6 Isolated Paper Lane

| Field | Value |
| --- | --- |
| Parent | `../08-validation-governance.md` |
| Work item | `P6-PAPER-001` |
| Design status | approved |
| Delivery gate | accepted for engineering; official prospective cycle still disabled |
| Policy | `config/forward-validation/paper/p6-paper-lane.v1.json` |
| Execution referee | accepted P2 `close-to-next-open-v1` |

## Boundary

The P6 paper lane is an authority wrapper and fixed-unit state surface around the already accepted P2
engine. It is not a second matcher. Fill price, costs, limit/suspension/missing behavior, buy expiry,
sell carry, daily snapshots and balanced ledger postings remain owned by P2.

The paper account has five isolated CNY 100,000 units. Target notional never changes after gains or
losses. Adds remain disabled, reductions remain allowed, profits transfer to each unit's
non-redeployable external pool, and no system-level compounding is introduced.

## Decision projection

The input is an exact sealed aggregate decision whose unit allocation is already fixed. Paper does
not change candidate ranking or concentration policy. It maps only the accepted final action:

- `trial_entry`: empty unit, exact decision close required, target fraction from the sealed decision
  or frozen 30% base fraction, whole 100-share lots;
- `no_action`: no intent;
- `reduce`: sell only the whole-lot quantity above the sealed target fraction;
- `full_exit`: sell the full settled position.

Missing decision close blocks a new entry/reduction. Missing or invalid execution evidence never
forces a held position sale.

## Execution and ledger

A pending paper intent carries a `paper` authority marker and `actual_execution_authorized=false`.
Settlement calls `BacktestEngine.run_buy_session` or `run_sell_sequence` directly. Buy gets one
next-open attempt and then cancels; sell carries until tradable. All P2 ledger entries must remain
balanced. The final P2 daily snapshot becomes the next immutable paper unit state.

Paper identifiers are distinct from actual execution identifiers even though the underlying P2
contracts are shared. No notification or broker edge exists.

## Verification

Engineering acceptance covers normal buy, limit-up rejection, missing-execution rejection, carried
limit-down sell, missing sell evidence, full exit, balanced P2 postings, fixed target-notional,
non-redeployable profit pool, authority separation and deterministic round trip.

## Authority ceiling

P6-PAPER-001 grants paper-lane implementation only. It does not authorize an official prospective
cycle, Web mutation, notification, broker, live capital, Stage action mapping, new experts,
aggregator changes, arbitration or permanent-blind access. `P6-WEB-001` is the next child.
