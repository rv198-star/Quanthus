# P10-DAILY-CORE-R1 — 2026-09-24 recovery and business-value warehouse quality rollout

| Field | Versioned contract |
| --- | --- |
| Authority | Owner October 8, 2026, ADR 0016; P10 #495/#496 |
| Product | `daily-core-v1`; distinct from legacy four-lane and strict intraday |
| Baseline | September 24 2026; prior DWD 2026-09-23 exactly bound |
| Accepted design | Two physically READY, immutable, same-schedule market-daily-core and calendar-daily-events lanes |
| Non-dependencies | `intraday_1m`, `intraday_5m` excluded as `not_in_product/formal_ready:false` |
| Output scope | Seven DWD tables including interval_size=1d only for `dwd_security_bar`; unavailable minute product explicitly recorded |
| Quality policy | `config/pipelines/warehouse-quality-policy.v1.json`, SHA bound to catalog and DWD manifest |
| Publish/status | New catalog kind schema2, DWD manifest kind schema2, `CURRENT_DAILY_CORE.json` **only** |
| Existing system | Legacy four-lane READY, DWD `CURRENT.json`, strict 240 actual 1m and all frozen 2026-09-24 assets remain untouched |
| Target rollout | New CN01/Hermes daily-core service and source-qualified frozen/date-correct OCI mirror to separate root, no legacy timers revived |

## 1. Real preflight (2026-10-08 CN01)

A revalidated immutable 2026-09-24 daily market release from CN01's September 28 fixed cold-test source has 4,992 real daily OHLCV, 4,992 daily tradability, 4,992 valuations, 3 daily indices, and calendar. Its READY/release SHA and physical source SHA were checked from unmodified quarantine. A production 2026-09-24 `calendar-daily-events` release has 7,612 announcement-archive observations, with deferred unsupported event artifact types empty. Their schedule contract ID/revision/config SHA match; invalid mixed contracts must fail before work.

The same-date native 5m frozen release is independently READY with 239,280 rows, but is **not** an input to the declared `daily-core-v1` product. 1m remains strictly unqualified for 2,672 two-provider semantic conflicts and 20 securities without 240 actual native source-minute observations (53 absent observed positions). The latter remain open P10 data debts; no vendor priority or synthetic minute fixes.

## 2. Physical consumer boundary

- `build_daily_ods_catalog(..., product_scope="daily-core-v1")` selects exactly two READY lanes, pins input metadata/SHA, and creates a schema2 product catalog with real exclusions. **Default** invocation still creates and verifies a full legacy four-lane v1 catalog.
- `build_daily_domain_increment()` accepts an explicitly verified v2 product catalog and builds the same governed seven DWD tables, but **only 1d bars**; all time/identity/price/PIT/source conservation checks and deferred event checks still run. All byte-level file digests and READY are verified after materialization.
- A core-v1 package records the quality policy identity/SHA, selected source manifest SHAs, prior package, plus minute unavailability. The package verifier rejects unknown products, altered policy or selected extra lanes. Existing legacy artifacts are never retrospectively relabeled or recut.
- `run-cn01-daily-dwd --product-scope daily-core-v1` has a separate daily-core root and `CURRENT_DAILY_CORE.json` pointer. Its first baseline 2026-09-24 accepts an **explicit read-only validated 9/23 legacy prior pointer**; future days must chain from the new product's own current. Neither kind can overwrite the other's pointer.
- `run-cn01-ods-daily --product-scope daily-core-v1` only runs the market and event lane, with daily-core-specific run root, catalog, notification-context and DWD output identity; excluded 1m/5m have no synthetic READY and are not fetched.
- OCI replication is intentionally separate: `sync-cn01-dwd-daily-core` requires an authenticated CN01 read-only SSH root `products/daily-core`, validates versioned source pointer/manifest/SHA, and mirrors into `/home/ubuntu/data/quanthus/dwd-daily-core`. The old `.../dwd-daily/CURRENT.json` is not a legal target.

## 3. Mandatory release gates

