# Standard Base build input operations

Parent: [Permanent P10 data line](../design/lld/12-permanent-data-line.md).
Work item: `P10-DATA-BASE25-001`, [#596](https://github.com/rv198-star/Quanthus/issues/596).

This runbook covers implemented input inventory, selection freezing, selected-byte recovery and
unsealed paired materialization and internal composition/recovery/environment primitives.
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

The common entry point currently implements `freeze`, `prepare-period`, `materialize` and `audit`:

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

`prepare-period` independently derives expected trading days from all parent-bound raw calendar
artifacts in the selected Daily releases and explicit supplement. Sources may cover partial dates;
the union must cover every required exchange/day and all observed overlapping values must agree.
Rows with unknown source or missing `built_at` are inert cached evidence and cannot fill gaps.
For each day the earliest complete observed original artifact is selected deterministically;
its source identity and actual knowledge clock remain intact, including closed-day projection.
Security-day populations come from each delivered source universe/security master. A retained
derived calendar member must agree for the selected period; it is not authoritative input for
reconstruction. Supplements bind source release, parent core and parent Daily metadata; later
market observations are not read.

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
Its `CONFIG.json` retains only input-relative source/reference selection, bound into ADMISSION;
archived products can replay it without looking up original host paths.
The stage graph preserves failed attempts and verifies all output bytes before reuse. This command
does not complete inherited-window reconciliation, paired materialize/audit/seal or A/B/C, and
does not produce formal Base READY. Those later actions remain under implementation.

## Unsealed paired materialization

Use the same job and contract with the `materialize` action. Its graph is
`period -> windows -> ODS -> DWD`; both layers consume that one verified period and window
closure. Stage identities include code, so a period built with a different revision is not an
implicit cache hit. A failed DWD attempt preserves its evidence; retry verifies and reuses the
matching period/windows/ODS stages before constructing a new DWD attempt.

The window stage reads the predecessor's actual legacy plan, acquisition CHECKPOINT and original
terminal receipts, or the successor's standard `evidence/windows` representation. Historical
failure attempts are retained separately from final terminal states. Held coordinates include
their original contract and scope identity and must remain exactly equal. New Daily security-day
windows use their own contract; they do not invent legacy supplier partitions. Duplicate IDs,
overlapping intervals, missing/extra/both terminal states, changed plan bindings and new held
windows fail closed. Empty terminal directories may be absent in file-inventory archives; final
exact membership and counts still apply. This window stage does not independently replay all
historical prices or dispose of historical quality contradictions.

The ODS materializer retains every frozen ODS/Daily/evidence member with independent unchanged
bytes, plus the complete source inventory, contract, period and windows. It retains predecessor
DWD control/evidence members and exact empty DWD placeholders; nonempty predecessor DWD payload
bytes belong to the paired DWD materializer. Empty original bytes must survive even when a
nonempty new period legitimately removes their placeholder from the successor DWD. Keeping the
original empty files avoids synthesizing a replacement Parquet during later source verification.
The archive reserves its full member-copy space before copying, rejects source growth or mutation,
and records its original provenance without introducing a runtime dependency on archived paths.
The readonly ODS auditor can operate after all original sources have been removed. It verifies
the exact archive namespace and frozen closure, the owning predecessor auditor, scope/period/window
bindings and every other member. Relabelling a valid frozen period as a later version fails even
when the changed outer metadata hashes agree. It also reconstructs the entire window closure from
the embedded predecessor and period, so a self-consistent new held cannot be relabelled as inherited.
This is an ODS archive audit, not paired audit B.

The DWD materializer consumes the shared 33-table composition through the existing native engine,
preserves the inherited limitation list and every referenced original evidence byte, and binds
the exact ODS materialization, period and window identities. Stage receipts record table counts,
rows by table, source bytes/member counts and observed resource peaks. Completed stages are TMP;
neither layer receives a new manifest or READY. Do not edit their successful stage outputs to add
publication metadata: the stage inventory is immutable. Paired readonly audit, guarded publication
and continuous-version full workflow A remain required separate gates. Synthetic 33-table/graph
recovery tests establish these materialization primitives only, not A/B/C or a formal Base.

## Readonly paired audit and archived-source replay

Use `audit --job audit-job.json --contract delivery-contract.json` with the separate fixed schema
in `config/pipelines/base-build-audit-job.example.v1.json`. Select the same clean pinned revision
as materialization. Both products must be kernel readonly, the namespace must have only loopback,
and every declared original input root must be absent or unreadable. The caller must isolate
production acquisition/recovery/cache directories as well. The actual namespace/runtime boundary
is a separate qualification; mocked mount flags in unit tests do not establish it.

Audit TMP belongs in a separate disjoint work root, which can use the system disk while products
remain on the data disk. Admit actual free space, inodes and the complete audit write reserve
before starting. An existing audit root is preserved and rejected. Failed attempts keep a bound
FAILURE receipt, exact code/input/policy/job identities, actual time and stable reason; use a new
attempt directory after a failure. No job switch can disable source denial or quality gates.

`base_build_audit` runs the ODS owning archive audit and the standard native whole-image scan for
all 33 DWD tables, including global keys, versions, references, exact types/precision, lineage,
partitions and cutoff rules. It then independently proves that every inherited nonempty file and
every admitted period file is selected, with the original per-table update/inherit disposition.
A DWD that coherently drops an old nonempty file can pass its own self-consistent native scan;
the paired original closure must reject it. Original limitation values remain identical. The
native writer may serialize the successor registry differently; its raw original bytes remain
unchanged in ODS and predecessor-control, and every referenced evidence file is byte-identical.

The audit maps each original predecessor DWD file to its verified local paired copy or retained
empty file, then replays the entire period from archived Daily/source/reference bytes. Only the
seven original parent reference tables need an explicit relocated reader; original sealed
metadata, selected-member set, independent files, hashes and partition counts still apply. The
ordinary reader retains its original physical-directory contract. No reconstructed full-size
predecessor cache or original source directory is needed. The freshly audited byte closure is an
in-process prerequisite, never a persisted cache receipt. Recomputed source ADMISSION, every
typed delta identity and every source/config/ledger binding must equal the original result.

Physical fingerprint guards can reject observed changes early. Matching metadata is not a byte
audit and cannot authorize reusing an old audit: timestamp granularity can hide very fast in-place
writes. Actual layer hashes and source replay remain mandatory. A later publication transaction
must preserve exclusive ownership and run a fresh readonly audit on recovery, rather than trust a
stale guard. Publication is still under implementation; successful stage directories are immutable.

The audit evaluates the four exact registered historical cases using
`config/pipelines/base-build-historical-gates.v1.json`. Source registration, inherited registration
or matching listing/venue/date observations keep a case pending. Omitting an observation cannot
hide a registered diagnostic. The current policy provides no repair or override and adds no held
window. Only absence of both observations and registration proves nonapplicability. The real
Base 2.5 inputs register all four cases, so they remain ineligible for publication. A complete
synthetic fixture can pass the absence rule. `formal_READY` always remains false for this action.

Each successful audit has a separate `COMPLETION.json` execution envelope binding the exact job,
unique work root, code/policy/environment, actual start/end times and AUDIT bytes. Existing roots
are rejected; a saved AUDIT file is not authority for a new publication invocation.

Internal `base_build_publication` primitives preserve successful stage inventories by recording a
no-overwrite ownership intent and moving each layer into the job's `published` directory on the
same filesystem. The original SUCCESS bytes remain intact. A consumed job cannot restart the
materialization graph. Only owned incomplete marker bytes can be archived during recovery;
unknown or altered markers stop recovery and remain available for diagnosis. Manifests are
published before READY using no-overwrite links, with interrupted links resolved before strict
single-inode hashing. Every incomplete recovery still requires a fresh isolated whole-pair audit;
the metadata primitives do not authorize publication by themselves. Fresh full byte readback,
mutual pair binding, complete native scan and historical eligibility are required at the marker
boundary. Native legacy sealing retains its existing contract.

Resource admission includes all retained failed attempts, controls and outputs in the same job.
Preflight reserves only the remaining allocated allowance plus the free-space floor; it does not
demand the original whole allowance again after a partial successful run. Each subsequent stage
receives the remaining total allowance, with room for immutable control receipts, and StageRunner
checks the total before recording success. Audit TMP retains its own disjoint budget.

The privileged controller, actual readonly protection/namespace qualification, governed historical
dispositions, standard Daily/reference publishing and two consecutive complete versions A remain
open. The synthetic fixture materializes all 33 tables, removes original inputs and period TMP,
replays all sources, prepares paired metadata and re-audits the sealed pair. Mocked mount flags
in that test do not establish runtime A, full-data B or independent-node C.

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
Paired primitives: `tests/test_base_build_windows.py`, `tests/test_base_build_ods.py` and
`tests/test_base_build_pair.py`, including source removal, scope relabelling, resource admission,
full 33-table materialization and interrupted-DWD recovery with verified shared-stage reuse.
`tests/test_base_build_audit.py` covers paired whole-image/source replay, coherent inherited-file
omission, unchanged original limitation values, exact relocated references, readable-source
rejection and bound failure preservation. Legacy Base 2.3/2.4 reader tests remain required.
Full handoff verification remains `make check`.
