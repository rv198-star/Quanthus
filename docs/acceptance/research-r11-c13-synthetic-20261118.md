# R11 C13 synthetic later-signal gate 025

This package implements a pure, deterministic pre-reservation gate for the proposed later-signal
contract v0.1. It does not activate a producer, alter the original T+1 path, create a reservation,
persist an attempt, or authorize market, training, capital, P6, daily or production use.

The gate receives one immutable parent opportunity, two explicitly attributed observations, an
optional versioned synthetic policy, and an authoritative synthetic calendar. It returns one of
five explicit states: `ORIGINAL_T1_UNCHANGED`, `OBSERVATION_ONLY`, `BLOCK_LATER_ATTEMPT`,
`ELIGIBLE_FOR_ONE_ATTEMPT`, or `TERMINAL_NO_REARM`.

The synthetic policy binds producer, predicate, calendar, observation lifetime, execution window,
zero-fill policy and expiry. Evidence must describe the same subject and rule on adjacent calendar
sessions, with feature session, evidence time, availability time and cutoff checked explicitly.
Unknown evidence is never treated as false. Funding or price refresh, same-session corrections,
duplicate evidence and true-to-true continuation remain observational. The planning attempt has an
attempt slot keyed only by account and parent opportunity, so its identity is stable across policy
versions; the returned object is not a reservation or execution instruction.

The A01-A09 witnesses are in `tests/unit/test_product_daily_c13_later_signal.py`. The original
sticky-expiry test and the existing pure Trend classifier tests are retained unchanged. A10-A13
transaction, receipt, slot-consumption, reconciliation and concurrency behavior remains outside
this package.
