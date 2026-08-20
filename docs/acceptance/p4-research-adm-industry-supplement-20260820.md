# P4 Research ADM Historical Industry Supplement Checkpoint

| Field | Value |
| --- | --- |
| Status | implementation/data-source checkpoint; production A/B acceptance still open |
| Work item | `P4-EVENT-002` / GitHub Issue #74 |
| ADM policy | `p4-event-research-adm@1.1.0` |
| ADM policy digest | `sha256:ace9e9a864e143f6b37bdea7539d83114d2d0102e6acd4def4865441f316213c` |
| P4 source policy | `p4-event-source.direct-dwd-v2.1-retrospective@2.2.0` |
| P4 source-policy digest | `sha256:b5373edc2f30d7d4786acb91c18fcc95c79517ef2bc1f8203c75d035f0e7f8b6` |
| Historical supplement provider | `baostock.query_stock_industry` / package `0.9.3` |
| Primary fixed snapshot | `2021-09-30` |
| Legacy fallback snapshot | `2012-12-31` |
| CSRC hierarchy SHA-256 | `sha256:fedcd39375e4de8a1dd6da28e63e119f0f50c6d818f429f58e8e30e62e6ff8e6` |
| Frozen source-content digest | `sha256:609fe99ea5eb2d29237a5c0112779ae58491270a80f390f099fb798de07382c9` |

## 1. Production-derived scope

The bounded production preflight identified exactly 259 candidate-universe securities missing from
current DWD company-industry membership. All 259 are historical-only listings; current-listed
missing coverage is zero. The complete listing-code set was emitted by production Job
`job_0aaf2cf61d7f455c8d002840cade279c` under revision
`9609f3081bf6a4716763448bca83693859aa19a1`.

The supplement is an ADM application reference only. It does not modify DWD V2.1, create a DWD
correction, or claim point-in-time historical industry membership.

## 2. Fixed source and deterministic selection

BaoStock is already an admitted ODS provider dependency in this repository. The supplement uses
`query_stock_industry(date=...)`, whose returned classification is `证监会行业分类`, under two fixed
snapshot dates:

1. query `2021-09-30` for the complete 259-code set;
2. accept the non-empty CSRC-2012 industry code returned there;
3. only when that row exists but its industry is empty, query the same security at `2012-12-31`;
4. map the legacy CSRC industry name to exactly one current CSRC-2012 leaf by deterministic name
   containment; ambiguous or missing mappings fail closed.

No current date, mutable pointer, favorable outcome, event return, label, or post-T value enters the
selection.

## 3. Real-source qualification result

A complete live-source qualification over the production-derived 259-code set produced:

| Result | Count |
| --- | ---: |
| requested historical-only securities | 259 |
| primary `2021-09-30` non-empty CSRC rows | 256 |
| legacy fallback rows | 3 |
| unresolved securities | 0 |
| distinct CSRC-2012 leaf industries | 55 |

The three fallback securities are:

| Listing code | Historical name | Legacy source industry | Deterministic CSRC-2012 leaf |
| --- | --- | --- | --- |
| `600102` | 莱钢股份 | 制造业-黑色金属冶炼及压延加工业-金属非金属 | `C31` 黑色金属冶炼和压延加工业 |
| `600263` | 路桥建设 | 建筑业-土木工程建筑业 | `E48` 土木工程建筑业 |
| `600991` | 广汽长丰 | 制造业-交通运输设备制造业-机械设备仪表-汽车制造业 | `C36` 汽车制造业 |

Canonical serialization of all 259 selected source rows and resolutions yields the frozen source
content digest:

`sha256:609fe99ea5eb2d29237a5c0112779ae58491270a80f390f099fb798de07382c9`.

ADM construction must reproduce this exact digest. A changed provider response, missing row,
ambiguous legacy crosswalk, hierarchy mismatch, or provider-version mismatch fails closed before
Event Library forward scanning.

## 4. Runtime packaging and authority

ADM 1.1 adds `static_industry_source_receipt.json` to the immutable `adm/` package. The receipt
binds provider/version, fixed query dates, CSRC hierarchy SHA, all selected historical source rows,
resolution mode, and the frozen source-content digest. `static_industry_mapping.json` then combines:

- current DWD company-industry membership where DWD coverage exists; and
- the checksum-qualified 259-security historical supplement only where DWD coverage is absent.

Both paths end at governed DWD `security_id` and the same CSRC level-one concentration groups. The
result remains `static_non_pit_concentration_only`; it is not candidate-signal input and cannot
create execution authority.

## 5. Acceptance remaining open

This checkpoint proves source availability, complete 259/259 data resolution, deterministic
normalization, and a frozen source digest. It does not close Issue #74. Remaining gates are:

1. repository-wide test/build gate after integration;
2. exact production API/Event Worker deployment;
3. a fresh full-corpus Run A with ADM coverage 100% and non-zero Event Library output;
4. only after Run A succeeds and its immutable Run manifest is readable, submit Run B;
5. reproduce candidate/executable/not-executable counts, Event manifest SHA-256, content digest,
   and ordered event identity set before closing #74.
