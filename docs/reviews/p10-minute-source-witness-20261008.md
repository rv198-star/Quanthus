# P10 1m 2026-09-24: historical-source capability and conflicting-provider semantic witness

| Control | Value |
| --- | --- |
| Date / parent | 2026-10-08 / P10-DATA-000, #436, #440, #456 |
| Scope | 20 still without a qualified whole-security 1m source; 2,672 double-qualified conflicting 1m securities |
| Status | **EVIDENCE_COMPLETE / SOURCE_DECISION_OPEN / NO_GO_DATA / NO_GO_PRODUCTION** |
| Authority | Owner authorized investigation and advancement; not blanket relaxation of source, missing-grid, PIT, four-lane or publication gates |
| Upstream fact | A Stock Data issue #57: Tencent recent-session odd-lot backfill; Shanghai/Shenzhen 1m boundary differences; minute amount unavailable |
| Prior code | T+1 next-trading-session engineering is **draft PR #490**, not deployed |
| Production | CN01/Hermes code be96a7c; daily/1m/maturity timers disabled/inactive; OCI DWD CURRENT remains 2026-09-23 |

## 1. Immutable evidence closure

All numeric results below are from actual 2026-09-24 material on CN01. Raw provider bodies and market references were read-only. The 2,672 pair witnesses were each re-read, raw SHA verified, reconstructed through current whole-security adapters, then validated against the original strict candidate's OHLCV semantic SHA before 48 native 5m buckets were computed. No new market-wide HTTP collection was performed.

| Asset | On-CN01 location relative to /root/work/astock/data/quarantine | SHA-256 |
| --- | --- | --- |
| Original strict 4,992 ledger | minute-ledger/2026-09-24-strict-20260929T0250/records.jsonl | 2dd3f24422276f73900f5bb76b44fd152e1237a82f29c7518e2307ebe8d96780 |
| Source numeric differences, 4,992 ledger | minute-ledger/2026-09-24-source-differences-20260928T2212/records.jsonl | d3805700f12aee1beebd3dd14561cf9624a22363230151f566cf7b74323109f0 |
| Sealed market daily | minute-cold-test/2026-09-24-20260928T2331/shadow/releases/daily/2026-09-24/20260924-20260928T153132189736Z/artifacts/daily_ohlcv_none_delta.parquet | 8de54259119bf8a7590f935271d6d2c28cabe72d23979bb5f3c8c7d975ae0136 |
| Independent BaoStock native 5m | minute-cold-test/2026-09-24-20260928T2331/shadow/releases/daily/5m/2026-09-24/20260924-20260928T154502992058Z/artifacts/intraday_5m_bars.parquet | eb295b8891f7e727461860e14d50e60fc569e636caa58da981096b28fbacde35 |
| Full T/P versus BaoStock 5m bucket witnesses (2,672) | minute-semantic-diagnosis/2026-09-24-20261008-ref5m/records.jsonl | a37ac34ed428c23721ea3cbaecdb78b16588dcd58dd64f6b4213e58edf53394b |
| Witness source/status/venue cohort | minute-semantic-diagnosis/2026-09-24-20261008-ref5m/cohort-analysis.json | c7e05f6cc20579e6af8181fb1a10ca8b91cb10d35c5c667de5fa7f880a8a497a |
| Four additions, 20 residual incremental diagnostic | minute-ledger/2026-09-24-tplus1-20261008-strict-overlay/summary.json | c5c8a4da24bf04a6f8ec0924e72d10f39465ddea5e0950ae6f0e95abc4ed0115 |
| Sino-Tencent 20-security source absence cross-check | minute-semantic-diagnosis/2026-09-24-gap20-sina-tencent-20261008.json | d932183f1521ca65a30947656c3f3dfc0a7959aa840ba14f15e281d5bb5300dd |

All evidence outputs are quarantined diagnostics and explicitly carry publication_authorized=false. Candidate/readiness labels from prior runs were not trusted instead of re-evaluation. Private API keys, source raw bodies and market rows were **not** committed to Git.

## 2. Exact decomposition of 2,672 conflicts

