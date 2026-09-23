# 2026-09-22 1m recovery technical review (2026-09-23)

## Decision

**GO, narrowly, for historical 1m OHLCV plus explicit nullable amount; NO_GO for any claim that minute turnover is complete.** The ODS data dictionary allows nullable source-faithful minute fields. DWD turnover is nullable. A null is materially more accurate than estimated `price × volume`, TDX whole-lot reconstruction, or allocation of daily/5m turnover. The accepted source label is `tencent-mkline-raw-normalized`, not native TDX bars or a quotation series. This is a date-bounded recovery, not a general daily-source switch.

The minute issue #436 remains open for a durable historical source that includes exact minute amount. A 2026-09-22 ODS/DWD/OCI release may restore OHLCV continuity, but must report minute amount coverage as **0/1,195,920** and cannot be described as economically complete.

## Source and independent checks

- Same-date Tencent `mkline` raw JSON was captured for the sealed 4,992-stock universe on CN01/Hermes (2,496 each), with exact shard membership, per-response SHA-256 and immutable receipts. Both nodes hold both shards for independent normalization.
- 4,983 traded stocks yield 240 bars each after 09:30 opening-auction merge and 177 zero-volume absent-session slots. Nine suspended stocks yield no bars. Every traded symbol's normalized total volume and close agree with the sealed 2026-09-22 BaoStock daily observations. All 1,195,920 amounts remain null, never inferred.
- Source time labels are end-of-minute: TDX historical transactions at 09:31 align predominantly with Tencent 09:32 bars; 09:25 and 09:30 auction activity belongs to the first 09:31 bar. TDX transaction volume is whole-lot rounded and differs from daily by odd shares, so transaction `price × lots × 100` is not an exact replacement for minute amount.
- CN01 candidate independent scan found 4,983 distinct securities, exactly 240 unique session timestamps per traded security, 0 duplicate keys, 0 OHLC-envelope violations, 0 daily volume/close mismatches and 0 non-null amounts. Versioned seed builder rechecks raw/receipt hashes, full grid, OHLCV and sealed daily comparison on each node before generating a terminal `status.json`/`checkpoint.json` seed.
- CN01 historical TDX after-hours collector independently returned 39,957 fixed-price trades and 6,672 complete equity/ETF receipts, with 0 failed, 0 wrong-date, 0 out-of-window, 0 duplicate-event and 0 amount-arithmetic violations. `trades.parquet` SHA-256 `a8c52b998e81f7081d59df5a6b014c4361daf60efd03289230b3712c40bd30c5`; `receipts.parquet` SHA-256 `48f130cc612f9c453e78c51d47dbed33eecf4cb7db215c2e37c5ecdb227f6e58`. Hermes independent acquisition and formal lane results must be appended before declaring both-node acceptance.
- Hermes' independent TDX acquisition also returned 39,957 rows and 6,672 receipts with no failures. A content-only per-security comparison agrees for 4,144/4,145 nonempty securities. For `688525`, one otherwise identical 2-lot trade is timestamped 15:10 by the CN01 route and 15:11 by the Hermes route; each route returns its own value consistently on three repeated queries. Preserve both immutable source receipts and flag this provider-route minute-label discrepancy for follow-up; it does not affect the 1m continuous bars, date, price, quantity, or DWD warehouse (the sparse trade dataset is ODS-only/deferred).
- OCI is reachable by verified-key passwordless SSH through CN01. Its 2026-09-22 sync failure receipt states `source_market_date_stale`: CN01 DWD CURRENT is 2026-09-21. This is downstream of missing CN01 ODS/DWD, not an OCI target-write failure.

## Release gates and limitations

1. Only use a verified, exact-universe, date-bound seed in a new 1m lane attempt; do not edit an older release, weaken the 240-grid or 4,992-universe gate, or overwrite the live checkout.
2. The separate 15:05–15:30 after-hours dataset must have complete per-security receipts on **each** formal node attempt. No-data receipts are valid only on successful historical transaction queries.
3. Accept two nodes only after four 2026-09-22 lanes and the catalog are READY, DWD build id/date/hash is verified, and OCI mirrors the same verified CN01 package. Do not infer this from email alone.
4. Record the null amount limitation in issue #436 and the incident ledger. Continue exact-minute-amount sourcing as a distinct unresolved quality work item; consumers requiring turnover must remain fail-closed.

## Test scope

New seed validation tests reject missing/wrong-date bars, estimated amount, daily-volume mismatch and invalid OHLC. Eleven targeted Tencent normalization/seed unit tests passed on 2026-09-23. `make check` could not start on the local Mac because the installed Xcode license is unaccepted; the previous full repository run for this PR was 2,844 passed, 20 failed, 7 skipped and is not a green baseline. This is a scoped technical review, not a claim of full-suite or human approval.

## Same-day recurrence guard (PR #438 commit `54794cd`)

The old `1m` path still reaches an Eastmoney EOF on a 2026-09-23 CN01 sample; leaving it
unchanged would predict another terminal circuit tonight. A separate opt-in repository adapter
therefore uses the same native Tencent `mkline` OHLCV after the sequential market lane has
published a same-date READY daily/status release. It checks every requested code/date, opening
auction, full 240-session grid, per-security daily volume and close, and preserves compressed
raw response bytes plus SHA-256 in the attempt-local collector home. Suspended securities must
have zero same-date provider rows. Exact minute amount remains NULL. The opt-in cannot combine
with an external collector or recovery policy; missing market evidence blocks collection.

The three-symbol 2026-09-22 live source smoke (`000001`, `600519`, `688001`) completed with
3/3 per-security receipts, 240 rows each, matching daily volume and 0 non-null amount; the
versioned SHA-bearing raw responses were preserved. The full-universe route is sequential, so
the opt-in rollout uses a 3,600-second collector timeout (below the 4-hour service ceiling),
with the existing orchestrator deadline still authoritative. This does not prove 2026-09-23
full-universe success; tonight's actual four-lane, DWD and OCI receipts remain a separate gate.
