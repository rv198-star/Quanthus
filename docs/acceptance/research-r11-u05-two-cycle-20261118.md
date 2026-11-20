# R11 U05 complete synthetic two-cycle story

Status: PASS for the bounded synthetic S01-S09 story.

Source base: ffe9dccc27dd56b931ef0246ae4f1d07246f4738 / tree
69c7f0c049067bd34e9744ae220ee58bbac98183. All shared policy files and the original 13-file
readonly closure remain unchanged.

The public story runs through typed application commands:

- first public entry yields 500 shares and pending commitment 5005;
- buy 500 x 10 plus fee 5 gives account 15995 and bucket 14995;
- full sell 500 x 12 minus fee 5 gives account and bucket 21990/20990;
- original public settle_batch gives bucket 20000 and reserve 1990;
- second public entry SYN002 yields q500 and B20000;
- a separate first-cycle late fee of 100 reduces account to 21890 and reserve to 1890 without
  changing the active second-cycle B or pending 5005;
- second buy gives 16885/14995; second sell at 9 gives 21380/19490; second settlement gives
  bucket 20000 and reserve 1380.

Final state has cash 21380, two immutable entry histories, two completed settlements and no active
cycle reservation. Gross price PnL 500 minus confirmed fees 120 equals net cash change +380.

The buy-only ExecutionReport remains buy-only. Sells use a separate typed report/profile path. The
one public sell calculation derives cost basis from the matched immutable buy lot and emits balanced
cash, position, realized PnL and fee postings. The same function is used by cold rebuild.

Settlement readiness is derived from matched terminal buy/sell profiles, actual flat quantity,
resolved application obligations, known fees and unresolved conflicts. The adapter never injects a
ready booleans fixture. It constructs CLOSED only after those facts pass and delegates allocation to
the original settle_batch.

The SQLite schema now holds a chronological typed journal for entry, buy, sell, settlement and
late-fee transitions. Cold rebuild starts from the original baseline and dispatches each journal
row through the same public transition calculations. Settlement and late-fee fault injection retain
the raw operation inbox while rolling back funding, account, journal, postings, source identity and
outbox; retry applies once.

Verification: 76 combined focused tests pass, including the original 72 closure and four new
two-cycle tests. Ruff/format, affected mypy, Python 3.10 compile and maintenance selection are
recorded in the return package. Full native make check remains for Sol after independent review.