Both Tencent mkline m1 and paid Tushare-style relay previously passed their individual real-240-bar, exact daily-share volume and sealed close checks, but their normalized minute OHLCV semantic hashes differed. 1,588 securities have a per-minute volume distribution difference; 2,671 have a price difference. Hash conflict does **not** prove either source is bad.

Each source's 240 minute bars was grouped into 48 end-labeled five-minute OHLCV bars and compared **numerically** (not by rounding or relaxed epsilon) with the independent sealed BaoStock native 5m product.

| All 48 five-minute buckets match independent reference | Volume only | High/low/close/volume jointly |
| --- | ---: | ---: |
| Tencent only | 79 | 472 |
| Paid only | 1,493 | 58 |
| Both sources | 520 | 43 |
| Neither source | 580 | 2,099 |
| **Total** | **2,672** | **2,672** |

**Volume-only market breakdown:** SZ Main 596 paid-only, 486 neither (n=1,082); SZ ChiNext 897 paid-only, 91 neither (n=988); SH Main 482 both, 78 Tencent-only, 3 neither (n=563); SH STAR 38 both, 1 Tencent-only (n=39).

The 48-bucket criterion is *supporting evidence*, **not a newly authorized source qualification gate or a truth-owner vote**:

- Shenzhen Tencent 1m boundary assignments can differ from BaoStock; 48 native 5m misses alone do not invalidate its source-native 1m.
- The 13:01 first minute may carry the prior morning's close rather than the actual afternoon first trade; even Shanghai Tencent can differ from native 5m on open only.
- Paid source-derived 5m sometimes agrees with independent 5m volume/close but has different high/low or raw minute prices.
- SH 600000 and STAR 688001 illustrate Tencent 48/48 native volume alignment, including 13:05 open-provenance differences; SZ 002460 and 300750 illustrate paid 48/48 native volume agreement while Tencent has different 5-minute distributions. SZ 000001 shows that **both** can fail most native 5m buckets despite exact daily totals.

**Do not** change source order based on this matrix alone. A source-specific bar-label, opening/closing auction, value-unit, non-trade and exchange-boundary contract must precede any policy that changes dual-source conflict behavior. Where contracts are non-equivalent, semantic hashes should not be presented as equivalent, but separate product/source identity is required rather than silent conflict cancellation.

## 3. Actual remaining 20 hard gaps

The October 8 authorized paid-relay re-request of the original 24 hard-gap securities returned 24/24 HTTP 200. Four source-whole-security candidates passed strict current gates: 000404, 000755, 300501, 301502. The **other 20** did not pass exact daily-share volume / opening auction / first native 5m gate, even when 240 raw observations were returned.

The residual codes are:

000726, 002917, 002959, 003028, 300187, 300190, 300899, 300908, 300994, 301009, 301049, 301097, 301100, 301103, 301173, 301203, 301221, 301288, 301296, 301491.

### Bounded alternate-source trials, October 8

- **Sina source-native 1m:** All 20 queried, HTTP 200; all 20 independently aggregate to the correct daily exact share count and final close, but only 170–234 observed standard session minutes for each, **0/20 satisfy the governed 240 real bar grid**. In 14/20 the Sina source has an absent timestamp for which Tencent's real source observation has positive volume, directly refuting any universal "missing row = zero trade" conversion. The 20 raw bodies, source hashes and bounded receipts are preserved under minute-semantic-diagnosis.
- **Liquid control sample:** 000001, 002460, 300750, 600000, 600010 and 688001 each return only 238 Sina lines, missing 14:58 and 14:59; this is a provider representation / auction label phenomenon, not proof of 240 actual source bars.
- **Eastmoney official recent trends 1m:** One bounded 000726 canary on CN01 returned ConnectionError, consistent with known prior source access trouble but not proof of universal outage. No broad retries were performed.
- Tencent m1 historical retention has expired for 9/24 (once <=800 recent bars cover only several more recent trading sessions). Another blind full-universe Tencent request cannot recover the missing date.
- RQData historically supports A-share 1m/tick; Tushare Pro direct stk_mins separately entitles the historical minute endpoint. Neither independent, account-authorized provider was available as a validated 2026-09-24 full-grid source in this investigation. **Do not purchase, sign up, or represent a public SDK as verified data without a sampled account-backed receipt.**

