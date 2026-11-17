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
