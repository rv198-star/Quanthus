# OCI S2 Baostock Candidate Audit

| Field | Value |
| --- | --- |
| Audit date | 2026-07-25 Asia/Shanghai |
| Candidate | `st_status_pit_v2_baostock_20260725` |
| Source issue | [#8](https://github.com/rv198-star/Quanthus/issues/8) |
| Normalization blocker | [#36](https://github.com/rv198-star/Quanthus/issues/36) |
| Decision | Source candidate accepted; SoT promotion blocked on code-validity normalization |

## 1. Scope and independence

OCI pulled the immutable candidate through the Hermes read-only rsync channel. The audit did
not alter Hermes raw files, `ref_st_status_pit.parquet`, a `CURRENT` pointer, formal manifests,
PostgreSQL, or release gates.

Audited inputs:

- candidate `META.json`, `REPORT.json`, checksums, fetch ledgers, raw index, daily file, and
  compressed interval file;
- `st_status_pit_baostock_isST_semantics_v2_1_stats_20260725` gold cases;
- OCI copies of the security master, trading calendar, announcement status track, terminal
  boundary ledger, and unadjusted daily bars;
- 20 deterministic raw shards fetched independently from the read-only channel.

## 2. Candidate integrity

All checksums declared by `CHECKSUMS.sha256` pass, including:

| Artifact | SHA-256 |
| --- | --- |
| `daily_isST.parquet` | `6c1a93367131d33dc40aab108d9f94897d6d6880377205573df46c44988a6d37` |
| `st_status_pit_candidate.parquet` | `2158b4553074d95e9333c5166b052aa1bd8521716f9b30d8fd9d09b4ebe9646b` |

Independent structural results:

| Check | Result |
| --- | ---: |
| Daily rows / codes | 13,813,700 / 5,469 |
| Daily duplicate `(stock_code, date)` keys | 0 |
| Invalid `isST` or `tradestatus` values | 0 |
| BSE `920*` rows | 0 |
| Interval rows / codes | 7,449 / 5,469 |
| Invalid interval order / duplicate starts / overlaps | 0 / 0 / 0 |
| Invalid status-label mapping | 0 |
| Invalid `knowledge_cutoff = effective_start + 1 day` | 0 |
| Daily rows uncovered or multiply covered by intervals | 0 / 0 |
| Daily values differing after interval reconstruction | 0 |

The 5,538-code input universe contains exactly 69 securities delisted before 2011. Those are
the reported `oos_window=69`; they are not fetch failures. The other 5,469 codes all have a
candidate interval.

The 20 raw samples cover active, delisted, currently ST-class, historical transition, recent
listing, SSE, and SZSE cases:

`000001`, `000004`, `000010`, `000016`, `000498`, `000584`, `002127`, `002570`, `300859`,
`600000`, `600203`, `600212`, `600338`, `600610`, `600701`, `600892`, `601106`, `688806`,
`688981`, and `689009`.

For all 20 samples, the remote file SHA matches `raw_index.json`, raw row counts match the
index and payload, and every raw `date/code/isST/tradestatus` row equals the daily Parquet row.
All 27 binary-applicable v2.1 gold cases also replay from the full candidate and reach their
expected state inside the frozen evaluation window: 27 pass, 0 fail.

## 3. Calendar coverage

The local authoritative calendar ends on 2026-07-23. Within that verified range:

| Metric | Value |
| --- | ---: |
| Expected lifecycle/open-session keys | 13,808,541 |
| Candidate keys present | 13,808,499 |
| Keys absent | 42 |
| Exact terminal-boundary matches | 39 |
| Non-terminal absent keys | 3 |

The candidate also contains 5,201 rows dated 2026-07-24, all one session beyond the local
calendar snapshot. They are kept as candidate data but are not counted as independently
calendar-verified in this audit.

The three non-terminal absent keys are all `2012-09-10` under current codes `001872`, `001914`,
and `302132`. Their predecessor codes `000022`, `000043`, and `300114` contain the corresponding
rows. Conversely, three terminal exceptions for those predecessor codes are not absent from the
Baostock S2 daily file. This is a code-identity boundary, not a new missing-data exception.

## 4. Announcement-track cross-check

The delivered `comparable=752 / conflict=201 / incomparable=4248` result is a bounded 5,000-row
probe, not a full cross-check. It does not preserve the complete conflict key set, and exact-date
comparison can evaluate a suspended implementation/revocation date before the first tradable
session. It must not be used as a promotion pass rate.

OCI re-evaluated all 1,487 non-unknown announcement intervals and confirmed that the current
announcement track is not a binary gold source:

- it labels every `revoke_star` event as remaining `ST`, although removing delisting risk may
  result in either normal or another ST-class state depending on any remaining risk warning;
- many `revoke_star_and_other` rows still disagree with the direct historical status, indicating
  that announcement parsing does not always prove final implementation or full revocation;
- the existing track therefore remains useful as event evidence, early visibility, and possible
  `ST/*ST` detail, but cannot override direct historical `isST` on unresolved cases.

This supports a layered S2 contract after the normalization blocker is fixed:

1. Baostock `isST` is the primary binary `st_class/non_st_class` state source.
2. Announcement evidence may refine an earlier proven `knowledge_cutoff` and ST subtype.
3. Conflicts remain auditable; neither source is silently rewritten to agree with the other.

## 5. Blocking defect: code validity

The current security master assigns separate identities to current and predecessor codes while
giving the current code the predecessor's original listing date. Baostock returns predecessor
history under the current code as well, so normalized history contains the same economic asset
twice:

| Current / predecessor | Overlapping `none` days | Equal OHLCV days |
| --- | ---: | ---: |
| `001872` / `000022` | 1,941 | 1,940 |
| `001914` / `000043` | 2,176 | 2,176 |
| `302132` / `300114` | 3,426 | 3,426 |

Promotion without a code-validity mapping could allow the same asset to enter a historical
universe, event set, or portfolio twice. This violates the Chapter 01 rule that codes are
attributes rather than economic identity. The defect predates the S2 candidate and also exists
in normalized `none` bars; it is tracked in #36.

## 6. Decision and next actions

The Baostock fetch, raw evidence, daily data, compression, and binary semantics pass OCI audit.
Do not refetch or overwrite this candidate. Do not promote it unchanged.

OCI must first build a versioned security identity/code-validity mapping using authoritative
code-change dates, then crop or map normalized bars, universe membership, and S2 rows to
non-overlapping code intervals. A new immutable child revision can be built from the existing
raw shards. It must rerun:

- uniqueness by `(security_id, market_date)`;
- calendar coverage plus the exact terminal-boundary ledger;
- interval reconstruction and 27 gold cases;
- S3 enrichment and P1 acceptance evidence.

After that child revision passes, it may replace the announcement-derived track as the formal
binary S2 SoT while retaining the announcement track as supplementary evidence. Only then should
`CURRENT`, manifests, PostgreSQL, gates, #8, and the weekly incremental job be changed.
