# P10 data pipeline image

Build and operation instructions: [container runbook](../../docs/operations/p10-container-jobs.md).
Source integration and verification: [P10-DATA-OPS-016](../../docs/work-items/p10-container-main-integration.md).

The image contains versioned code/runtime only. Acquisition through ODS/DWD,
scoped Weekly, operational mail, GDrive transport and TMP housekeeping use explicit
roles of that image. Data, state and secrets stay in configured host binds. The
checked-in examples are not deployable until their exact identities and private
paths are provisioned and independently qualified.

## Reference-import failure continuation (v1)

Source bundles include the exact tracked `pyproject.toml` as well as `src` and
`config`. Collector subprocesses run with their own bundle as working directory;
transitive configuration discovery must not select the pipeline bundle instead.
A release preflight imports the native delivery/capture modules from the sealed
collector and freezes an exact-date reference before acquisition is resumed.

A single continuation is permitted only for `CORE_READY_5M_FAILED` with the exact
`DAILY_CYCLE_PROVIDER_FAILED_reference` receipt and no reference or capture output.
Under the same date lock, verify the original core, both ODS manifest identities,
business date, Base identity and policy digest before creating a fresh run. Record
`native-continuation.json`, retain failures and reuse the core unchanged; do not
recollect daily sources or rebuild the core. Repeated continuations are rejected.
The two full-acquisition attempt limit, native resource limits, intake/delivery
windows, source quality gates and consumer admission remain unchanged. Other
failure stages require a separate disposition rather than automatic full reruns.
