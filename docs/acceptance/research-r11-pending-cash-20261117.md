# R11 pending-cash repair and U05-U07 seam quote

Date: 2026-11-17  
Implementation: `R11 1.2.1`  
Risk policy: `1.0.2`  
Scope: synthetic engineering only; `daily_adopted=false`, `production=false`

## Result

A public `evaluate_quantity` call now treats `RiskSnapshot.cash_available` as the canonical cash
basis before internal pending-buy commitments, matching
`pending-cash-contract.v1.json`. For each unique pending reservation it deducts
`remaining_quantity * price_bound` and, when the fee is not already recognized, the declared
unrecognized fee upper bound once. Exposure still uses `max(mark_price, price_bound)`.

The reproduced failure returned `q=500`, `total_cost=5005`, and residual capacity `5010` while a
`9000 + 5` pending commitment existed. The repaired public result is `q=0`, residual cash `995`,
and `CASH_CAPACITY`. The new decision output exposes the input cash basis, pending principal,
pending unrecognized fee, total pending commitment and residual cash. Missing price, fee or cash
basis returns `INPUT_UNAVAILABLE`; a negative residual is clamped to zero.

The frozen research contract, `CURRENT.json`, original 21 goldens and their hashes are unchanged.
The old `ds-adoption-usage.v1.json` remains historical `1.1.0` evidence. The successor versions are
bound in `config/research/r11/implementation-policy.v1.json`.

## Public cash behavior

- All 11 independent cash oracles execute through `evaluate_quantity`; every expected quantity,
  residual-cash value and required reason code is read from the frozen case JSON.
- PC12 verifies that a cash-blocked `decide_entry` preserves the exact input `FundingState`, emits
  no receipt, transfer, hold or reservation, and still intersects the configured fraction with the
  expert maximum.
- PC13 verifies immutable accepted-command replay and confirms that a new command can use restored
  cash only after the prior active cycle is settled. A new pending hold still blocks otherwise
  available cash.
- PC14 verifies that duplicate pending reservation identities fail at `RiskSnapshot` construction;
  duplicate holds are not silently summed.
- The original two-cycle FA-01 test continues to cover old-cycle history, late fee/new B isolation,
  refused-entry rollback and command replay without new postings.

The focused regression is in `tests/unit/test_product_daily_r11_pending_cash.py`. Existing accepted
coverage remains in `tests/unit/test_product_daily_r11.py` and
`tests/unit/test_product_daily_r11_review.py`.

## Account configuration disposition

`config/research/r11/continuation-001/account-profile-disposition.v1.json` records four modes:

| Mode | Disposition |
| --- | --- |
| `synthetic` | Executable only from the exact `SYN-R11-U03` fixture. |
| `historical_proposal` | Seed only; execution requires exact purpose, source/as-of, limits, expert admission and finite budget. |
| `daily_simulated_template` | Blocked; all unknown facts remain null. |
| `actual_account_template` | Blocked on P12/Owner account facts; an empty account is not inferred. |

The same-as-of adapter contract needs canonical cash before internal holds, equity, all positions,
pending buys/sells, recognized and unrecognized fees, N/B/reserve, limits, one fee profile,
execution windows, and terminal/fill/correction semantics. A broker display value already net of
internal holds is a different producer basis and cannot be passed through this input and deducted
again.

## U05-U07 concrete seam quote

This quote starts from current code and tests. It does not apply a proportional discount to the
historical `46-92h` quote.

| Item | Reusable seam | Remaining adapter or independent work | Dependencies | Low | High |
| --- | --- | --- | --- | ---: | ---: |
| U05 | `RiskSnapshot`, `evaluate_quantity`, `decide_entry`, `FundingState`, `EntryReservation`, `settle_batch`, `apply_late_cash_delta`; FA-01 and PC12-PC14 tests | Map same-as-of account/fill facts into typed snapshots, bind ratios/fees/windows/admission, reconcile external fills and corrections across two cycles | complete producer adapter; P12 fee/window semantics; approved expert use | 14h | 28h |
| U06 | Original candidate/expert cards, funding and settlement core, comparison proposal and all current synthetic invariants | Freeze exact old/new input closures, compose one control and one R11 path, materialize event-complete differences and resource evidence | U05; qualified U02 manifest/source; adopted ratios, purpose, limits and finite run budget | 20h | 40h |
| U07 | Existing deterministic receipts, manifests, golden/story rebuilds and local acceptance pattern | Independently rebuild prerequisites and both paths, reconcile every identity/result, execute whole-use audit and cold handoff | U06; independent implementer; fresh compute/access; no unresolved hard gate | 16h | 32h |

The bottom-up sequential quote is `50-100 active engineering hours`. It is a planning range for
these three adapters and evidence handoffs, not a promise or a result-bearing run estimate. It
excludes P10 source recovery and data qualification, P12 product decisions or actual account fact
collection, market-row access, training, capital replay, daily serving, production deployment,
compute purchases and final scientific/consumer adoption. A source or account fact gap is returned
to P10/P12; it does not become a synthetic default or an R11 result.

## Verification

Local focused regression: `30 passed` on macOS Python 3.14. The accepted runtime closure is also
compile-checked with Python 3.10; Ruff check/format and affected-module mypy pass. A fresh Python
3.10 Linux `make check` on the exact final GitHub commit is recorded in the delivery package rather
than inferred from the prior accepted `1.2.0` suite.
