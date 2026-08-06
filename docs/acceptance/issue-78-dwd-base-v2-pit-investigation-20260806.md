# DWD Base V2 Historical PIT Investigation Evidence

| Field | Value |
| --- | --- |
| Evidence status | blocked finding; CN01 is the sole canonical audit source; Phase B complete with seven affected tables; Phase C-D and V2.1 acceptance incomplete |
| Evidence date | 2026-08-06 |
| Governing work | `META-DATA-PIT-001` / GitHub Issue #78 |
| Investigated package | `dwd-base-202607-v2` |
| Manifest SHA-256 | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` |
| Content digest | `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9` |
| DWD PIT audit policy | `config/audits/dwd-pit-semantics.v1.json` / `sha256:b312afa9b8f6125581235c28ec82d192a38b9ce242472aa6b04dd8c25ad04ccd` |
| Full pre-rebuild audit policy | `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json` / `sha256:c2394fae5f8e46021865874aa1a309c5669e31cf851b14d292a8e56d57de9e56` |
| ODS sufficiency policy | `config/audits/ods-pit-sufficiency.v1.json` / `sha256:170f4557c43202e82c6951f6a1516b2fee228b38b4b94766495ac1a232fafa4d` |
| Successor boundary | new `dwd-base-202607-v2.1`, through 2026-07-31; CN01-only NFS build; later Daily excluded and cleaned separately |
| Proposed remediation | `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` |
| Consequence | V2 historical PIT use remains blocked; no Base or consumer pointer changed |

## 1. Investigation Decision

The Stage B P4 blocker is a package-wide DWD authority issue, not a P4 reader defect. Code inspection
confirms that 17 of 33 tables substitute a Base build/acquisition timestamp for historical
availability, three tables have source precision or row-level ambiguity, three tables already use
defensible source-derived historical timestamps, and ten tables are forward-only from immutable
acquisition/detection. The ambiguous objects are `dwd_disclosure`, `dwd_disclosure_subject`, and
`dwd_shareholding_change`: announcement time precision is mixed, while 124,674 of 194,322 holder
changes lack `disclosure_date`. The three financial tables move to forward-only because ODS V2
contains current values without immutable report-revision history.

This evidence does not close Issue #78. On 2026-08-06, the project explicitly expanded the Issue
from a PIT-focused investigation into a full DWD Base V2 pre-rebuild audit and one-time remediation
window. The expensive V2.1 reconstruction may start only after all 33 tables and physical
partitions complete package/schema, field-level PIT, identity, referential, key/version, temporal,
coverage, numeric, lineage, deterministic replay, downstream compatibility, and rebuild-readiness
audits; every finding must have a measured impact and approved disposition, and the exact findings,
remediation, builder-contract, and semantic digests must be approved.

### 1.1 Canonical-Node Correction

The pre-rebuild method now treats CN01 as the only authoritative DWD data node. All 13 quality
dimensions execute once against the installed CN01 package. Hermes and OCI are byte-copy targets;
they do not contribute independent semantics, findings, or remediation scope. Their only required
role is checksum verification after the accepted CN01 successor is copied. Earlier OCI/Hermes
reads remain supplementary copy-equality evidence and are not pre-rebuild closure gates.

Phase B revision `5a14e30b046ae38487053995310d9b53363a1f83` audited all 76,671 CN01
Parquet parts. Seven tables have physical-contract findings: four ownership tables use generic
`year=` paths instead of their model coordinates; `dwd_quality_exception` uses `detected_year=` and
has a field-order mismatch; and `dwd_tradability_state` plus `dwd_observation_gap` physically store
derived partition columns absent from the table-column contract. The canonical Phase B semantic
digest is `sha256:ae3513f43d7eff991e5294e9e867243bb484823bdbb9397e2b8f4a246d32651d`.

CN01 package bytes have now been fully read and independently verified. An earlier supplementary
OCI copy comparison exposed an audit-tool timezone dependency; the reviewed UTC fix and reruns
closed that tool defect. No Hermes or OCI scan is required for the pre-rebuild audit. The sealed V2
package remains blocked for historical PIT consumers. No V2.1 builder,
candidate assembly, timestamp override, legacy fallback, Base mutation, package republish, or
pointer movement is authorized.

## 2. Canonical Inventory And Supplementary Copy Evidence

ADR 0007 assigns node-local copies to Hermes and OCI, but CN01 is the authority for this audit.
The table below records earlier copy evidence for history; only the CN01 row governs findings.

| Mirror | Expected role | Current evidence | Physical result |
| --- | --- | --- | --- |
| Hermes | Node-local immutable Base used by the Hermes DWD builder | Deployment configuration names `dwd-base-202607-v2` | Pending; no DWD physical-audit authority was supplied to this run |
| CN01 | Node-local immutable Base and source of OCI's current DWD mirror | Exact installed root and matching `BASE_CURRENT.json` read as root | Deep verify passed; all 33 tables scanned; 468,268,180 rows match the manifest |
| OCI | Read-only verified DWD mirror | Exact installed root is `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2` | Deep verify passed; all 33 tables scanned; package matches CN01; `BASE_CURRENT.json` is absent |

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

The Phase A full scans then used revision
`551de1031a02bc9a6d9fdbce2b135c1b03b71793` from independent OCI and CN01 worktrees. Both exact
package roots passed READY/manifest binding, physical inventory, declared member size/SHA-256,
package identity, and payload verification: 76,722 manifest members, 76,671 Parquet payload files,
33 tables, 468,268,180 rows, and 14,842,590,066 payload bytes. Both scans opened all 76,671 declared
Parquet files, reported no manifest row-count mismatch, and recorded `mutation_performed=false`.
The node-local report digests are:

- CN01: `sha256:09c44aad118858fc4148aa2b5ad9330466ac0b6f5496188c4d9c568e7bbbd4b3`;
- OCI: `sha256:084e091906fef9eddfbf17056fb1a6bc48b7ee22ed9568f2539221201772fa4d`.

The matching manifest plus two independent full member-SHA verifications prove CN01/OCI declared
package-byte equality. The 16,384-byte difference in `du -sb` is directory metadata and is not a
payload difference. CN01's local `/dev/vda3` package root is `root:root` and its descriptor matches
V2. OCI's `/dev/sda1` mirror is `ubuntu:ubuntu`; the package is complete, but
`/srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json` is absent.

The first table-profile comparison is not acceptable as deterministic semantic evidence. DuckDB
inherited `Asia/Shanghai` on CN01 and UTC on OCI. All 33 profiles rendered identical instants with
different offsets, and five tables also returned different date-window/year counts because the
audit used node-local `CAST(TIMESTAMPTZ AS DATE)` and `year()` semantics:
`dwd_disclosure`, `dwd_disclosure_subject`, `dwd_share_capital_observation`,
`dwd_shareholding_change`, and `dwd_trading_calendar`. This is finding
`DWDV2-PREAUDIT-A-001` (high). The audit connection is now pinned to UTC with an
`Asia/Shanghai` boundary regression test. Commit
`3bb776105b843cbb5691bda58515d38975697892` was distributed through GitHub, and independent CN01
and OCI full reruns produced exactly equal, unnormalized table-profile objects for all 33 tables.
Their report digests are respectively
`sha256:f3f7fb510d71edf98fb1da4be9c75712fffdfee3db8d992bcae8db447bf8c884` and
`sha256:d7a5879f1dfe766d56d0bdf19bf5d534e8c26870aa143a0eb42d42d61900da95`.
`DWDV2-PREAUDIT-A-001` is therefore resolved for Phase A audit evidence. The absent OCI descriptor
is finding `DWDV2-PREAUDIT-A-002` (medium); successor mirror publication must freeze and satisfy its
exact descriptor contract before cutover.

The Phase A evidence files are retained outside every sealed package under
`/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1/pre-rebuild-audit/phase-a-20260806`.
The canonical content digests for `mirror-inventory.json`, `mirror-comparison.json`, and
`phase-a-findings.json` are respectively
`sha256:5e7f000478dd01c56acfc98b11faebcebb8db418459dde6aed26771b17e97149`,
`sha256:acb07176d0fe6e4bddd3f7ea307e757aaf30ba40d49358eb1cb8dca462bd8aa3`, and
`sha256:25918b7f123c9800c923b0e86fb7b07aa0d2c9fc307cfe4f58e88bc399fac147`.
The UTC rerun closure is bound separately by
`phase-a-utc-rerun-closure.json` / canonical digest
`sha256:d69db74fdd14ff0ce071735b4e3b4f449099f6400610e4354d8410769bfd67e4`.

CN01 was then audited through temporary, explicitly authorized root access. The exact
`/srv/quanthus/ods/base/ods-base-202607-v2` package passed deep verification with exit status `0`:
112 datasets, 21,396 files, 13,643,600,642 bytes, manifest
`sha256:7f74a7655f3f86982b4e662f55b12aff4fa2f28ee23cf9b5db530f9f2aa0955b`, and content digest
`sha256:c95adef6624d5d904a53fc93b27d1e2d78ed4a908a653661d89c47147380ac01`.
READY and `BASE_CURRENT.json` matched that identity. The run took 378.97 seconds and recorded no
package, service, or pointer mutation. The temporary public key and `/tmp` audit files were removed
afterward.

The V2.1 execution topology was then audited on CN01. Local storage had 37,807,775,744 free bytes;
the installed V2 package occupied 14,913,159,818 bytes, while the prior Base planner required
100,290,327,430 bytes. Local candidate/spill construction is therefore impossible under the
accepted capacity margin. `/personal` NFS reported 11,258,750,132,289,536 free bytes and passed
controlled write, atomic-rename, same-mount hard-link, and CN01-local `flock` probes. The mount is
NFSv3, root-squashed, and `nolock`; the probe directory was removed after verification.

The accepted execution boundary is CN01-only, single-writer reconstruction. Plans, candidates,
DuckDB spill, checkpoints, July-tail cleaning, complete-package assembly, and evidence live under
`/personal/quanthus-cn01-build-scratch/dwd-base-202607-v2.1`. Lock authority, hidden final ingress,
installed packages, and `BASE_CURRENT.json` remain on CN01 local storage. NFS failure blocks the
build and does not permit a local-scratch fallback. The complete NFS candidate is deeply verified,
then copied manifest-by-manifest into a hidden local ingress and deeply verified again before local
atomic installation. No pointer changes during build or installation.

The missing pre-rebuild evidence is explicit and CN01-only:

- partition-level schemas, nullability, types, timezone/decimal/enum domains, and path values;
- row- and field-level PIT availability, identity graph, time-aware foreign keys, keys/versions,
  temporal intervals, and authorized lineage for all 33 tables;
- expected-versus-actual coverage, numeric/domain invariants, and reason-coded exclusions;
- deterministic real-partition replay for every derived builder;
- Daily/weekly/consumer compatibility and one-time remediation impact;
- minimum/maximum and year distributions of `knowledge_from` and representative 2012-2021 future
  knowledge samples.

## 3. Complete 33-Table Code And Source Matrix

CN01 package bytes and manifest row counts are verified for every row below. No replica scan is
needed to complete these table dispositions. The exact evidence statements and remediation notes
are machine-readable in the audit policy.

| Table | Code disposition | Historical PIT authority | ODS sufficiency | Physical evidence |
| --- | --- | --- | --- | --- |
| `dwd_listed_company` | build timestamp substituted | blocked | stable identity reconstructable; historical display attributes partial | pending |
| `dwd_company_identifier` | build timestamp substituted | blocked | organization mapping complete; historical attribute versions partial | pending |
| `dwd_security` | build timestamp substituted | blocked | existing ODS resolves all historical bar/valuation codes | pending |
| `dwd_security_identifier` | build timestamp substituted | blocked | current code set plus eight historical code intervals | pending |
| `dwd_listing` | build timestamp substituted | blocked | stable listing intervals reconstructable under approved identity rule | pending |
| `dwd_trading_venue` | build timestamp substituted | blocked | versioned static venue contract required | pending |
| `dwd_security_bar` | build timestamp substituted | blocked | reconstructable from completed observation interval under a new contract | pending |
| `dwd_security_valuation` | build timestamp substituted | no historical authority | canonical history is forward-only from acquisition; July 31 tail uses exact artifact cutoff | pending |
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
| `dwd_disclosure` | source-time precision ambiguity | blocked until precision classification | 5,413,404 midnight and 205,275 fixed-06:30 timestamps require conservative handling | pending |
| `dwd_disclosure_subject` | parent source-time ambiguity | blocked until parent correction | inherits corrected disclosure availability | pending |
| `dwd_corporate_action` | build/acquisition timestamp substituted | blocked | exact ODS has announcement date for all 40,594 action rows and 54,509 dividend rows | pending |
| `dwd_corporate_action_term` | build/acquisition timestamp substituted | blocked | inherits corrected announcement-date timing | pending |
| `dwd_financial_report` | forward operational | forward-only from envelope acquisition | publication remains descriptive; no immutable revision history | pending |
| `dwd_financial_concept` | forward operational | forward-only from envelope acquisition | current concept/taxonomy snapshot only | pending |
| `dwd_financial_fact` | forward operational | forward-only from envelope acquisition | current value version cannot be assigned to original publication | pending |
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
- `disclosure_event.py` treats all announcement `publish_time` values as exact even though the
  source mixes precise times with 5,413,404 midnight and 205,275 fixed-06:30 values. Factor source
  cutoffs are valid, while corporate actions incorrectly prefer source `built_at` over
  `announce_date`.
- `financial_reporting.py` correctly uses acquisition for the current envelope snapshot, but its
  prose/consumer interpretation must not treat `published_at` as authority for the current fact
  value. All 852,497 reports carry publication dates, while the shared provider update epoch does
  not preserve report-level revision history. Reports, concepts, and facts remain forward-only.
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
| Daily valuation | 11,416,988 rows through 2026-07-18; no historical publication/acquisition field; full-history receipts cover only 209 securities and were acquired after cutoff | no historical PIT authority; forward-only from independently evidenced acquisition |
| Benchmark definitions | current metadata only | static/versioned benchmark contract or additive evidence required |
| Index bars | 23,242 rows through 2026-07-24; no historical fetch time | use session-close rule after benchmark identity approval |
| Trading calendar | 17,055 canonical rows through 2026-07-25 with one 2026 build time; non-conflicting historical Hotfix retained | realized sessions may use conservative session/date end; no earlier future-schedule authority |
| ST and daily provider state | 7,030 rows, all with cutoff; only 1,492 have proven events and 5,538 are explicitly not proven | use proven rows; keep not-proven rows fail closed; daily state uses session completion |
| Suspensions | 2,745 rows, all with cutoff; 543 become known after suspension start; coverage begins in 2016 | use exact cutoff for observed rows; missing/pre-2016 history remains fail closed |
| Price-limit regimes | 2,750 rows, all with announcement and cutoff | source timing sufficient |
| Adjustment factors | 46,649 rows with cutoff: 39,739 action-announcement, 2,737 provider-notice, 4,173 no-provable-announcement exceptions | source timing sufficient; retain all 4,173 fail-closed exceptions |
| Announcements | 6,395,198 rows; 5,413,404 exactly midnight and 205,275 exactly 06:30:00 | classify precision; exact only where proven, otherwise conservative day-end/artifact cutoff |
| Corporate actions/dividends | 40,594 action rows and 54,509 dividend rows, no null announcement dates | reconstruct with conservative end-of-announcement-date rule, never 2026 `built_at` |
| Full financial statements | 14,976 envelopes decode to 852,497 reports; all have publish date, but shared 10-digit update epochs do not identify value revisions | reports/concepts/facts are forward-only from acquisition; 86 versions dated 2026-08-01 are excluded |
| Quarterly/abstract financial sources | in 2012-2021, 139,915 of 144,794 quarterly coordinates and 139,915 of 144,891 abstract rows match the publish-date source | 4,879 quarterly and 4,976 abstract development rows are unmatched; none obtain historical value authority without revision evidence |
| Industry membership | 4,992-row snapshot, market/acquisition date 2026-07-31 | forward-only from acquisition |
| F10 capital/holdings | 79,872 rows in 17 categories, acquired on 2026-07-31 | forward-only from acquisition |
| Shareholder count | 411,318 rows; no null notice or cutoff | source timing sufficient |
| Holder changes | 194,322 rows; 69,648 have disclosure date and 124,674 do not | disclosed subset reconstructable; remainder needs mapping or fail closed |
| Quality and gap evidence | actual Base detection time | forward-only governance evidence |

ODS Base V2 remains immutable. The CN01 package is sufficient to freeze these source-content
findings without a replica audit. A successor DWD build may
read the exact ODS Base plus separately sealed additive evidence only where the table-specific
rules above identify a genuine gap.

The accepted successor boundary is a new `dwd-base-202607-v2.1` through exactly 2026-07-31. Its
July tail must be rebuilt from the manifest-bound ODS releases rather than from V2 DWD rows. Exact
reconciliation passed for 24,960 daily bars, 24,960 status rows, 15 index rows, 4,992 valuation
rows, 31,190 announcements, 21 suspensions, 175 factor rows, 358 dividend rows, 7,179,120 one-minute
bars, and 1,435,824 five-minute bars. Each artifact retains its actual cutoff; some July 27-28
market facts were acquired only on July 29-30. Five corporate-action and five financial-disclosure
parts, plus one factor part, are verified zero-row/zero-column Parquet and must be accepted as empty
increments rather than schema failures. Daily source and DWD packages after 2026-07-31 are excluded
from the Base and handled through a separate clean/rebuild/rebase migration.

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
| `config/audits/dwd-pit-semantics.v1.json` | exact 33-table code/source PIT classification, reference fields, ODS sufficiency, and remediation notes |
| `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json` | hard gate, exact 33-table scope, 13 audit dimensions, finding model, six execution phases, required outputs, and Issue #78/V2.1 closure rules |
| `config/audits/ods-pit-sufficiency.v1.json` | 18-source-family ODS evidence matrix, 21 physical checks, CN01 exact-package statistics, accepted V2.1/Daily boundary, and CN01-only NFS rebuild topology |
| `src/quanthus/pipelines/dwd_pit_audit.py` | read-only DWD package verification, mirror comparison, and table-distribution scanner |
| `src/quanthus/pipelines/ods_pit_audit.py` | read-only Hermes/CN01 ODS package verification and required Parquet/SQLite source-field scanner |
| `ops/bin/audit-dwd-pit-semantics` | CLI for manifest-only and full canonical DWD evidence; legacy replica comparison is supplementary only |
| `ops/bin/audit-ods-pit-sufficiency` | CLI for manifest-only and full canonical ODS source evidence |
| `docs/operations/dwd-base-pit-audit.md` | DWD PIT permission, execution, interpretation, and failure runbook |
| `docs/operations/dwd-base-v2-pre-rebuild-audit.md` | full physical/semantic/structural/replay audit and one-time remediation-freeze runbook |
| `docs/operations/ods-base-pit-sufficiency-audit.md` | ODS package/source audit and post-Base Daily boundary runbook |
| `docs/operations/dwd-v2-1-cn01-nfs-rebuild.md` | CN01-only NFS build, capacity, cross-filesystem installation, cutover, and rollback runbook |
| `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` | proposed immutable successor-Base remediation; not yet approved |
| `tests/unit/test_dwd_pit_audit.py` | PIT policy completeness, file mapping, output isolation, access failure, and future-knowledge scan tests |
| `tests/unit/test_dwd_base_pre_rebuild_audit_policy.py` | exact 33-table full-audit scope, hard builder gate, required dimensions/outputs, finding classification, and Issue #78 closure tests |
| `tests/unit/test_ods_pit_sufficiency_policy.py` | all-33-table source coverage, CN01/Hermes authority boundary, compound-dataset path selection, and V2.1/Daily decision tests |

The audit implementation reuses `verify_domain_base`, which verifies the READY/manifest binding,
all declared file sizes and SHA-256 values, exact physical inventory, exactly 33 row-count entries,
and payload totals before any Parquet query. Full mode scans every table and records the exact
manifest-declared Parquet paths accessed. Output paths inside a supplied mirror are rejected.

## 7. Verification

- Focused DWD audit and pre-rebuild policy tests after the UTC fix: 11 passed.
- Focused Ruff checks: passed.
- Strict mypy for the audit implementation: passed.
- Real OCI manifest-only permission probe: failed closed with exit code 2 as expected.
- No sealed Base, READY marker, manifest, DWD increment, ODS package, DWS/ADS package, or consumer
  pointer was modified.
- CN01 and OCI each read and scanned all 76,671 manifest-declared DWD Parquet files; the reports
  recorded no mutation and no manifest row-count mismatch.
- CN01 ODS V2 deep verification passed all 21,396 declared package files and 13,643,600,642 bytes
  with exit status 0. Focused exact-source scans covered the listed Parquet/SQLite families.
- Full financial decoding reconciled all 14,976 envelopes to 852,497 report versions. Publication
  dates were present, but the shared 10-digit provider update epochs proved insufficient for
  report-level value revision history; 86 post-cutoff versions were identified explicitly.
- Announcement precision audit classified 5,413,404 midnight and 205,275 fixed-06:30 source times.
- July-tail row counts reconciled exactly to every audited manifest part, including verified
  zero-row/zero-column empty increments.
- CN01 storage preflight proved local reconstruction capacity insufficient and `/personal` NFS
  mandatory; NFS write, atomic rename, same-mount hard link, and CN01-local lock probes passed and
  left no persistent probe artifact.
- Phase A CN01/OCI physical inventory and UTC-pinned table profiles were executed and proved copy
  equality. Under the corrected canonical-node method, this is supplementary evidence; only CN01
  governs the pre-rebuild audit and no replica descriptor blocks Phase C-D.
- Repository `make check` passed environment doctor, compilation, 903 tests, and GitHub planning
  synchronization after the full pre-rebuild audit scope was frozen.

The Phase A result and exact evidence digests were published to Issue #78 as
`issuecomment-5202648389`. Publication does not accept the findings or open any gate.

## 8. Remaining Closure Gates

Issue #78 remains open until:

1. the complete policy in `config/audits/dwd-base-v2-pre-rebuild-audit.v1.json` executes on CN01 across all
   33 tables and all required audit dimensions;
2. every CN01 manifest part has a physical/schema disposition;
3. every PIT/field, identity, relationship, key/version, temporal, coverage, numeric, lineage,
   deterministic replay, downstream, and rebuild-readiness finding is measured and classified;
4. no critical finding or unknown disposition remains, and every fail-closed exclusion and repair
   has a reason, count, regression test, and V2.1 acceptance check;
5. the exact findings ledger, one-time remediation scope, builder contracts, and semantic digests
   receive human approval before the V2.1 builder gate opens;
6. the proposed ADR and table/source availability contracts are explicitly approved;
7. the CN01 partition pilots freeze builder memory/spill bounds and the full NFS build completes;
8. the complete 33-table NFS candidate and copied local ingress both pass deep and finding-closure
    verification;
9. CN01 installs V2.1, any optional Hermes/OCI copies actually deployed are checksum-identical, and consumer cutover/
    rollback are accepted; and
10. the post-2026-07-31 Daily migration may remain separately executed, but its compatibility and
    migration disposition are frozen under Issue #78.
