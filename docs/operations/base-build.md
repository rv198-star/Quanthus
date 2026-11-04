# Standard Base build input operations

Parent: [Permanent P10 data line](../design/lld/12-permanent-data-line.md).
Work item: `P10-DATA-BASE25-001`, [#596](https://github.com/rv198-star/Quanthus/issues/596).

This runbook covers implemented input inventory, selection freezing, selected-byte recovery and
internal composition/recovery/environment primitives.
It does not declare a complete materialize/audit/seal orchestrator, a Base 2.5 acceptance or a
production consumer switch. The full build consumes the previous paired Base, selected Daily
deliveries and their explicitly selected source/reference evidence. Original products stay read-only.

## Input selection and relocation

Run `python -m quanthus.pipelines.securities_warehouse.base_build_freeze freeze` with
`--input selection.json --roots roots.json --output frozen.json`. `verify` takes a frozen receipt
and the relocated roots. Root paths are separate from content identity. An extra root, missing or
changed member, link, version mismatch or unbound calendar member fails closed. Do not write
receipts inside an input root.

Selection v1 and existing Base 2.4 packages retain their original interpretation. Selection v2
(`p10-base-build-input-selection-v2`) adds `predecessor_version`, `target_version` and `evidence` to
the v1 fields. Versions must advance; the two Base packages must have the selected predecessor
version and cutoff. Monthly or longer periods use the same entry point and explicit expected days.

The generic envelope kinds are `quanthus_ods_base_bundle_v1`, `quanthus_dwd_base_bundle_v1` and
`quanthus_daily_1d5m_v1`. Their `content.files` use the same full relative-path/size/SHA256 member
inventory; `content.role` identifies the layer. Base metadata has `content.base_version` and
`content.through_date`; Daily metadata has `content.market_date`. The envelope format is distinct
from business version. Generic envelope admission does not establish table completeness or quality.
Existing Base 2.4/Daily kinds are explicit legacy adapters, not wildcards for arbitrary formats.

Each v2 evidence entry has exactly `id`, `purpose`, `parent_manifests` and `files`. Purpose is one
of `calendar_reference`, `daily_source_evidence` or `historical_quality`. Each file has exactly
`path`, `size_bytes`, `sha256`; its entire physical evidence root must match the selected set.
Parents must be selected package manifest hashes or explicitly retained metadata bytes in that
evidence root. Retain actual parent/source manifests and original capture/publication clocks.
Semantic audit must additionally prove the parent-child relation and permitted reference use;
a matching arbitrary byte hash is not parent provenance acceptance. Supplement-only references
may not introduce later market observations or infer historical PIT availability.

`expected_days_evidence` may point into a selected package or evidence input by id/path/hash.
Calendar semantics and expected universe remain explicit downstream gates. Evidence does not get
a manufactured product READY. Every successful freeze still reports
`FROZEN_BYTES_ONLY_SEMANTIC_GATES_PENDING`; it never qualifies or seals a Base.

## Recovery and retries

`base_build_recovery` restores only requested SHA-selected regular blobs from the retained content
archive. It verifies the pinned archive, complete decoded stream and final target members. The
existing archive requires a 256 MiB zstd decoder window. Correct targets are rehashed before
reuse; a conflicting target is preserved and rejected. Archive paths are never extracted directly.

Use `--archive-format relative-path` for a conventional tar whose selected member names match
request paths, including the portable preparation archive. Default mode retains the existing
SHA-addressed blob layout. Both modes require exactly one regular occurrence of each selected
archive member, verify size/SHA256, ignore unselected paths and reject partial/conflicting targets.
After a member failure the decoder is terminated; it must not hang waiting for its unread pipe.

Retry the same command with the same identities. After successful verification the existing
`RECOVERY.safe.json` is checked and retained byte-identically, including its original attempt
dispositions. A receipt for another request fails. Receipt publication is atomic and never
overwrites an existing receipt. It is a recovery receipt, not product READY or data qualification.
Use the outer build lock to serialize writers to a recovery/output root.

## Engine stage recovery and composition

`base_build_stages.StageRunner` executes an explicitly supplied dependency graph under one
nonblocking job lock. Shared prerequisites run once. A stage identity binds frozen inputs, code,
environment, policy, scope, its configuration and both identities of each dependency (stage and
output). Every reuse checks the complete output file inventory and runs the stage verifier.
Configuration changes propagate to descendants even if upstream output bytes remain identical.
Unrelated branches may retain verified results. The concrete builder must supply semantic stage
verifiers; a runner success alone never grants input admission or quality acceptance.

Each attempt has a separate directory, START and either SUCCESS or FAILURE receipt. Unreceipted
partials, corrupt outputs and prior failed attempts are preserved; recovery builds a new attempt.
The runner never invents a receipt to adopt an existing image and never writes product READY.
Metrics record elapsed time, files, bytes, result counters and build/reuse disposition. Outputs
inside the job are TMP until the separate product audit/seal contract succeeds.

`base_build_composition` creates the shared native engine's 33-table selection from a pinned
predecessor and a typed period delta. The seven declared Daily families append admitted period
rows. Five explicit identity extension tables may append source-replayed new identities from
the Daily reference members; changing an effective date does not authorize reusing a predecessor
identity. Their earliest identical real knowledge clock is retained, and conflicts fail. The
other 21 tables retain original rows and coverage. Empty placeholders are retained only when
the whole table is empty. Original lineage and values remain unchanged. This structural plan does
not replace independent expected-universe admission, global key/time/reference checks, inherited
held reconciliation or paired ODS/DWD quality audit. The standard seal wrapper checks the exact
stored contract, version, code and plan, and enumerates all physical members for the next build's
generic input envelope.

## Source admission and period command

The common entry point currently implements `freeze` and `prepare-period`:

```sh
PYTHONTZPATH='' PYTHONPATH=src python -m quanthus.pipelines.securities_warehouse.base_build \
  prepare-period --job /data/controls/build-job.json --contract /data/controls/delivery-contract.json
```

Use the versioned job schema in `config/pipelines/base-build-job.example.v1.json`, replacing its
zero revision with the exact clean GitHub checkout. Input roots must be kernel readonly in the
build namespace; keep the work root separate and writable by the actual runtime user. Run the
data stages with networking disabled. The preflight checks the locked environment, physical
space/inode reserves, fixed code, contract and selection scope. It hashes the complete input
closure once per invocation, including a stage reuse, and shares that verified prerequisite.
No job field can disable semantic or quality checks.

`prepare-period` independently derives expected trading days from a parent-bound raw calendar
and security-day populations from each delivered source universe/security master. A retained
derived calendar member must agree for the selected period; it is not authoritative input for
the reconstruction. The optional reference supplement binds source release, parent core and
parent Daily metadata; later market observations are not read. Original observed calendar clocks
are retained when projecting intervening closed days.

The source adapter locates each sealed Daily's exact selected release hash inside the frozen
evidence inventory, validates the original release contract and every artifact, and replays all
seven source projections with the Daily converter. Full typed values and multiplicities are
compared, including valuations and events. Native 5m strings are separately replayed by the
existing exact source verifier. A self-consistent repack is insufficient. Historical source
READY markers omitted by an explicitly parent-pinned evidence archive are not invented.

The five reference extensions are reconstructed from the same frozen universe/master and checked
against the delivered reference members. All other nonempty source artifacts require their
existing explicit disposition or a handler. A closed month end requires its selected event
release; the shared announcement converter retains actual publish/acquisition clocks and source
identity. Zero new quality held and normal 48-slot native days remain hard gates. Nullable legacy
Parquet headers may be adapted only when physical types match and every required value is present.
No precision or instant conversion is inferred from a nullable header.

The result is one job-local period TMP shared by later composition: seven Daily families, five
optional identity extensions, a source-derived security-day ledger and ADMISSION/DELTA receipts.
The stage graph preserves failed attempts and verifies all output bytes before reuse. This command
does not complete inherited-window reconciliation, paired materialize/audit/seal or A/B/C, and
does not produce formal Base READY. Those later actions remain under implementation.

## Locked offline runtime

`ops/data/base-build-environment.v1.json` and `ops/data/base-build-runtime.lock` declare the current
Linux x86_64 CPython 3.10 environment (Python >=3.10.12, glibc >=2.28), exact DuckDB 1.5.6,
PyArrow 23.0.1 and tzdata 2026.5 binary wheels. Prepare the three named wheels with the recorded
sizes/SHA256 before disconnecting; other interpreter/architecture combinations are not admitted.
No source build or unpinned dependency resolution is part of the build.

Create a dedicated venv with Python 3.10 on a filesystem that permits native library execution
(for example `/opt/quanthus/venvs` on CN02). The CN02 data disk is mounted `noexec`; keep wheels,
inputs, output and TMP there, but not installed `.so` libraries. Then install:

```sh
"$BASE_BUILD_VENV/bin/python" -m pip install --no-index \
  --find-links "$BASE_BUILD_WHEELHOUSE" --only-binary=:all: --require-hashes \
  -r ops/data/base-build-runtime.lock
```

Launch the pinned GitHub code with `PYTHONTZPATH=''` so `zoneinfo` uses locked tzdata rather than
the host database. `base_build_environment.check_environment` verifies platform, lock identity,
wheel bytes and every installed wheel payload against its pinned RECORD. A fresh isolated child
must load DuckDB/PyArrow and round-trip an exact Decimal and Shanghai instant; this detects
`noexec`, ABI and missing runtime dependency failures before data stages. Installed version
strings alone are insufficient. The restore-only external `zstd` decoder must be >=1.4.8; its
observed version is recorded and every decoded selected member is independently hash-checked.
The environment receipt explicitly grants no data acceptance. A clean environment smoke check
is separate from A's two-period workflow and B/C's full-data acceptance.

## Failure repair and accumulation

Keep the failed attempt, exact input/code/config/environment identities, reason and affected
coordinates. Stop affected descendants and withhold seal. Repair missing sources in the Daily
publisher, conversion/merge defects in production modules, and resource/environment faults in
preflight or recovery. Preserve original values, timestamps, grades and sealed products.

Commit the repair with an independently expected regression case. Corrected data/reference input
must be a new parent-bound immutable delivery with cloud-verified inventory. Freeze a new input
identity when bytes or selection change. Rebuild affected stages and descendants; reuse only fully
verified matching semantic identities. Do not edit generated Parquet, manifests or checkpoints to
turn failures green. Normal regression tests do not replace full paired data audit and independent
GitHub/GDrive reconstruction. A fix is reusable only when the same entry point automatically applies
it or its admission gate catches the condition on the next Build.

Focused tests: `tests/test_base_build_inputs.py`, `tests/test_base_build_freeze.py`,
`tests/test_base_build_recovery.py`, `tests/test_base_build_profile.py`,
`tests/test_base_build_delta.py`, `tests/test_base_build_composition.py`,
`tests/test_base_build_stages.py` and `tests/test_base_build_environment.py`.
Full handoff verification remains `make check`.
