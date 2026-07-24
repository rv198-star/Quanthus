# PIT Security Code-Validity Normalization

- Status: Implemented OCI child revision; not yet promoted
- Tracking issue: [#36](https://github.com/rv198-star/Quanthus/issues/36)
- Mapping: `config/security-code-validity.v1.json`
- OCI mapping revision: `security_code_validity_v1_oci_20260725`
- OCI candidate child: `st_status_pit_v2_baostock_code_validity_v1_1_20260725`

## Problem

The provider security master assigned a distinct identity to a successor code while copying the
predecessor's original listing date. Baostock then returned predecessor history under both codes.
Using `stock_code` alone could therefore yield two positions, events, or universe rows for one
economic security on the same market date.

## Contract

`security_id` is the economic identity. `stock_code` is a listing attribute valid only within an
inclusive `valid_from` / `valid_to` interval. The frozen mapping records the exchange, source,
evidence locator and SHA-256, source revision, and a point-in-time `knowledge_cutoff` per interval.
The loader rejects overlapping intervals both for an economic identity and for a reused code.

The initial mappings are:

| Economic identity | Old code through | Successor from | Official evidence |
| --- | --- | --- | --- |
| `SZSE-000022` | `000022` through 2018-12-26 | `001872` from 2018-12-27 | CNInfo `1205690369` |
| `SZSE-000043` | `000043` through 2019-12-16 | `001914` from 2019-12-17 | CNInfo `1207164397` |
| `SZSE-300114` | `300114` through 2025-02-17 | `302132` from 2025-02-18 | CNInfo `1222544408` |

The original public PDFs are archived outside Git in
`/home/ubuntu/data/astock/revisions/security_code_validity_evidence_v1_oci_20260725/`; their
checksums are locked in the mapping. Git holds only the contract and evidence locators.

## Build

1. `quanthus build-code-validity` overlays the evidenced aliases on the immutable provider master
   and writes `ref_security_code_validity.parquet` as a new revision.
2. `quanthus normalize-security-identity` crops raw `none` and S2 daily rows to those intervals,
   adds the economic `security_id`, produces a date-level universe, and clips the already-audited
   S2 intervals without refetching any raw shard.
3. The builder fails closed if any normalized daily table would contain a duplicate
   `(security_id, market_date)`, if alias intervals overlap, or if a S2 interval cannot account for
   every normalized S2 daily row exactly once.

The current child revision contains 13,802,529 normalized `none` rows, 13,800,992 daily universe
rows, 13,806,154 normalized S2 rows, and 7,449 S2 intervals. Its full audit found zero duplicate
economic-date keys, zero S2 interval overlap, zero uncovered S2 rows, and zero S2 state mismatches.
The former three successor-code calendar gaps disappeared; the remaining 39 gaps exactly match the
existing terminal-boundary ledger, leaving zero unexplained calendar gaps.

`st_status_pit_v2_baostock_code_validity_v1_20260725` is a non-promoted draft created while the
interval compressor was too granular. It has no pointer, manifest, PG publication, or gate effect.
Only the `v1_1` child above is a candidate for further acceptance work.

## Promotion Boundary

This normalization neither changes raw Hermes evidence nor mutates existing Parquet, PostgreSQL,
`CURRENT`, or P1 gates. Before promotion, OCI must use `quanthus build-identity-coverage` to rerun
the S1/C2 evidence from the normalized `daily_universe`, then publish a complete versioned snapshot
and rebuild P1 acceptance evidence. The mapping itself remains
reusable for future DWD, DWS, event, position, and attribution builds.
