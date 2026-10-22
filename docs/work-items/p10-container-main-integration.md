# P10-DATA-OPS-016: Integrate the collection/container stack

| Field | Value |
| --- | --- |
| Parent | [Work-item index](index.md); [P10 / #537](https://github.com/rv198-star/Quanthus/issues/537) |
| Governing design | [Permanent P10](../design/lld/12-permanent-data-line.md); [data foundation](../design/lld/01-data-foundation.md) |
| Design status | approved: Owner requested source integration on 2026-10-23 |
| Delivery gate | open: integrated checks and source review in progress; deployment is separate |

## Source and scope

Starting main is `e922247620b62a049dc6d45f9876c3476c2ce394`, following the
[Base 2.4 construction integration](p10-base24-main-integration.md) / #553.
The retained clean source is `codex/base22-standalone` at
`ab261d0a02ea161ca4c7790b376be25134f7e9d2`. Import the operational dependency
closure in an independent integration worktree, preserving the source worktree.

| Boundary | Production implementation | Focused tests |
| --- | --- | --- |
| Frozen-input intake/job and T-day acquisition/core/native-5m cycle | `daily_base24_intake.py`, `daily_base24_job.py`, `daily_base24_cycle.py`, `daily_base24_provider.py` | `test_daily_base24_intake.py`, `test_daily_base24_job.py`, `test_daily_base24_cycle.py` |
| Finite recovery with verified independent capture reuse | `native_capture_resume.py`, `base24-native-recovery.v1.json` | `test_native_capture_resume.py`, `test_native_login_repair.py` |
| Immutable source bundles and image jobs through ODS/DWD | `container_code.py`, `container_jobs.py`, `p10_container_control.py`, `ops/data/` | `test_p10_container_delivery.py` |
| Task-local API notification and retry | `p10_operational_mail.py` | `test_p10_operational_mail_content.py`, `test_p10_container_delivery.py` |
| Exact Base 2.4 Daily/Weekly backup and durable delivery queue | `p10_gdrive_backup.py`, `p10_delivery.py`, `base24-backup.v1.json` | `test_p10_gdrive_backup.py`, `test_p10_delivery.py` |
| Owned scratch leases, conservative cleanup and internal clock | `p10_tmp.py`, `p10_tmp_schedule.py` | `test_p10_tmp.py`, `test_p10_tmp_schedule.py` |

Warehouse module/config names above live under
`src/quanthus/pipelines/securities_warehouse/` and `config/pipelines/`;
the `p10_*.py` modules live directly under `src/quanthus/`, and tests under
`tests/unit/`. Host launchers and uninstalled systemd templates live under `ops/`.
The [container runbook](../operations/p10-container-jobs.md) owns exact commands,
mounts, configuration and rollback. Checked-in examples contain placeholders only.

## Preservation and acceptance limits

Do not overwrite main's frozen `daily_domain.py` or `daily_ods_catalog.py` with
older source-branch variants. This cycle uses the opt-in `daily_core_*` and
`daily_base24` path. Preserve main's quality-policy helpers, #538 purpose/repair
interfaces, Base 2.4 shard safeguards and all P6 changes. Historical Base22/23/231
source-hunting modules and legacy CN01/OCI script changes are outside this merge.

This is source integration. No service restart, timer activation, live acquisition,
mail, upload, credential/ACL change, CURRENT mutation or formal-data deletion is
performed. Preserve original runtime clocks and failed/late-recovery evidence.
No source test accepts a natural T-day run. The Weekly entry remains financial
only, with other domains held; generic explicit Weekly backup selection does not
establish an automatic full-domain Weekly collector/queue. #537 remains open for
its outstanding operational/Weekly gates. #539's next-Base incorporation of #538
remains separate. Merging does not qualify P3/P5/P6 consumers or historical PIT.

## Verification

Run focused unit/synthetic integration tests, changed-file Ruff/strict typing,
documentation/ownership contracts and repository `make check` against the actual
integrated tree. Record real commands, Python/runtime, pass/fail/skip counts and
tree identity in the handoff. Preserve known repository lint debt and distinguish
unavailable hosted CI or Python versions from executed tests. Image construction
and live kernel/container/provider/mail/Drive acceptance are separate runtime
checks; retained historical receipts are not a fresh run of this integrated tree.
