# DWD Base V2.1 r11 Post-build Audit

## Scope

This packet closes the read-only candidate audit for:

- run: `dwd-v2-1-gpu-p100-20260810T063544Z-c010c277-r11`
- revision: `c010c2772ac54938042507f7f1d0296fcd4dadba`
- candidate: `dwd-base-202607-v2.1`
- rows: `468268180`
- tables: `33`

The sealed candidate, ODS Base, DWD Base V2, install location, and current pointer were not
mutated. The standard full-build path stopped at `11b-semantic-contract-audit`. The remaining
read-only audits were run independently under
`evidence/post-failure-audit-continuation-20260810` so that the failed run state and standard
gate evidence remained unchanged.

## Gate Matrix

| Gate | Status | Main result |
| --- | --- | --- |
| 11a schema and partitions | verified | 33 tables verified |
| 11b semantic contracts | finding | 11 finding tables |
| 11c domain invariants | finding | 1 finding table |
| 11d coverage and exclusions | finding | 6 finding tables |
| 11e lineage authority | verified | exact ODS, plan, model, and code authority |

## Findings

### A. Missing knowledge-time propagation

All ordinary foreign-key orphan counts are zero. The systematic failures occur because child
rows preserve source acquisition or publication time without taking the maximum knowledge time
of the resolved parent identity.

| Child relation | Non-null rows | Knowledge failures | Effective failures | Temporal failures |
| --- | ---: | ---: | ---: | ---: |
| adjustment factor -> security | 139947 | 139947 | 0 | 139947 |
| corporate action -> company | 94797 | 94797 | 8 | 94797 |
| corporate action -> security | 94797 | 94797 | 8 | 94797 |
| disclosure -> company | 6489940 | 6489940 | 83894 | 6489940 |
| disclosure subject -> security | 6395143 | 6395143 | 83889 | 6395143 |
| disclosure subject -> financial report | 2430409 | 2430409 | 0 | 2430409 |
| financial report -> company | 1749480 | 1198988 | 251168 | 1288266 |
| share capital -> security | 207064 | 207064 | 10884 | 207064 |
| shareholder count -> company | 411318 | 411318 | 20791 | 411318 |
| shareholder count -> security | 411318 | 411318 | 20791 | 411318 |
| shareholding change -> company | 192773 | 192773 | 5 | 192773 |
| shareholding change -> security | 192773 | 192773 | 5 | 192773 |
| shareholding observation -> company | 390627 | 390627 | 782 | 390627 |
| shareholding observation -> security | 390627 | 390627 | 782 | 390627 |

The builder must materialize child knowledge as the maximum of source knowledge, every required
parent knowledge coordinate, and the applicable build cutoff. Rows outside the target parent's
effective interval must be rejected from the core table or backed by authoritative parent
history; they must not be bridged to a future identity.

### B. Financial concept effective interval

All `89622402` financial facts pass the concept knowledge-time check and fail only the concept
effective-interval check. Concept `effective_from` currently uses acquisition time
(`2026-07-31` through `2026-08-04`), while fact periods span `1989-12-31` through `2026-06-30`.
Concept validity must use authoritative taxonomy validity, or a deterministic earliest referenced
period when the provider has no validity field. Acquisition time remains `knowledge_from` and
must not be reused as business effective time.

### C. Domain invariant

`dwd_financial_fact` has `39892294` rows with both `value_decimal` and `value_text`. A successfully
parsed numeric value must populate only `value_decimal`; `value_text` is for non-numeric values.
The immutable ODS source continues to preserve the raw representation.

### D. Coverage cutoff

The following rows have a business date after the package through-date `2026-07-31`:

| Table | Rows after through-date |
| --- | ---: |
| dwd_corporate_action | 3 |
| dwd_corporate_action_term | 9 |
| dwd_disclosure | 3 |
| dwd_disclosure_subject | 3 |
| dwd_financial_concept | 33 |
| dwd_share_capital_observation | 285 |