1. **Source:** exact market date/security/calendar, 4,992 date-aligned daily OHLC, PIT acquisition and frozen ODS source digests. If a required market or event lane, source file, row conservation, adjusted price basis, source schedule SHA or declaration is missing/mismatched, block daily-core. The event product cannot silently become optional without a new ADR/consumer review.
2. **Isolation:** inject absent/faulty 1m, 5m into fixtures and verify daily-core remains eligible while legacy four-lane remains fail-closed. No diagnostic m5 volume hypothesis can become actual 1m OHLC.
3. **Consumer:** inspect generated `dwd_security_bar` Parquet and assert exactly `interval_size=1d`, expected daily keys, source lineage and no 1m/5m. Strict 1m consumers still have no formally qualified 2026-09-24 product.
4. **Historical chain:** September 24 binds the 9/23 prior SHA read only, then no skipping of unsupported open trading dates in the new daily-core sequence. September 28 and subsequent open sessions need real ODS inputs and sequential versioned candidates. Closed October 1-7 do not fabricate market bars.
5. **Linux and remote:** focused unit/contract+full appropriate checks; independent CN01 and Hermes on the exact GitHub revision, physical permissions, restored service process cleanup, no disabled legacy timers silently reused, no target shadow root used as production.
6. **OCI:** read-only CN01 mirror credentials/permissions tested on a bounded package, separate root and pointer only. Verify exact manifest/content SHA and date monotonicity, same-date immutability, malicious legacy source/consumer misbinding rejection, and sync failure receipt.
7. **Activation/rollback:** only after 9/24 production package and OCI mirror both independently verified, introduce dedicated daily-core services/timers on both nodes and OCI, with explicit next-run time, owner and rollback. Legacy timers stay disabled, Base unchanged, safe source-availability budget, no duplicate market producer.
8. **Observation:** ten trading sessions denominator with data-ready time, size/coverage, missing products, notices, request counts, exact package SHAs, and rollback history. Do not promise daily readiness before actual evidence.

## 4. Backlog and time budget

2026-09-28 has CN01 READY market/events; 2026-09-29, 09-30 and 10-08 presently lack CN01 READY market/event snapshots. Backfill one **market open session at a time** after 09-24, with source date checks. Never jump the `CURRENT_DAILY_CORE.json` chain directly from 09-24 to 10-09 while silently omitting trading dates. New timers may collect the current session into distinct immutable lane attempts before backlog finishes, but **cannot claim the daily-core chronological DWD is caught up** until open-session holes are accounted for.

The full-universe 1m recovery has explicitly lower priority and an explicit stop: no repeated 4,992 request scans unless a named precision-sensitive consumer demonstrates material impact. This does not remove its open source debt or change strict 1m publisher.

## 5. Verified work and remaining acceptance

- CN01's first 2026-09-24 isolated cold release from frozen market/events produced exactly 4,992 1d security bars, 19,968 valuations, 4,992 tradability records, 1,468 disclosure + 1,468 subject, 3 indices and 3 calendar records, with deep SHA/READY verification.
- An independently replayed versioned `warehouse-quality-policy.v1.json` frozen SHA produced the same counts with daily-only declared consumer and separate shadow core pointer; confirmed an attempted write to legacy `CURRENT.json` is refused.
- All isolated outputs are diagnostic/non-production. Exact source identities and complete tests must accompany the implementation PR and the independent Hermes/OCI production receipts.

**Status is staged: code and shadow evidence do not authorize production by themselves.** Only report "restarted" after the new services are actually enabled/next run is shown, plus 9/24 primary and OCI exact receipts.

## 6. Current authorized rollout (Owner 2026-10-08)

Owner explicitly requested re-launch under the new standard, using 2026-09-24 as baseline. This authorizes this product-scoped implementation, versioned publication, verified CN01/Hermes/OCI baseline and dedicated timers after acceptance; the earlier docs-only restriction is not a request for repeated approval. The legacy four-lane/precision-1m contract and pointers remain unchanged.

Dedicated `quanthus-daily-core@cn01` / `@hermes` services use `run-daily-core-cycle`: one authoritative calendar request, at most two sequential market sessions per run, lock and process budget, no old-date skips and no optional minute acquisition. Five daily retry slots at 08:30/12:30/18:30/20:30/22:30 CST are operational catch-up, not a new transaction-time assertion. OCI's `oci-daily-core-sync` mirrors all immutable `date-pointers` sequentially to the separate root, not only the newest CURRENT.

Daily DWD uses its established serialized unadjusted-price value `price_basis=none`. Deep verification requires all seven declared tables, true 1d-only bars and accepted dated audit; it rejects an empty rehashed package or a 1m payload merely tagged daily. New current pointers cannot move backward, change a previously published date or skip a concurrent predecessor. Dedicated source/runtime release path `/opt/quanthus/daily-core-current` avoids silently replacing other production code.

## 7. Post-rollout compatibility correction

Full CI exposed a real source-identity regression outside the dedicated core runtime: P3-STRAT-003 pins the exact bytes of legacy `daily_domain.py` and `daily_ods_catalog.py`. Changing their bytes for an opt-in product tripped the existing research probe. Restore BOTH original files byte-for-byte; do not update the frozen probe hashes or research policy. The new product implementation is isolated in `daily_core_domain.py` and `daily_core_ods_catalog.py`, with the new core entrypoints importing that pair. Legacy package exports retain the original modules. A permanent test checks both pinned hashes. This copy preserves accepted legacy semantics while the new product evolves independently.

Separation verification: 74 product/cycle/mirror/orchestrator/P3 tests pass, including the previously failing frozen-probe test. Dedicated production processes already running ae154229 remain frozen for the duration of their jobs; do not hot-replace their code or rewrite the accepted 9/24/9/28 products to change code labels. Other CI failures for older dual-lane runtime/BaoStock versions and web tests require separate baseline attribution, not relaxing frozen research rules.
