# R11 U05 funding/account core bridge

Status: PASS for the bounded first-entry and first-buy bridge.

Source starts at 1ad3fa5f5c9ee7238f168a3a29259ceba141e715, tree
3250685920640dd30615769c08fbe5e8229f1ad7, the ordinary merge of the independently accepted
first U05 slice. Shared funding.py, risk.py, candidate.py, contracts.py and the original 45
focused tests remain byte-identical.

The new application layer restores the complete versioned FundingState JSON shape, calls the
existing public decide_entry, and asks a narrow UnitOfWork port to commit the returned state.
The SQLite adapter persists account and funding state, the raw report inbox, command receipt,
event, balanced postings and reassessment outbox in one business transaction.

Public F01-F07 results:

- The inherited FA01 serialization fixture round-trips every bucket, Decimal, enum, receipt,
  settlement, reservation and active-history identity; missing fields and unsupported versions fail.
- Synthetic 21000 cash, U1 20000, reserve 1000 and the explicit expert/fraction fixture produce a
  500-share intent while account cash remains 21000.
- The confirmed 500 x 10 buy and 5 confirmed fee produce account cash 15995, position 500,
  bucket cash 14995, reserve 1000, FundingState cash total 15995 and unchanged B 20000.
- The post-fact risk view has marked equity 20995 and canonical cash before internal holds 15995,
  with no pending buy because terminal remaining quantity is explicitly zero.
- Identical report replay adds no event, posting, outbox, account or FundingState delta. A new
  intent with a stale account revision is rejected without mutation.
- A fault after FundingState storage but before account projection rolls back funding, account,
  reservation execution state, event, postings and outbox while retaining the raw inbox receipt.
  Retry posts once; a reopened SQLite file restores the same typed state, and account reconstruction
  from the baseline plus facts matches the stored projection.

Evidence: 4 new tests and the original 31 R11 plus 14 U05 tests form 49 focused cases; adding the
required platform boundary module yields 68 passed on both the available project Python 3.12.9
environment and Python 3.14. Ruff, affected mypy and Python 3.10 compile pass. Maintenance selection
against exact base 1ad3fa5f is recorded separately.

Sell, settlement, second-cycle reuse and late-fee integration remain the next contiguous U05 slice.
Actual/daily profiles, UI/HTTP, market data, training, capital replay and production remain closed.
