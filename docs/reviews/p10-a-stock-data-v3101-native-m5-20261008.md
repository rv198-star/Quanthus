# P10 — A Stock Data v3.10.1 historical 1m/m5 source verification (2026-10-08)

| Field | Result |
| --- | --- |
| Upstream | simonlin1212/a-stock-data release v3.10.1, commit 8f6a6a53a59813bf5f010875f16f794fdc4f44f4 |
| Scope | 2026-09-24 remaining 20 native 1m source gaps plus bounded 5-control source discrepancies |
| Collection status | Real upstream m5 source fetched and immutable raw archived on CN01; no production collection or publish |
| Engineering status | New isolated *diagnostic-only* exact source-frequency volume witness; zero-gap source contract NOT approved |
| Hard gates | Strict actual-240-minute bars, true nonmissing OHLCV, original dual-qualified source conflicts, PIT/READY/DWD/OCI remain unchanged |
| Related | #436, #440, #456, #492, #493; T+1 draft PR #490; upstream issue #57 |

## A. Latest upstream is not an updated historical 1m provider

The latest Git tag / main HEAD are both v3.10.1 at the frozen SHA above (live git ls-remote, October 8). A Stock Data is primarily an embedded SKILL.md Python recipe, not a distributable production package that can simply replace Quanthus. The source was cloned to an isolated directory on CN01, never installed into the production collector. Relative to v3.10.0, the executable AST bodies of tencent_kline and _tencent_kline_call are identical; the v3.10.1 additions correct volume documentation and improve recent-trading-session tencent_ticks retries/completeness.

Upstream maintainer issue #57 explicitly confirms: non-STAR historical Tencent m1 eventually gets exact sub-lot shares at next trading session, Tencent m1 has a short ~800-bar raw window (upstream helper m1 count capped to 320), and the new tick API only provides the most recent session. v3.10.1 alone cannot access native September 24 minute gaps or resolve 2,672 inter-source semantic disagreements. STAR 688/689 m1/m5 volume is already shares, other in-scope ordinary stock m1/m5 is lots times 100. Do not confuse STAR shares with the rounded Tencent tick unit.

## B. Newly recovered actual same-date m5 from v3.10.1

Via the pinned upstream tencent_kline(period=m5,count=320) entrypoint, CN01 actually fetched native Tencent m5 for the 20 precise 2026-09-24 hard-gap securities, plus five control stocks. Each of the 20 returned the complete 48 observed native 5m buckets for 2026-09-24, with exact sum of day shares and daily close matching the SHA-sealed 2026-09-24 BaoStock market references.

Collecting 320 m5 bars now still reaches 2026-09-24 despite m1 history being unavailable; this should be regarded as a bounded historical-source opportunity, not proof that the 1m 240-row product exists. The m5 source offers no minute amount and cannot reconstruct a genuine 1m trade tape. Raw responses and physical SHAs are stored in quarantine at:

/root/work/astock/data/quarantine/a-stock-data-v3.10.1-20261008/

Reference identities:

- old strict ledger SHA256 2dd3f24422276f73900f5bb76b44fd152e1237a82f29c7518e2307ebe8d96780
- sealed daily SHA256 8de54259119bf8a7590f935271d6d2c28cabe72d23979bb5f3c8c7d975ae0136
- sealed native BaoStock 5m SHA256 eb295b8891f7e727461860e14d50e60fc569e636caa58da981096b28fbacde35

## C. Real 20-security cross-frequency witness — no generated missing OHLC

The new Quanthus source_native_m5_support implementation accepts only caller-supplied raw bodies and their expected SHA-256. It checks exact code/day, source rows/units, observed 48 native m5 timestamps, 240 canonical m1 *expected positions* and raw observations, actual 09:30 auction, finite nonnegative OHLCV, exact 5m bucket volumes, sealed daily shares and final close. It never writes a single derived m1 row, never returns an ODS artifact and has no network access.

| Result on all 20 historical hard-gap securities | Count |
| --- | ---: |
| Source-native m5 returned all 48 real buckets | 20 / 20 |
| Source m5 share sum equals independent sealed daily | 20 / 20 |
| Source m5 close equals sealed daily close | 20 / 20 |
| Actual Tencent m1 observed-volume sum equals each matching m5 bucket | 20 / 20 |
| Missing original m1 timestamps (total) | 53 |
| Same-vendor m5 corroborates zero-volume hypothesis | 20 / 20 |
| Supplied missing m1 OHLC source observations | **0 / 53** |
| Formally newly qualified 240-real-minute securities | **0 / 20** |