Builders must filter or explicitly exclude these records using the business timezone date. The
coverage auditor currently compares a UTC-rendered date substring, so it also needs a timezone
boundary test for local `2026-08-01` values represented as UTC `2026-07-31`.

### E. Ownership key and governed source anomalies

- Seven duplicate key groups contain 14 distinct same-name holder rows. They differ by
  `holder_rank`, holding quantity, and holding percentage. The business key and row key must add
  the already-modeled `holder_rank`; deduplication would destroy valid observations.
- One holding percentage is `168.87`; its raw source field is also `168.87%`. It is a source
  anomaly, not a scaling defect, and requires exact quarantine or an accepted quality exception.
- One security-bar OHLC envelope violation already has an exact `OPEN_ACCEPTED` /
  `ACCEPTED_LIMITATION` quality exception. The semantic auditor currently ignores exception
  governance and still counts the row. Only exact table, business-key, rule, and effective-time
  matches may suppress a governed numeric finding.

## Execution-script Audit

The current full builder stops the entire audit sequence when 11b has status `finding`; 11c-11e
therefore required independent invocation for this closure. Future candidate auditing should run
all read-only dimensions and aggregate gate status after evidence collection.

Cross-run domain reuse is also unsupported. Domain verification requires the candidate manifest
`run_id` to equal the current run, while a repair revision requires a new revision-bound run ID.
The repair script needs an explicit import step that deeply verifies the source candidate, hardlinks
immutable payload files into a new candidate, writes a new run-bound manifest, and emits a reuse
receipt. The r11 manifests must remain unchanged.

Reusable domains after deep verification:

- listed company
- trading calendar
- security market observation
- tradability state
- industry classification

Domains requiring rebuild:

- financial reporting
- disclosure and company event
- ownership and capital
- quality governance

After repair, candidate sealing and all five audits must run again. No ODS download, extraction,
V2 rebuild, or market-data recomputation is required.

## Test Audit

The focused baseline suite passed `69` tests, but it does not exercise the production failure
combinations. Required additions are:

- parent knowledge propagation for every resolver consumer;
- fail-closed parent effective interval resolution;
- financial concept validity across historical periods;
- mutually exclusive financial value representations;
- `holder_rank` in the ownership business and row keys;
- exact governed-exception matching;
- business-timezone through-date boundaries;
- run-bound, digest-verified import of reusable domain candidates;
- audit orchestration that continues 11c-11e after an 11b finding.

## Evidence

GPU-P100 evidence root:

`/srv/quanthus/gpu-p100-dwd-workspace/runs/dwd-v2-1-gpu-p100-20260810T063544Z-c010c277-r11`

| Report | Status | File SHA256 | Canonical digest |
| --- | --- | --- | --- |
| schema-partitions.json | verified | `73e07fb7459cb66cc7ac902858ac90bb8f1238b1886909afe0c5c0b5546d1bd2` | `sha256:a9ad5ad6aa0e0b3c54b247bb6aecce5b33018e5f8f81a55b79d0e4fcc5b1545f` |
| semantic-contracts.json | finding | `9ad2407984213e4b653e6673b6da8101663af9937522945c5cbd5843a4f97931` | `sha256:92160a7aeacacccb519d9c3efa01ecc65ff027f6798ceb6a6a50ce9659d11f64` |
| domain-invariants.json | finding | `b4796164d8652822c11411efbf22ff38f23a04ea680705d4986ff08949c68247` | `sha256:92e23251ce9c64606cf616c2bdd1691d20c7a64a4c012dad96a11433a5949225` |
| coverage-exclusions.json | finding | `5cf0c296ffbcb1c1948382e49db7efdb53d002c448ac1588119e7d7b9f41528e` | `sha256:f32e6ff58552eabaec2250c33b7955924c715e3da2ed8b269b3711c28fe6d6c9` |
| lineage-authority.json | verified | `a295184718702780d4410812ef9400bc94bfc9c1beccf58826ddc74175d20aba` | `sha256:bd29c35ce2d0ec139d65e545070c0f57df7ba88fa0db9dd219d269d915458ebc` |

