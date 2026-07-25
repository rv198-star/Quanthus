# S2 Historical ST Status Contract

- Status: Implemented and promoted on OCI (2026-07-25)
- Tracking issue: [#8](https://github.com/rv198-star/Quanthus/issues/8)
- Policy: `config/st-status.v1.json`

## Authority and meaning

Baostock historical daily `isST` is authoritative for the binary state used by price-limit
and entry checks: `st_class` or `non_st_class`. The announcement-derived track remains
supplementary audit evidence. It must not override an observed Baostock value merely because an
announcement date differs from the first provider flip.

Binary `isST` does not prove whether the name was `ST`, `*ST`, or subject to a particular
delisting-risk subtype. Quanthus therefore does not synthesize those details. BSE `920*` remains
outside the P1 universe.

## Point-in-time execution rule

A state observed on market session T is available only after that close and can first affect an
order on the security's next actual exchange session. Weekends, exchange holidays, and suspended
sessions do not consume that step. The formal revision publishes:

- `st_status_observed_pit.parquet`: compressed provider observations and raw evidence lineage;
- `st_status_execution_daily.parquet`: one row per in-scope security session, shifted from the
  immediately preceding actual session.

The first session in a security's supported history has no prior observation. Its state is
explicitly `unknown`, `can_open_position=false`, and reason
`st_status_unknown_fail_closed`. Consumers must use the execution table and may not join the
same-day provider flag into an opening decision.

## Build and publication

`quanthus build-st-status` verifies binary states, economic-identity uniqueness, absence of
same-day/future leakage, and complete prior-session coverage. It writes a new immutable OCI
revision with content and schema checksums, source revision hashes, and a manifest. Promotion
atomically updates the OCI `CURRENT` metadata only; Hermes raw files and pointers remain intact.

Weekly acquisition remains a Hermes responsibility. OCI consumes the resulting immutable raw
candidate, rebuilds with at least a 90-calendar-day lookback, creates a new child revision, runs
the same acceptance checks, then promotes and publishes the accepted projection. Earlier
revisions remain addressable; late corrections are recorded through parent lineage rather than
overwriting history.

PostgreSQL is a rebuildable serving projection. S2 publication requires a dedicated OCI writer
over the private channel; the `astock_ro` account is deliberately insufficient and must never be
upgraded or reused for writes.

## Current revision

The promoted OCI revision is `st_status_pit_v2_oci_v1_1_20260725`, derived from the immutable,
identity-normalized candidate `st_status_pit_v2_baostock_code_validity_v1_1_20260725`.

| Artifact | Rows | SHA-256 |
| --- | ---: | --- |
| `st_status_observed_pit.parquet` | 7,448 | `979efa5364059bd50fb6ba3c76df0af94f12ad66e7116f4e66237061f6ebfd28` |
| `st_status_execution_daily.parquet` | 13,799,601 | `373e7b905cbfad06aa59031d74b641d47522f5073ad859526cab2772a01016ca` |

The execution table covers 5,465 securities from 2011-01-04 through 2026-07-23. It contains
460,021 `st_class`, 13,334,115 `non_st_class`, and exactly 5,465 fail-closed `unknown` rows: one
unsupported first session per security. Independent full-table audit found zero duplicate keys,
same-day/future observations, mid-history unknowns, unknown rows permitting entry, or `920*` rows.
The v1.1 child also removes explicitly out-of-scope CDR `689009`; its parent remains immutable.

The local evidence is outside Git at
`/home/ubuntu/data/quanthus/revisions/st_status_pit_v2_oci_v1_1_20260725/`; OCI promotion metadata is
`/home/ubuntu/data/quanthus/revisions/st_status_pit_CURRENT.json`.
