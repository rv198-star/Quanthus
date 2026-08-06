# DWD Base V2 Historical PIT Investigation Evidence

| Field | Value |
| --- | --- |
| Evidence status | blocked finding; code audit complete, physical three-mirror audit incomplete |
| Evidence date | 2026-08-06 |
| Governing work | `META-DATA-PIT-001` / GitHub Issue #78 |
| Investigated package | `dwd-base-202607-v2` |
| Manifest SHA-256 | `sha256:82dfe29400a6b4dbc901e5d2d999ebcbb6887dc4af46a082f87e7acc74a4ee9f` |
| Content digest | `sha256:5a183ca5790d9384ec0256137186a7d74fd1df52f2b5e15dbe1f6c998d106fc9` |
| Audit policy | `config/audits/dwd-pit-semantics.v1.json` |
| Audit policy SHA-256 | `sha256:f7327a86f3a60bdd7495a8b182b650949f86f024455df6b73ee3a20ec6e680ac` |
| Proposed remediation | `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` |
| Consequence | V2 historical PIT use remains blocked; no Base or consumer pointer changed |

## 1. Investigation Decision

The Stage B P4 blocker is a package-wide DWD authority issue, not a P4 reader defect. Code inspection
confirms that 17 of 33 tables substitute a Base build/acquisition timestamp for historical
availability, three financial tables mix source families whose historical availability cannot be
resolved by one rule, six tables already use source-derived historical timestamps, and seven tables
are correctly forward-only operational/governance objects.

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

The missing physical evidence is explicit:

- READY/manifest binding and every declared file SHA-256 on all three installed copies;
- cross-mirror manifest/content equality;
- actual table schemas and row counts;
- minimum/maximum and year distributions of `knowledge_from`;
- representative 2012-2021 rows whose knowledge is in 2026; and
- exact physical source rows needed to confirm ODS sufficiency.

## 3. Complete 33-Table Code And Source Matrix

`Physical evidence` is `pending` for every row until the read-only three-mirror scan completes.
The exact evidence statements and remediation notes are machine-readable in the audit policy.

| Table | Code disposition | Historical PIT authority | ODS sufficiency | Physical evidence |
| --- | --- | --- | --- | --- |
| `dwd_listed_company` | build timestamp substituted | blocked | additive identity evidence or fail closed | pending |
| `dwd_company_identifier` | build timestamp substituted | blocked | additive identifier evidence or fail closed | pending |
| `dwd_security` | build timestamp substituted | blocked | additive identity evidence or fail closed | pending |
| `dwd_security_identifier` | build timestamp substituted | blocked | additive code-availability evidence or fail closed | pending |
| `dwd_listing` | build timestamp substituted | blocked | additive exchange lifecycle evidence or fail closed | pending |
| `dwd_trading_venue` | build timestamp substituted | blocked | versioned reference evidence or fail closed | pending |
| `dwd_security_bar` | build timestamp substituted | blocked | reconstructable from completed observation interval under a new contract | pending |
| `dwd_security_valuation` | build timestamp substituted | blocked | provider-availability evidence required or fail closed | pending |
| `dwd_adjustment_factor` | source-derived historical | conditional on corrected parent identity | existing ODS knowledge cutoff is sufficient | pending |
| `dwd_adjustment_factor_input` | source-derived historical | conditional on corrected parent identity | existing ODS knowledge cutoff is sufficient | pending |
| `dwd_market_index` | build timestamp substituted | blocked | versioned benchmark evidence or fail closed | pending |
| `dwd_index_bar` | build timestamp substituted | blocked | reconstructable from completed observation interval under a new contract | pending |
| `dwd_trading_calendar` | build timestamp substituted | blocked | reconstructable under a reviewed conservative session rule | pending |
| `dwd_security_status_interval` | build timestamp substituted | blocked | ST source cutoff and daily observation date are available | pending |
| `dwd_listing_status_interval` | build timestamp substituted | blocked | suspensions reconstructable; lifecycle depends on corrected identity | pending |
| `dwd_price_limit_regime` | build timestamp substituted | blocked | source knowledge cutoff is available | pending |
| `dwd_tradability_state` | build timestamp substituted | blocked | recomputable from corrected inputs and observation completion | pending |
| `dwd_observation_gap` | forward operational | not historical authority | correctly retained from detection time | pending |
| `dwd_disclosure` | source-derived historical | conditional on corrected parent identity | provider publication time is available | pending |
| `dwd_disclosure_subject` | source-derived historical | conditional on corrected parent dependencies | provider publication time is available | pending |
| `dwd_corporate_action` | build/acquisition timestamp substituted | blocked | announcement evidence partial; missing rows fail closed | pending |
| `dwd_corporate_action_term` | build/acquisition timestamp substituted | blocked | inherits corrected action timing | pending |
| `dwd_financial_report` | source-family ambiguity | blocked pending classification | partially reconstructable; remainder fails closed | pending |
| `dwd_financial_concept` | source-family ambiguity | blocked pending classification | earliest defensible published use required | pending |
| `dwd_financial_fact` | source-family ambiguity | blocked pending classification | partially reconstructable; remainder fails closed | pending |
| `dwd_industry_scheme` | forward operational | not historical authority | current evidenced scheme only | pending |
| `dwd_industry_node` | forward operational | not historical authority | current evidenced hierarchy only | pending |
| `dwd_company_industry_membership` | forward operational | not historical authority | first defensible snapshot only | pending |
| `dwd_share_capital_observation` | forward operational | not historical authority | acquired F10 snapshot only | pending |
| `dwd_shareholder_count_observation` | source-derived historical | conditional on corrected parent identity | source knowledge/notice date is available | pending |
| `dwd_shareholding_change` | source-derived historical | conditional on corrected parent identity | disclosure/change date is available | pending |
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
- `financial_reporting.py` uses `acquired_at` or a Base-cutoff fallback while retaining
  `published_at` only for some source families. The table family requires source-specific rules.