This is **not** merely a stale T+1 delay problem: neither existing paid source nor historical Sina supplies all 20 current-contract full 1m securities.

### Cross-market six-security third-source negative control

A separate October 8 Sino raw canary examined high-liquidity 000001, 002460, 300750, 600000, 600010 and 688001. All returned 238 dated rows. These bars are **not interchangeable** with either of the original 240-bar providers: on the 238 same-clock observations, exact per-minute volume agrees with Tencent or paid on only 4 to 85 timestamps (depending on symbol). Three Shenzhen liquid samples match the independent day-total volume, but the Shanghai samples do not: 600000 misses 50,100 shares, 600010 misses 88,900, STAR 688001 misses 7,039 relative to sealed daily. Differences include the closing auction representation.

Therefore even when Sina gives an accurate total and last close for the 20 illiquid hard gaps, it must not be used to vote for a winning vendor on 2,672 conflicts. Its own volume/clock convention requires independent qualification and complete source-period evidence. Receipt: minute-semantic-diagnosis/2026-09-24-sina-third-witness-20261008/cross_venue_three_source_observations.json. No publication authorized.

## 4. Required P10 authority decisions and shortest honest route

### A. Semantic decision for 2,672

First prove equivalence classes per *venue + provider + price/volume units + bar label + auction/no-trade behavior*. Use real recent tick evidence for 6 narrow canaries (SH main 600000, STAR 688001, SZ main 000001/002460, ChiNext 300750 and another conflicting source) and at least a second trading date. Where tick timestamps differ by market, codify separate source-native contracts rather than weakening the sealed daily, PIT or 240-real-minute checks.

Only if the governing source contract and independent calibration explicitly reject one otherwise individually qualified whole source may its current per-security qualification change; old immutable receipts must not be relabeled. A normal source priority is not a substitute for a same-semantic conflict adjudication.

### B. Reliable data for 20

Trial a genuinely independent historical 1m/tick provider on **a bounded selected sample** (start 000726, 301203, 300190 and cross-market controls). Suggested candidate capabilities to verify, not an authorized purchase:

- RQData historical 1m (or verifiably complete historical tick) with no adjustment, no forward-filled suspended/non-trade rows and explicit timestamp/source-accuracy contract.
- Official Tushare Pro stk_mins with separately granted minute permission, distinguished from the already tested Tushare-style *relay*. Sample data must be measured; identical backend data is not an independent witness.

A passing 20/20 formal candidate must have a source-authentic, exact date/code/frequency/adjustment and unit identity; *actual* 240 observations (or a separately authorized, evidence-backed revised sparse/no-trade schema), exact sealed daily shares and close, complete raw checksums, no unauthorized splitting/stitching, and independent first-5m semantic verification.

### C. If actual 1m is unavailable

Record these 20 as **source unavailable under current contract**, not synthetic zero bars. A separate architectural ADR may review whether historical sparse source-native 1m should become a distinct, explicitly incomplete product, or whether daily DWD must be decoupled from optional 1m. Both would change the existing four-lane publication/consumer contract and must be separately authorized with PIT and protected-partition impact checks. This investigation does **not** enact either change.

## 5. Stop conditions and next verification

- Do not enable CN01/Hermes daily, lane or maturity timers; do not merge draft #490 solely because these diagnostics completed.
- Do not publish partial DWD or OCI mirror on the 2,300 candidate / 2,672 conflict / 20 no-source overlay.
- Do not retroactively synthesize no-trade OHLC values, split 1m bars across providers, assign daily residual shares or assume a numerical epsilon proves causation.
- Freeze a versioned source-identity/auction/minute-boundary contract and test **independent live or historical raw** on narrow cross-exchange samples; re-evaluate the 4,992 actual denominator under one reviewed version before any READY.
- Pass separate data engineering, Linux/role, dual-node independent build, date/manifest/hash and OCI mirror gates before requesting Owner approval to resume production timers.

Source references: upstream https://github.com/simonlin1212/a-stock-data/issues/57 ; programme https://github.com/rv198-star/Quanthus/issues/456 ; historic source qualification https://github.com/rv198-star/Quanthus/issues/436 ; T+1 draft https://github.com/rv198-star/Quanthus/pull/490 .
