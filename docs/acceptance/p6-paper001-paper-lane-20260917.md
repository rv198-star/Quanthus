# P6-PAPER-001 Isolated Paper Lane Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted for engineering |
| Work item | `P6-PAPER-001` |
| Policy | `config/forward-validation/paper/p6-paper-lane.v1.json` |
| Execution profile | `close-to-next-open-v1` |
| Units | 5 x CNY 100,000 fixed target notional |
| Actual execution authority | false |
| Next gate | `P6-WEB-001` |

## Accepted outcome

P6 now has one isolated fixed-unit paper state/intent/settlement lane. It does not duplicate P2.
Every paper buy/sell delegates execution to the accepted P2 engine and policy, including costs,
tradability, missing evidence, buy expiry, carried sell behavior and balanced accounting events.

A sealed aggregate decision is projected only after unit allocation is already fixed. Trial entry,
reduction and full exit quantities use the frozen strategy unit notional/fractions and 100-share lot
rule; `no_action` creates no intent. Missing decision close blocks a new entry or reduction rather
than inventing a quantity.

Paper artifacts carry `authority=paper` and `actual_execution_authorized=false`. No notification,
broker or live execution record is created.

## Fixed-capital accounting

Five units start at CNY 100,000 each. `target_notional` is immutable. Full-exit P2 settlement restores
available unit cash toward the fixed target and transfers positive excess to `external_pool`; that
pool is not used by the paper projection for new entries. Losses do not enlarge the target and the
paper lane does not compound profits.

All emitted P2 ledger-entry postings must balance to zero before paper state advances. The final P2
`DAILY_SNAPSHOT` is the only accepted next paper-unit state.

## Verification

Focused paper verification passed `10/10`:

- exact strategy/P2/profile/funding policy binding;
- five-unit initial ledger and CNY 500,000 total fixed target;
- no-action creates no paper intent;
- trial entry projects frozen whole-lot quantity and fills only through P2;
- limit-up and missing execution evidence produce no buy fill;
- full exit carries through limit-down and later fills through P2;
- missing held execution evidence does not force a sale;
- target-notional mutation fails closed;
- missing decision close blocks entry;
- deterministic entry/exit round trip preserves paper-only authority and transfers profit to the
  non-redeployable external pool.

Full repository verification is rerun after current-state status is reconciled.

## Authority ceiling

This is an engineering paper lane only. No official prospective cycle has run and no 2026 historical
row is prospective evidence. Web mutation, notification, broker, live capital, Stage action mapping,
new experts, aggregator change, 2022-2024 arbitration and permanent-blind 2025 remain unauthorized.
`P6-WEB-001` is the next dependency.
