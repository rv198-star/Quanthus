# R11 U05 synthetic durable facts

Status: PASS for the bounded synthetic slice.

Source starts at 9010d35d07ea506c82cd4afd299178f3ea22c216, tree
a3a2bf917d8de95c2aca21b5d46ec09b882ae1e7. The new use cases separate pure accounting,
execution facts, application ports and a SQLite adapter.

EX01 posts a 400 x 12.50 buy and confirmed 5.00 fee on a 10000.00 baseline, producing cash
4995.00, quantity 400 and PRICE_DEVIATION. EX03 returns the original result for an identical
report and IDEMPOTENCY_CONFLICT for changed content. EX04 appends a 400-to-300 correction,
producing cash 6245.00, cash delta 1250.00 and quantity delta -100 while retaining the original
report.

Both report reception and business posting have explicit transaction boundaries. A fault after
event insertion rolls back events, postings, projection, revision and outbox while retaining the
inbox report. Retry posts once. SQLite reopen and baseline-plus-event rebuild reproduce the stored
projection and digest. Trusted synthetic context, aware timestamps, finite Decimal inputs, positive
non-bool quantities and fact-revision CAS fail closed.

Evidence: 3 new tests pass; original 31 R11 tests pass; Ruff, affected mypy and Python 3.10 compile
pass; maintenance selection completed. Full cross-cycle funding integration, actual/daily account
profiles, HTTP/UI and production composition remain outside this slice.

## Sol internal review repair

The original e41e2bde candidate was rejected by independent internal review (R01-R04 high, R05-R07 medium); its immutable return and witnesses are retained. Revised application services require an explicit immutable runtime account allowlist supplied by trusted composition, separate from every business context. A business context has no trust boolean. The SQLite composition factory is trusted bootstrap, not a report endpoint.

Account/mode-scoped canonical receipt/event/source identities, original baseline payload/result replay, raw report/correction receive times, neutral capability primitives, named dependent-plan reassessment in the same transaction, pending reconciliation on insufficient cash, correction rollback and old-schema rejection are verified by boundary regressions. Current code has 12 U05 tests plus the original31 R11 tests; exact final native acceptance and revised independent review are recorded in the delivery receipts.

The first slice uses explicitly confirmed per-report fees in a single-fill fixture. Complete order/day fee coverage, full account producer mapping, same-core two-cycle funding, actual/daily modes and U06/U07/U08 remain open.

## Platform static boundary integration

The existing platform boundary check matched the substring `execution` and therefore rejected the new public fact types. The reviewed P12 v1.1 infrastructure direction is implemented by a versioned exact-file/module/name declaration: only `adapters/product_daily_facts_sqlite.py` may import `Correction`, `ExecutionReport`, `SyntheticContext` and `validate_context` from the public facts capability. Other platform files, broker/strategy engines, star imports and new action functions retain the original prohibition. This declaration grants no runtime capability or mode admission. Five negative/positive boundary cases guard the scope.
