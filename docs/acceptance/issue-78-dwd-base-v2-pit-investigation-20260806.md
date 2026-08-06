# DWD Base V2 Historical PIT Investigation Evidence

| Field | Value |
| --- | --- |
| Evidence status | blocked finding; code and CN01 ODS audits complete, physical DWD and Hermes ODS mirror audits incomplete |
| Evidence date | 2026-08-06 |
| Governing work | `META-DATA-PIT-001` / GitHub Issue #78 |
| Investigated package | `dwd-base-202607-v2` |
| Manifest SHA-256 | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` |
| Content digest | `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9` |
| DWD audit policy | `config/audits/dwd-pit-semantics.v1.json` / `sha256:9593aa765b0681e36560ec52820b4b8f13158d54e7c7b9b595bd630eecb4d7ab` |
| ODS sufficiency policy | `config/audits/ods-pit-sufficiency.v1.json` / `sha256:1dc04ea312a4600bc9a4c62e6d594f5c557b6d6688bfcfbff2b31980a6d9d8d5` |
| Successor boundary | new `dwd-base-202607-v2.1`, through 2026-07-31; later Daily excluded and cleaned separately |
| Proposed remediation | `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` |
| Consequence | V2 historical PIT use remains blocked; no Base or consumer pointer changed |

## 1. Investigation Decision

The Stage B P4 blocker is a package-wide DWD authority issue, not a P4 reader defect. Code inspection
confirms that 17 of 33 tables substitute a Base build/acquisition timestamp for historical
availability, four tables have source-family or row-level historical availability ambiguity, five
tables already use source-derived historical timestamps, and seven tables are correctly
forward-only operational/governance objects. The fourth ambiguous table is
`dwd_shareholding_change`: 124,674 of 194,322 exact ODS rows lack `disclosure_date`, so the
builder's fallback to business `change_date` cannot be accepted as historical knowledge.

This evidence does not close Issue #78. The physical package bytes and installed Hermes, CN01, and
OCI mirrors have not all been read by the current principal. The sealed V2 package therefore remains
blocked for historical PIT consumers. No historical scan, timestamp override, legacy fallback,
Base mutation, package republish, or pointer movement is authorized.

## 2. Mirror Inventory And Physical Access

ADR 0007 assigns node-local DWD Base use to Hermes and CN01 and a verified CN01 DWD mirror to OCI.
The final audit therefore requires the three labels `hermes`, `cn01`, and `oci`.

| Mirror | Expected role | Current evidence | Physical result |
| --- | --- | --- | --- |
| Hermes | Node-local immutable Base used by the Hermes DWD builder | Deployment configuration names `dwd-base-202607-v2` | Not accessed from the OCI DevSpace boundary |
| CN01 | Node-local immutable Base and source of OCI's current DWD mirror | Deployment configuration names `dwd-base-202607-v2` | Not accessed from the OCI DevSpace boundary |
| OCI | Read-only verified DWD mirror | Protected root identified as `/srv/quanthus/dwd` | Root metadata was visible; `manifest.json` and child contents were unreadable to DevSpace |

A real node-local manifest-only probe used the new audit command against the OCI root. It returned
exit code `2`, report digest
`sha256:237443673e86f835c6aea3f0c330e702529262e5dbfec15647bc7afc3d1b1123`, and reason codes
`required_mirror_not_supplied` plus `mirror_verification_failed`. The report recorded
`mutation_performed=false`. No DWD Parquet file or market-data partition was opened.

A corresponding OCI topology probe used the ODS audit command with the expected Hermes/CN01
package paths. Because OCI intentionally holds neither ODS Base, both labels failed verification,
the command exited `2`, report digest was
`sha256:fb803e6320b46d27bbb53b1293a0e05e5cd82a94451af08aac439ba00059e853`, and
`mutation_performed=false`. This confirms that `/home/ubuntu/data/astock` is not an ODS Base
substitute.

CN01 was then audited through temporary, explicitly authorized root access. The exact
`/srv/quanthus/ods/base/ods-base-202607-v2` package passed deep verification with exit status `0`:
112 datasets, 21,396 files, 13,643,600,642 bytes, manifest
`sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b`, and content digest
`sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01`.
READY and `BASE_CURRENT.json` matched that identity. The run took 378.97 seconds and recorded no
package, service, or pointer mutation. The temporary public key and `/tmp` audit files were removed
afterward.

The missing physical evidence is explicit:

- READY/manifest binding and every declared file SHA-256 on all three installed copies;
- cross-mirror manifest/content equality;
- actual table schemas and row counts;
- minimum/maximum and year distributions of `knowledge_from`;
- representative 2012-2021 rows whose knowledge is in 2026; and
- checksum-identical verification of the Hermes ODS V2 mirror.

## 3. Complete 33-Table Code And Source Matrix

`Physical evidence` is `pending` for every row until the read-only three-mirror scan completes.
The exact evidence statements and remediation notes are machine-readable in the audit policy.

| Table | Code disposition | Historical PIT authority | ODS sufficiency | Physical evidence |
| --- | --- | --- | --- | --- |
| `dwd_listed_company` | build timestamp substituted | blocked | stable identity reconstructable; historical display attributes partial | pending |
| `dwd_company_identifier` | build timestamp substituted | blocked | organization mapping complete; historical attribute versions partial | pending |
| `dwd_security` | build timestamp substituted | blocked | existing ODS resolves all historical bar/valuation codes | pending |
| `dwd_security_identifier` | build timestamp substituted | blocked | current code set plus eight historical code intervals | pending |
| `dwd_listing` | build timestamp substituted | blocked | stable listing intervals reconstructable under approved identity rule | pending |
| `dwd_trading_venue` | build timestamp substituted | blocked | versioned static venue contract required | pending |
| `dwd_security_bar` | build timestamp substituted | blocked | reconstructable from completed observation interval under a new contract | pending |
| `dwd_security_valuation` | build timestamp substituted | blocked | provider-availability evidence required or fail closed | pending |
| `dwd_adjustment_factor` | source-derived historical | conditional on corrected parent identity | existing ODS knowledge cutoff is sufficient | pending |
| `dwd_adjustment_factor_input` | source-derived historical | conditional on corrected parent identity | existing ODS knowledge cutoff is sufficient | pending |
| `dwd_market_index` | build timestamp substituted | blocked | versioned benchmark evidence or fail closed | pending |
| `dwd_index_bar` | build timestamp substituted | blocked | reconstructable from completed observation interval under a new contract | pending |
| `dwd_trading_calendar` | build timestamp substituted | blocked | reconstructable under a reviewed conservative session rule | pending |
| `dwd_security_status_interval` | build timestamp substituted | blocked | 1,492 proven ST rows reconstructable; 5,538 remain fail closed | pending |
| `dwd_listing_status_interval` | build timestamp substituted | blocked | suspensions reconstructable; lifecycle depends on corrected identity | pending |
| `dwd_price_limit_regime` | build timestamp substituted | blocked | source knowledge cutoff is available | pending |
| `dwd_tradability_state` | build timestamp substituted | blocked | recomputable from corrected inputs and observation completion | pending |
| `dwd_observation_gap` | forward operational | not historical authority | correctly retained from detection time | pending |
| `dwd_disclosure` | source-derived historical | conditional on corrected parent identity | provider publication time is available | pending |
| `dwd_disclosure_subject` | source-derived historical | conditional on corrected parent dependencies | provider publication time is available | pending |
| `dwd_corporate_action` | build/acquisition timestamp substituted | blocked | exact ODS has announcement date for all 40,594 action rows and 54,509 dividend rows | pending |
| `dwd_corporate_action_term` | build/acquisition timestamp substituted | blocked | inherits corrected announcement-date timing | pending |
| `dwd_financial_report` | source-family ambiguity | blocked pending classification | partially reconstructable; remainder fails closed | pending |
| `dwd_financial_concept` | source-family ambiguity | blocked pending classification | earliest defensible published use required | pending |
| `dwd_financial_fact` | source-family ambiguity | blocked pending classification | partially reconstructable; remainder fails closed | pending |
| `dwd_industry_scheme` | forward operational | not historical authority | current evidenced scheme only | pending |
| `dwd_industry_node` | forward operational | not historical authority | current evidenced hierarchy only | pending |
| `dwd_company_industry_membership` | forward operational | not historical authority | first defensible snapshot only | pending |
| `dwd_share_capital_observation` | forward operational | not historical authority | acquired F10 snapshot only | pending |
| `dwd_shareholder_count_observation` | source-derived historical | conditional on corrected parent identity | source knowledge/notice date is available | pending |
| `dwd_shareholding_change` | source ambiguity | blocked for rows without disclosure evidence | 69,648 rows have disclosure date; 124,674 require additive evidence or fail closed | pending |
| `dwd_shareholding_observation` | forward operational | not historical authority | acquired F10 snapshot only | pending |
| `dwd_quality_exception` | forward operational | not historical authority | correctly retained from detection time | pending |

### 3.1 Confirmed Builder Findings

- `listed_company.py` initializes identity member knowledge no earlier than the Base plan cutoff and
  propagates that maximum through company, security, identifier, listing, and venue rows.
- `trading_calendar.py` writes the maximum of Base cutoff, source `built_at`, and venue knowledge for
  every historical session.
- `market_observation.py` writes the Base cutoff into bars, valuations, benchmark definitions, and
  index bars.
- `tradability_state.py` writes `build_cutoff()` into security/listing status, price-limit regimes,
  derived tradability, and gap detections. Gap detections are correctly forward governance events;
  the other four objects require historical reconstruction.
- `disclosure_event.py` correctly uses announcement publish time and factor source cutoff, but
  corporate actions prefer source `built_at` over `announce_date`.
- `financial_reporting.py` uses `acquired_at` or a Base-cutoff fallback even though the exact full
  statement source has valid `publish_date` on all 852,497 decoded report versions. Canonical
  quarterly and `fin_abstract` still have unmatched coordinates without actual publication time.
- `industry_classification.py`, F10 ownership parsing, and quality governance intentionally preserve
  first acquisition/detection time and must remain forward-only.
- shareholder counts retain source-derived historical timestamps. Holder changes are only
  source-timestamped where `disclosure_date` is present; the current `change_date` fallback is not
  accepted as knowledge time.

## 4. ODS Impact Assessment

The following facts come from the deep-verified CN01 ODS V2 package, not the OCI source mirror.

| Source family | Exact package evidence | Investigation disposition |
| --- | --- | --- |
| Security/company/listing identity | 5,868 unique master rows; all 5,329 bar codes and 5,316 valuation codes resolve; 6,217 org ids; 8 historical code-validity intervals | stable joins reconstructable under a conservative identity contract; historical display/reference attributes remain unavailable unless evidenced |
| Daily/minute security bars | 13,815,115 daily rows through 2026-07-24; historical rows have only 2026 `built_at`; minute archives passed package deep verification | use versioned session/interval-completion knowledge rules |
| Daily valuation | 11,416,988 rows through 2026-07-18; no historical publication/acquisition field | provider-semantic evidence required or history fails closed |
| Benchmark definitions | current metadata only | static/versioned benchmark contract or additive evidence required |
| Index bars | 23,242 rows through 2026-07-24; no historical fetch time | use session-close rule after benchmark identity approval |
| Trading calendar | 17,055 canonical rows through 2026-07-25 with one 2026 build time; non-conflicting historical Hotfix retained | realized sessions may use conservative session/date end; no earlier future-schedule authority |
| ST and daily provider state | 7,030 rows, all with cutoff; only 1,492 have proven events and 5,538 are explicitly not proven | use proven rows; keep not-proven rows fail closed; daily state uses session completion |
| Suspensions | 2,745 rows, all with cutoff; 543 become known after suspension start | use exact cutoff without backward projection |
| Price-limit regimes | 2,750 rows, all with announcement and cutoff | source timing sufficient |
| Adjustment factors | 46,649 rows with cutoff: 39,739 action-announcement, 2,737 provider-notice, 4,173 no-provable-announcement exceptions | source timing sufficient; retain all 4,173 fail-closed exceptions |
| Announcements | 6,395,198 rows, no null `publish_time` | source timing sufficient |
| Corporate actions/dividends | 40,594 action rows and 54,509 dividend rows, no null announcement dates | reconstruct with conservative end-of-announcement-date rule, never 2026 `built_at` |
| Full financial statements | 14,976 envelopes decode to exactly 852,497 reports; every report has valid publish/update time | use actual publication date; exclude 86 report versions dated 2026-08-01 from V2.1 into Daily cleaning |
| Quarterly/abstract financial sources | 233,073 of 243,164 canonical quarterly coordinates and 287,488 of 307,328 abstract rows match an actual-publish source | 10,091 quarterly and 19,840 abstract unmatched coordinates need additive mapping or fail closed |
| Industry membership | 4,992-row snapshot, market/acquisition date 2026-07-31 | forward-only from acquisition |
| F10 capital/holdings | 79,872 rows in 17 categories, acquired on 2026-07-31 | forward-only from acquisition |
| Shareholder count | 411,318 rows; no null notice or cutoff | source timing sufficient |
| Holder changes | 194,322 rows; 69,648 have disclosure date and 124,674 do not | disclosed subset reconstructable; remainder needs mapping or fail closed |
| Quality and gap evidence | actual Base detection time | forward-only governance evidence |

ODS Base V2 remains immutable. The CN01 package is sufficient to freeze these source-content
findings; Hermes still must prove checksum-identical package identity. A successor DWD build may
read the exact ODS Base plus separately sealed additive evidence only where the table-specific
rules above identify a genuine gap.

The accepted successor boundary is a new `dwd-base-202607-v2.1` through exactly 2026-07-31. Daily
source and DWD packages after that date are excluded from the Base and handled through a separate
clean/rebuild/rebase migration.

## 5. Downstream And Increment Impact

| Consumer/path | Dependency | Current disposition |
| --- | --- | --- |
| P4 Stage B / PR #77 | direct use of security, listing, calendar, bars, tradability, price-limit, and factor objects | blocked; no historical scan and no timestamp substitution |
| `build_daily_domain_increment` | reads Base identity, calendar, and index references; emits seven current-date tables | operational collection may continue as retained evidence, but post-2026-07-31 data is excluded from V2.1 and must be cleaned and rebuilt separately |
| DWD weekly corrections | planned reuse of the same domain builders | still disabled; must not activate on superseded semantics |
| Hermes/CN01 node-local DWD | exact Base identity plus immutable daily packages | continue immutable evidence retention; no Base rewrite |
| OCI DWD mirror | verified CN01 immutable packages | no transform or repair on OCI; later successor mirror requires exact checksum equality |
| Generic DWS | currently demand-gated and deferred | not activated by this issue |
| Legacy DWS/ADS/research bundles | retained historical packages and old strategy-shaped outputs | forbidden as timestamp evidence, rebuild input, or P4 fallback |
| Future consumers | manifest-bound DWD readers | must declare whether they need historical PIT authority and bind the successor package explicitly |

A successor Base changes package identity and may change parent row identities/knowledge intervals.
Therefore daily increments after 2026-07-31 cannot be assumed portable; they require deterministic
replay or explicit retirement under an approved cutover plan.

## 6. Implemented Investigation Assets

| Asset | Purpose |
| --- | --- |
| `config/audits/dwd-pit-semantics.v1.json` | exact 33-table code/source classification, reference fields, ODS sufficiency, and remediation notes |
| `config/audits/ods-pit-sufficiency.v1.json` | 18-source-family ODS evidence matrix, 21 physical checks, CN01 exact-package statistics, remaining gaps, and the accepted V2.1/Daily boundary |
| `src/quanthus/pipelines/dwd_pit_audit.py` | read-only DWD package verification, mirror comparison, and table-distribution scanner |
| `src/quanthus/pipelines/ods_pit_audit.py` | read-only Hermes/CN01 ODS package verification and required Parquet/SQLite source-field scanner |
| `ops/bin/audit-dwd-pit-semantics` | CLI for manifest-only and full three-mirror DWD evidence |
| `ops/bin/audit-ods-pit-sufficiency` | CLI for manifest-only and full two-mirror ODS source evidence |
| `docs/operations/dwd-base-pit-audit.md` | DWD permission, execution, interpretation, and failure runbook |
| `docs/operations/ods-base-pit-sufficiency-audit.md` | ODS package/source audit and post-Base Daily boundary runbook |
| `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` | proposed immutable successor-Base remediation; not yet approved |
| `tests/unit/test_dwd_pit_audit.py` | policy completeness, file mapping, output isolation, access failure, and future-knowledge scan tests |
| `tests/unit/test_ods_pit_sufficiency_policy.py` | all-33-table source coverage, CN01/Hermes authority boundary, compound-dataset path selection, and V2.1/Daily decision tests |

The audit implementation reuses `verify_domain_base`, which verifies the READY/manifest binding,
all declared file sizes and SHA-256 values, exact physical inventory, exactly 33 row-count entries,
and payload totals before any Parquet query. Full mode scans every table and records the exact
manifest-declared Parquet paths accessed. Output paths inside a supplied mirror are rejected.

## 7. Verification

- Focused DWD/ODS audit tests: 12 passed.
- Focused Ruff checks: passed.
- Strict mypy for the audit implementation: passed.
- Real OCI manifest-only permission probe: failed closed with exit code 2 as expected.
- No sealed Base, READY marker, manifest, DWD increment, ODS package, DWS/ADS package, or consumer
  pointer was modified.
- No DWD Parquet file was read during the current investigation.
- CN01 ODS V2 deep verification passed all 21,396 declared package files and 13,643,600,642 bytes
  with exit status 0. Focused exact-source scans covered the listed Parquet/SQLite families.
- Full financial decoding reconciled all 14,976 envelopes to 852,497 report versions, with no
  missing/invalid publish date or update time; 86 post-cutoff versions were identified explicitly.
- Temporary CN01 root authorization and `/tmp` audit outputs were removed after the audit.
- Repository `make check` passed environment doctor, compilation, 897 tests, and GitHub planning
  synchronization after the CN01 evidence update.

GitHub publication is recorded in the Issue #78 implementation change after branch review.

## 8. Remaining Closure Gates

Issue #78 remains open until:

1. approved read-only access permits the Hermes/CN01/OCI physical DWD audit;
2. full mode completes all 33 DWD table scans on all three mirrors with matching package identities
   and manifest row counts;
3. the Hermes ODS V2 mirror is confirmed checksum-identical to the deep-verified CN01 package;
4. identity, observation-completion, valuation, benchmark, financial, and holder-change rules are
   frozen, with every uncovered row explicitly fail-closed or bound to additive immutable evidence;
5. the proposed ADR is explicitly approved;
6. corrected availability contracts and the final V2.1 semantic digest receive human approval;
7. the separate post-2026-07-31 Daily cleaning/rebuild/rebase plan is accepted; and
8. downstream cutover, rollback, and P4 reapproval plans are accepted.