This supports source-internal volume conservation at the 5m clock boundary, not independently proven missing-minute OHLC. The synthetic 5m price normalization used during earlier exploration matched 48 H/L/C/V buckets for 15/20 and 48 full OHLCV buckets for 9/20. Those are *non-publishing hypotheticals*, not permission to fabricate bars. Cross-frequency source provenance: native m5 and m1 come from the same Tencent backend; neither is independent exchange-level tick truth.

Frozen real replay output:

/root/work/astock/data/quarantine/a-stock-data-v3.10.1-20261008/20-cross-frequency-source-witness-records.jsonl
/root/work/astock/data/quarantine/a-stock-data-v3.10.1-20261008/20-cross-frequency-source-witness-summary.json

Each record enforces formal_m1_qualified=false, ods_ready_authorized=false and dwd_publication_authorized=false, and stores actual source SHA, complete missing-minute identities and any 5-minute volume discrepancies.

## D. Five bounded cross-market source-difference controls

For 000001, 002460, 300750, 600000 and 688001, source-native Tencent 5m is volume-identical in all 48 buckets to Tencent 1m after the governed 09:30 merge. In deep SZ, this source-native volume is often not equal per bucket to independent BaoStock 5m; daily sums remain equal. For SH 600000 and STAR 688001, Tencent-native and BaoStock-native 5m equal all OHLCV buckets; m1-regrouped open at 13:05 can still differ due to the morning-close carry-forward. A source's native 5m is a geometry witness, not a majority-vote to select among two qualified 1m sources.

Other two historical hard-gap controls 000726 (one absent m1 row) and 301203 (17 absent m1 rows) match the native Tencent m5 in 48/48 volume and HLCV buckets under an explicitly hypothetical no-trade normalization, not a real 1m observation.

## E. Integration boundaries and next decision

1. Promote only this nonpublishing read-only provenance witness in a separate scoped PR with synthetic unit tests and real replay evidence, keeping the production collectors, MINUTE_SOURCE_POLICY, amount schema, exact 240-observed-row gate, source conflict policy and disabled timers unchanged.
2. Draft separately a future P10 source-native sparse/no-trade interpretation contract: explicit time-label and auction authority, independently verified historical tick evidence for positive versus zero, separate derived/observed row flags, semantic hash and consumer impact. Do **not** auto-activate such a contract. A source-internal m5 sum cannot by itself create OHLC for missing minutes.
3. Confirm licensed truly independent historical 1m/tick source capability on 000726, 301203, 300190 and cross-venue samples under #493; no RQData or official Tushare Pro direct minute client/auth was configured in the checked CN01/Hermes/RX6800 environments. Existing paid Tushare-style relay is not official direct Tushare permission. Tushare official historical minute permission is separate from normal integral tiers (https://tushare.pro/document/2?doc_id=290). Do not purchase or use an unrelated relay token without specific authorization.
4. Resolve the separately preserved 2,672 conflicting double-qualified candidates under #492; any new source priority/waiver demands explicit reviewed semantic contract, not a 5m equality heuristic. Re-qualify the frozen 4,992 denominator independently on a fully versioned policy before publication, then complete CN01/Hermes and OCI SHA/manifest acceptance.

## F. Verification and authority ceiling

Upstream v3.10.1: 150 relevant offline v3.9/v3.10 tests passed (live tests opt-in skipped). The optional broader 177-test upstream run was not fully green: 14 unrelated Excel tests failed to start because isolated openpyxl was absent; an attempted isolated dependency install timed out and was stopped without modifying any production environment. Quanthus new source-witness and related minute suites: 55 passed; focused Ruff and compile validations are retained separately. Linux runtime exact source evidence verified in the isolated checkout.

CN01/Hermes remain on production commit be96a7cb3b2b7e41937aef7e5b19ab5c5fbee9f1 and all 1m/daily/maturity timers remain inactive/disabled. OCI CURRENT DWD is 2026-09-23. No production merge/deploy, source relaxation, fabricated missing-minute bar, ODS READY, DWD, notifications or OCI mirror is authorized by this document.

Reference issues: https://github.com/simonlin1212/a-stock-data/issues/57 ; https://github.com/rv198-star/Quanthus/issues/492 ; https://github.com/rv198-star/Quanthus/issues/493 .