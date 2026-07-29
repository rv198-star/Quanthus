# Issue 45 Shadow Deployment And Copy-Only Migration Acceptance

- Work item: `META-DATA-PIPE-003`
- Tracking issue: GitHub issue #45
- Evidence date: 2026-07-29 UTC
- Evidence status: accepted for the bounded shadow migration
- Production cutover: blocked; no `GO_FOR_CUTOVER` was recorded
- Deployed code revision: `8dcc2505c2b96bb557b155207ee6462b0f3163e1`
- Governing decision: `docs/architecture/adr-0005-dual-lane-canonical-and-ads-publication.md`

## Scope

This acceptance covers the copy-only deployment of the Issue #45 roles and the ordered shadow
migration of market dates `2026-07-27`, `2026-07-28`, and `2026-07-29`. CN01 remained the fixed
primary ODS and candidate producer, Hermes remained the independently executed standby producer,
and OCI owned epoch publication, arbitration, canonical catalog construction, operational ADS, and
the target-only release-set pointer.

It does not authorize consumer cutover, target timer activation, legacy publisher shutdown,
retention cleanup, or deletion of any legacy or shadow artifact.

## Implementation And Deployment Evidence

- Repository revisions `b462704` and `8dcc250` bind each modern epoch to the OCI-selected security
  master, copied canonical parent/base, policies, and code revision. Candidate verification checks
  DWD security-master lineage against the trusted OCI epoch.
- Legacy epochs and candidates remain verifiable under their original contract but are explicitly
  audit-only and cannot build a new candidate or enter a new arbitration.
- `make check PYTHON=.venv/bin/python` passed 387 tests at revision `8dcc250`. A final OCI-host
  rerun of `dual-lane-doctor oci` also passed after the ordered migration.
- OCI, CN01, and Hermes each reported the exact revision above. All three role-specific
  `dual-lane-doctor` checks passed with policy digest
  `sha256:eaddc60fee2c64dec8e88590e3a376f7bed574771f105cdafef5b42b8de55d6a`.
- OCI, CN01, and Hermes used the frozen Python 3.10/PyArrow 23.0.1/Polars 1.43.0/DuckDB 1.5.5
  runtime contract. Hermes retained its `polars[rtcompat]` installation for CPU compatibility.

## Ordered Migration Results

| Market date | Epoch id / manifest | CN01 candidate | Hermes candidate | Shared business digest | Release set |
| --- | --- | --- | --- | --- | --- |
| 2026-07-27 | `epoch-20260727-8dcc2505c2b9-6806fe11c31d` / `sha256:d237eb1eabd6ba4ca33b4e350035e2dcc5d0c29d1ac9ec58769508d30feb7c4e` | `candidate-cn01-20260727-d237eb1eabd6-20260727-20260727T151929127088Z` | `candidate-hermes-20260727-d237eb1eabd6-20260727-20260729T181657644350Z` | `sha256:7b91cc1fbfff21bef7f242e3b2e99df4044e14c2b7c81b1b4edd8d3ee57a04d8` | `release-set-20260727-16ef5d44eda1f824` |
| 2026-07-28 | `epoch-20260728-8dcc2505c2b9-7316f899aa25` / `sha256:3caaffde6a817b802d851c25cbb0c5c490febb74563e8e2b6cdd548187185086` | `candidate-cn01-20260728-3caaffde6a81-20260728-20260728T114208920955Z` | `candidate-hermes-20260728-3caaffde6a81-20260728-20260729T190703757566Z` | `sha256:c4643d1a6a56a02a68737413323f53871b7f6c5689185f3c534e8acf114123af` | `release-set-20260728-4598754703e65903` |
| 2026-07-29 | `epoch-20260729-8dcc2505c2b9-11a3d2d3b042` / `sha256:cf2f730e4e9859a335818e6f73ef8bf65ea4e2dae167707e7744c3fe9634b5cf` | `candidate-cn01-20260729-cf2f730e4e98-20260729-20260729T110001481742Z` | `candidate-hermes-20260729-cf2f730e4e98-20260729-20260729T074016396258Z` | `sha256:3ad603328da1789122aa247364883834f9fa7ed7344bfece64700c1772e419fa` | `release-set-20260729-eb1617fa056eda49` |

Every pair used the same security-master digest:
`sha256:54604917da2784ba66847c86b7ee9bc8849aaf118a399ea9639720c5023b51f1`.
OCI compared both complete candidates before arbitration, selected CN01 with reason `primary`, and
did not merge rows or artifacts across lanes.

Hermes had no complete market-daily ODS release for `2026-07-28`. The operator used the governed
`manual_recovery` launcher with an absolute future deadline. It independently acquired and sealed
release `20260728-20260729T190703757566Z` with 4,992 daily-bar and 4,992 status rows, and declared
`excluded_from_collection_window_experiment=true`. No CN01 market artifact was copied to Hermes.

## Final Publication Evidence

The verified target pointer resolves to
`release-set-20260729-eb1617fa056eda49`. Its catalog contains exactly these ordered mappings:

| Market date | Accepted generation |
| --- | --- |
| 2026-07-27 | `generation-20260727-cn01-16ef5d44eda1f824` |
| 2026-07-28 | `generation-20260728-cn01-4598754703e65903` |
| 2026-07-29 | `generation-20260729-cn01-eb1617fa056eda49` |

The release-set verifier checked its acceptance record, catalog, canonical generation, operational
ADS, `READY`, manifest hashes, and safe publication-root-relative references. The final operational
ADS is `ads-operational-20260729-eb1617fa056eda49`.

## Preservation And Activation Audit

- All six pre-migration legacy consumer pointer checksums passed `sha256sum -c` after the shadow
  migration: corporate-action replay, daily market, daily market summary, daily unadjusted OHLCV,
  price limits, and ST status PIT.
- The legacy `quanthus-t-close-plus-5h.timer` remained enabled and active. Its pre-existing service
  status remained failed because of the SLA exit classification; this migration did not stop,
  restart, disable, or replace it.
- OCI epoch and arbitration timers remained disabled/inactive. CN01 and Hermes candidate timers
  remained disabled.
- Existing ODS releases, legacy OCI mirrors, old epochs/candidates, logs, pointers, and evidence
  were retained. Migration and transfer commands used copy-only paths without `--delete`.

## Residual Gate

This is acceptance of a bounded manual shadow rehearsal, not production readiness. ADR 0005's
clock enforcement, terminal arbitration/correction behavior, full PIT/tradability family,
qualification horizon, recovery drills, retention proof, and explicit cutover approval remain open.
The target release-set pointer is not a consumer authority while the legacy publisher remains live.
