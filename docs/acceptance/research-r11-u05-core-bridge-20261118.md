# R11 U05 funding/account core bridge

Status: PASS for repair R1 of the bounded first-entry and first-buy bridge.

Source starts at 1ad3fa5f5c9ee7238f168a3a29259ceba141e715, tree
3250685920640dd30615769c08fbe5e8229f1ad7, the ordinary merge of the independently accepted
first U05 slice. Shared funding.py, risk.py, candidate.py, contracts.py and the original 45
focused tests remain byte-identical.

The new application layer restores the complete versioned FundingState JSON shape with exact keys
and primitive types at every nested layer. It rejects bool-as-int, dropped nested fields, digest
mismatches and unsupported schemas. It calls the existing public decide_entry and asks a narrow
UnitOfWork port to commit the returned state.

Public F01-F07 results:

- The inherited FA01 serialization fixture round-trips every bucket, Decimal, enum, receipt,
  settlement, reservation and active-history identity; missing fields and unsupported versions fail.
- Synthetic 21000 cash, U1 20000, reserve 1000 and the explicit expert/fraction fixture produce a
  500-share intent while account cash remains 21000. Before fill the application obligation is
  500 shares at bound 10 with fee upper 5, and the existing pending_cash_commitment helper yields
  5005.
- The confirmed 500 x 10 buy and 5 confirmed fee produce account cash 15995, position 500,
  bucket cash 14995, reserve 1000, FundingState cash total 15995 and unchanged B 20000.
- The post-fact risk view has marked equity 20995 and canonical cash before internal holds 15995,
  with no pending buy because terminal remaining quantity is explicitly zero.
- Identical report replay adds no event, posting, outbox, account or FundingState delta. A new
  intent with a stale account revision is rejected without mutation.
- Baseline content and source/profile/report identities are versioned. A changed baseline,
  changed profile, same source sequence under a new report_id, or a second fact after the sole
  terminal fill cannot post. Raw conflicts remain separately auditable.
- A fault after FundingState storage but before account projection rolls back funding, account,
  reservation execution state, event, postings and outbox while retaining the raw inbox receipt.
  Retry posts once; a reopened SQLite file restores the same typed state, and account reconstruction
  from the baseline plus accepted entry/fact transitions matches the stored projection. Rejected
  commands remain separate outcomes and do not increase account revision during rebuild.

One public application transition applies the accepted account delta exactly once to account cash
and the matched funding bucket while preserving all other buckets and historical FundingState
reservations. Live posting and cold rebuild call that same transition. SQLite 2 rejects an older
candidate schema instead of interpreting it as the repaired identity model.

Evidence: 7 bridge tests and the original 31 R11 plus 14 U05 tests form 52 focused cases; adding the
required platform boundary module yields 71 passed on the available project Python 3.12.9
environment. Ruff, affected mypy and Python 3.10 compile pass. Maintenance selection against exact
base 462c668a is recorded separately.

Sell, settlement, second-cycle reuse and late-fee integration remain the next contiguous U05 slice.
Actual/daily profiles, UI/HTTP, market data, training, capital replay and production remain closed.

## CB02 concurrent snapshot repair

Independent008 closedCB01/CB03-CB07 and retained one real SQLite mixed-snapshot defect. The application now consumes one typed BridgeSnapshot of account/funding/obligations with a common revision/digest; the adapter materializes it in one read transaction. Risk, entry and buy use this common read. Shared funding-cycle reservation history stays unchanged.

A controlled two-thread public regression commits a500share fill between reading and returning the risk view. The original implementation returned prefill cash21000 with zero pending cash and failed; the revised view retains prefill pending5005, while a fresh view returns postfill cash15995/pending0.72related tests pass. Ruff/format and strict Python3.10-target mypy over the two changed source files plus neutral fact_contracts pass. A fresh non-incremental mypy cache was required after an internal incremental-cache error; no dependency changed. Exact final native acceptance remains pending until the limited009review passes.

## Final internal first-bridge acceptance

Exact87cdf3ae85536608c7c846b72aac2cf704e86c24/tree82b7185b80c12911523ed37fab9a3820607d33e1 passed CN02Python3.10.12 complete make check: 9458 passed/35 skipped. The008six closures plus009CB02closure and72related tests bind the same final source. Native logs/receipts were read back and SHA-verified; wall874.959s, scriptCPU857.867s, peakRSS561946624bytes, no guard fired. Existing optional dependency skips remain recorded; hosted CI was not used.

Ordinary705 mergee4d217913a7903f90295e5fa620ba1d17c6ffadd into the continuation branch has exactly the tested tree. Original rejected462c and5ee3mixed-snapshot failure remain preserved. This accepts the first synthetic entry/buy bridge for the next originalU05engineering step. Full two-cycle sell/settlement/late-fee, qualified historical comparison and daily-use acceptance remain open in700.

Evidence: `/Users/william/Documents/Codex/2026-11-17/quanthus-r11-continuation-relay/sol-corebridge-r1-005/engineering-acceptance.json`, `native-evidence/final/check-receipt.json`, `merge705-commit.json`; independent returns in the relay'sds-u05-corebridge-r1-review-008 andds-u05-corebridge-final-review-009.