- `industry_classification.py`, F10 ownership parsing, and quality governance intentionally preserve
  first acquisition/detection time and must remain forward-only.
- shareholder counts and disclosed holder changes retain source-derived historical timestamps.

## 4. ODS Impact Assessment

| Source family | Existing evidence | Investigation disposition |
| --- | --- | --- |
| Security/company/listing identity | effective list/delist and code-validity intervals; current maps | business time is insufficient; add versioned source-availability evidence or fail closed |
| Daily/minute security bars | observation date/interval and immutable source bytes | sufficient for a conservative observation-completion contract, subject to approval |
| Daily valuation | trade date and immutable source bytes | exact availability is not accepted; add evidence or exclude affected history |
| Benchmark definitions | current metadata and effective range | add versioned reference evidence or fail closed before its evidenced version |
| Index bars | observation date/interval and immutable source bytes | sufficient for a conservative observation-completion contract, subject to approval |
| Trading calendar | session dates, open/closed state, source `built_at` | table-specific conservative availability rule required, including closed sessions |
| ST and daily provider state | ST `knowledge_cutoff`; daily session observation | sufficient after identity correction and a reviewed daily-observation rule |
| Suspensions | source `knowledge_cutoff` | sufficient for suspension rows; listing lifecycle still depends on identity evidence |
| Price-limit regimes | source `knowledge_cutoff` and announcement date | sufficient after identity correction |
| Adjustment factors | source `knowledge_cutoff` | sufficient after identity correction |
| Announcements | provider `publish_time` | sufficient after company/security relationship rebuild |
| Corporate actions/dividends | `announce_date`, `effective/ex` dates, and `built_at` | partial; use announcement evidence where present and fail closed otherwise |
| Full financial statements | provider publish date for many envelopes plus acquisition time | source-family audit required; publication evidence may be usable where present |
| Quarterly/abstract financial sources | period end, deadlines, mixed acquisition/publication metadata | no period-end/deadline inference; missing publication evidence fails closed |
| Industry membership | first provider snapshot and acquisition time | forward-only; no reconstructed historical membership |
| F10 capital/holdings | reported observation date and acquisition time | forward-only without archived publication evidence |
| Shareholder count | source knowledge cutoff/notice date | sufficient after identity correction |
| Holder changes | disclosure/change date | sufficient after identity correction |
| Quality and gap evidence | Base detection time | forward-only governance evidence |

The current conclusion is that ODS Base V2 should not be modified. A successor DWD build may read
that exact ODS package plus separately sealed additive evidence packages.

## 5. Downstream And Increment Impact

| Consumer/path | Dependency | Current disposition |
| --- | --- | --- |
| P4 Stage B / PR #77 | direct use of security, listing, calendar, bars, tradability, price-limit, and factor objects | blocked; no historical scan and no timestamp substitution |
| `build_daily_domain_increment` | reads Base identity, calendar, and index references; emits seven current-date tables | operational collection may continue, but output remains V2-lineage-bound and must be replayed/rebased before successor cutover |
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
| `src/quanthus/pipelines/dwd_pit_audit.py` | read-only package verification, mirror comparison, and table-distribution scanner |
| `ops/bin/audit-dwd-pit-semantics` | CLI for manifest-only and full three-mirror evidence |
| `docs/operations/dwd-base-pit-audit.md` | permission, execution, interpretation, and failure runbook |
| `docs/architecture/adr-0008-dwd-historical-knowledge-remediation.md` | proposed immutable successor-Base remediation; not yet approved |
| `tests/unit/test_dwd_pit_audit.py` | policy completeness, file mapping, output isolation, access failure, and future-knowledge scan tests |

The audit implementation reuses `verify_domain_base`, which verifies the READY/manifest binding,
all declared file sizes and SHA-256 values, exact physical inventory, exactly 33 row-count entries,
and payload totals before any Parquet query. Full mode scans every table and records the exact
manifest-declared Parquet paths accessed. Output paths inside a supplied mirror are rejected.

## 7. Verification

- Focused audit tests: 6 passed.
- Focused Ruff checks: passed.
- Strict mypy for the audit implementation: passed.
- Real OCI manifest-only permission probe: failed closed with exit code 2 as expected.
- No sealed Base, READY marker, manifest, DWD increment, ODS package, DWS/ADS package, or consumer
  pointer was modified.
- No DWD Parquet file or market-data partition was read during the current investigation.

- Repository `make check` passed environment doctor, compilation, 891 tests, and GitHub planning
  synchronization.

GitHub publication is recorded in the Issue #78 implementation change after branch review.

## 8. Remaining Closure Gates

Issue #78 remains open until:

1. the audit principal receives approved read-only access to the three physical package roots;
2. manifest-only verification succeeds for Hermes, CN01, and OCI;
3. full mode completes all 33 table scans on all three mirrors with matching package identities and
   manifest row counts;
4. physical ODS schemas/rows confirm or narrow every sufficiency disposition;
5. the proposed ADR is revised with actual evidence and explicitly approved;
6. corrected availability contracts and successor package identity receive human approval; and
7. downstream replay, cutover, rollback, and P4 reapproval plans are accepted.
