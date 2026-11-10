# ODS Base Runbook

This runbook operates `META-DATA-BASE-001` under ADR 0005 and
`docs/design/lld/01-data-foundation/ods-base.md`. Migration is copy-only. Do not delete legacy
data, attempts, daily releases, provisional/final packages, logs, or pointers; do not change
production schedules or record `GO_FOR_CUTOVER`.

## Fixed Layout And Authority

| Concern | Contract |
| --- | --- |
| Source authority | Hermes `/root/work/astock` |
| Published Base | Hermes and CN01 `/srv/quanthus/ods/base` |
| Frozen inventories | Hermes `/srv/quanthus/ods/base/.inventories` |
| Transfer staging | CN01 `/srv/quanthus/ods/base/.incoming-<bundle-id>` |
| OCI | No target-chain ODS Base |
| Provisional policy | `config/pipelines/ods-base.v1.json` from one reviewed commit |
| Final July policy | `config/pipelines/ods-base-final-through-20260731.v2.json` |

Deploy code only through GitHub into a node-local checkout. Data transfer uses the dedicated
staging path and writes `READY` last. Never rsync repository code or a mutable producer directory.

## Preflight

1. Verify the exact clean Git revision on Hermes and CN01.
2. Record byte-accurate free space and source sizes for both nodes.
3. Confirm ODS market, event, and minute services are inactive before a manual repair or Base
   inventory. Do not disable their timers for a normal read-only inventory/build.
4. Verify all required canonical files, 4,992 files in each minute tree, the announcement SQLite
   integrity result, every selected release `READY`, recursively classified mixed source roots,
   and the machine-readable list of source families not formally acquired.
5. Reconcile known date gaps. Use an isolated revision root for a repair; never mutate a historical
   release or production pointer.

Manual provider collection through `ops/bin/run-hermes-ods-lane` loads
`/etc/quanthus/hermes-ods-lane.env` with exported variables. For a bounded historical minute
repair, set `HERMES_ODS_MINUTE_MODE=backfill`, an explicit page cap, and a timeout that exceeds the
measured full-universe duration. Write the attempt to a dedicated `--shadow-root`. A failed or
partial attempt remains evidence and cannot be selected without a complete `READY`.

## Provisional Build Through July 30

Run from the exact deployed issue revision on Hermes:

```bash
sudo install -d -o root -g root -m 0750 \
  /srv/quanthus/ods/base /srv/quanthus/ods/base/.inventories

sudo ops/bin/inventory-ods-base \
  --policy config/pipelines/ods-base.v1.json \
  --astock-root /root/work/astock \
  --through-date 2026-07-30 \
  --output /srv/quanthus/ods/base/.inventories/ods-base-through-20260730-v1.source.json

sudo ops/bin/build-ods-base \
  --policy config/pipelines/ods-base.v1.json \
  --astock-root /root/work/astock \
  --output /srv/quanthus/ods/base/ods-base-through-20260730-v1 \
  --bundle-id ods-base-through-20260730-v1 \
  --through-date 2026-07-30 \
  --code-revision "$(git rev-parse HEAD)" \
  --source-inventory /srv/quanthus/ods/base/.inventories/ods-base-through-20260730-v1.source.json

sudo ops/bin/verify-ods-base \
  --policy config/pipelines/ods-base.v1.json \
  --bundle /srv/quanthus/ods/base/ods-base-through-20260730-v1 \
  --deep
```

The inventory file is immutable evidence. If it already exists or source selection changes, stop
and use a reviewed new inventory name or package revision; do not overwrite it.

Promote only after deep verification:

```bash
sudo ops/bin/promote-ods-base \
  --policy config/pipelines/ods-base.v1.json \
  --ingress-bundle /srv/quanthus/ods/base/ods-base-through-20260730-v1 \
  --base-root /srv/quanthus/ods/base \
  --current /srv/quanthus/ods/base/BASE_CURRENT.json
```

## CN01 Mirror

Use `ops/bin/sync-ods-base` from an authorized transfer host to stage the package under CN01's
hidden incoming child. The sync excludes `READY` until every other member is copied. On CN01,
deep-verify the hidden staging package with `verify-ods-base --allow-staging-name --deep`, then
promote it with `promote-ods-base --move-ingress` and advance only the ODS
`BASE_CURRENT.json`. The staging exception accepts only the exact
`.incoming-<manifest bundle-id>` name; normal package and pointer verification remain strict.

Record and compare on both nodes:

- package id and `through_date`;
- manifest SHA-256 and content digest;
- file count and total bytes;
- deep-verification result;
- `BASE_CURRENT.json` package and digest bindings.

Any mismatch blocks publication. Do not repair a copied package in place; repeat transfer into a
new hidden staging child after diagnosing the source.

## Base 2.3 warehouse input

Use a checkout containing the reviewed monthly source adapter. Obtain code through
GitHub; copying the package does not deploy its reader. Keep the exact sealed ODS
bytes and the original paired identity. This procedure reads the restored package
and writes only a new isolated audit/plan directory, never CURRENT or a service.

For a locally restored 2.3 image, supply its physical root as `ODS_BASE23`, an
existing isolated writable parent as `ODS_AUDIT_PARENT`, a unique job name as
`ODS_INPUT_JOB`, and an actual timezone-qualified observation time as
`ODS_OBSERVATION_CUTOFF`. The latter must cover the preserved original artifact
cutoffs; September 30 is a business cutoff, not the observation cutoff.

```bash
ops/bin/verify-ods-base \
  --bundle "$ODS_BASE23" \
  --monthly-consumer-contract config/pipelines/ods-base23-warehouse-input.v1.json \
  --model config/pipelines/dwd-domain-model.v1.json \
  --knowledge-cutoff "$ODS_OBSERVATION_CUTOFF" \
  --monthly-audit-root "$ODS_AUDIT_PARENT/$ODS_INPUT_JOB-verify"
```

This always performs full native verification; `--deep` is not required. The
image and model hashes must match the pinned successor contract, all original
manifests and native members must close, and scratch must be new and physically
disjoint. Register at least the existing 4 GiB RSS bound, 8 GiB free-space floor,
256 MiB scratch allocation and 30-minute per-stage time limit before running.
The 256 MiB bound is for audit scratch bytes, not application memory. Any failure
leaves evidence and never falls back to an old source. Two original physical-copy
receipts remain bound to the unchanged seal; a source-read receipt does not replace
target-node recovery or claim a new two-copy construction.

To prepare model-mapped raw inputs instead, invoke one whole admission and emit
the immutable source ledger in the same job:

```bash
ops/bin/plan-dwd-dws-run \
  --ods-package "$ODS_BASE23" \
  --model config/pipelines/dwd-domain-model.v1.json \
  --mode monthly_base \
  --monthly-consumer-contract config/pipelines/ods-base23-warehouse-input.v1.json \
  --monthly-audit-root "$ODS_AUDIT_PARENT/$ODS_INPUT_JOB-audit" \
  --output "$ODS_AUDIT_PARENT/$ODS_INPUT_JOB-plan" \
  --run-id "$ODS_INPUT_JOB" \
  --through-date 2026-09-30 \
  --knowledge-cutoff "$ODS_OBSERVATION_CUTOFF" \
  --code-revision "$(git rev-parse HEAD)" \
  --runtime-id cn01-monthly-ods-input-v1
```

Do not run both commands merely to repeat the same expensive prerequisite inside
one job. The planning command already performs the native source admission. Its
ledger preserves distinct original revisions, source hashes, physical paths and
clocks, while its purpose is raw reconstruction only. The native member resolver
also rechecks individual files when used after admission.

Successful output admits `WAREHOUSE_RAW_RECONSTRUCTION`, with the seven minute
gaps still disclosed. It grants no execution, research/PIT or complete-minute
qualification. An unchanged July DWD executor will not accept the new monthly
plan. Production DWD Base compatibility, October increment continuation, paired
switch/rollback and any service deployment need their own existing gates; do not
mix ODS 2.1 and DWD 2.3 or alter the seal to bypass them.

## July Valuation Gap Repair

The retained valuation history ends on 2026-07-18. Acquire the open-session gap through July 30
directly from BaoStock before final inventory freeze; do not re-slice the retained Parquet and label
it as a new acquisition:

```bash
PYTHONPATH=/root/work/Quanthus/src \
  /root/work/astock/.venv/bin/python -m quanthus.providers.valuation_history \
  --start 2026-07-20 \
  --end 2026-07-30 \
  --stock-list /root/work/astock/data/stock_list.parquet \
  --output /root/work/astock/data/backfill/valuation/2026-07-20_2026-07-30/valuation_history.parquet \
  --receipts /root/work/astock/data/backfill/valuation/2026-07-20_2026-07-30/receipts.parquet \
  --checkpoint-dir /root/work/astock/data/backfill/valuation/2026-07-20_2026-07-30/checkpoints
```

Acceptance requires a nonempty artifact, one receipt per requested security, zero `failed`
receipts, dates bounded to the requested interval, and at least one non-null valuation field per
successful security. July 31 and later are acquired by `market-daily-core`; the bounded repair is
not installed as another timer. Preserve the repair metadata, receipts, and checkpoints through
final Base verification.

## Final July Base

The close-release gate is expected to become runnable only after the final 5m job completes. Run it
independently on Hermes and CN01 using each node's release root:

```bash
ops/bin/check-ods-base-release-gate \
  --policy config/pipelines/ods-base-final-through-20260731.v2.json \
  --parent-policy config/pipelines/ods-base.v1.json \
  --release-root /root/work/astock/data/shadow/hermes-ods-v1/releases \
  --base-current /srv/quanthus/ods/base/BASE_CURRENT.json \
  --market-date 2026-07-31 \
  --expected-parent-bundle-id ods-base-through-20260730-v1 \
  --expected-parent-manifest-sha256 sha256:d38b8332ddd5427104bbc6f71bf89df1c81f85cca507a1f9a80ac58662b7352e \
  --expected-parent-through-date 2026-07-30
```

CN01 substitutes `cn01-ods-v1` in `--release-root`. The command verifies the exact provisional
Base pointer under the provisional policy, then applies the final policy to market `CURRENT` and
the latest eligible event/1m/5m releases. The final market release must include the July 31
`valuation_daily_delta`. The command also verifies every artifact checksum and free space. Its
result deliberately sets `authorizes_base_promotion=false`: passing this gate authorizes freezing
the Hermes inventory and constructing a final candidate, not publishing it.

After both nodes pass, freeze a new Hermes inventory through July 31 and build
`ods-base-202607-v1` with `config/pipelines/ods-base-final-through-20260731.v2.json`,
`--parent-base /srv/quanthus/ods/base/ods-base-through-20260730-v1`, and
`--parent-policy config/pipelines/ods-base.v1.json`. The final policy includes the bounded repair
Parquet as canonical valuation history and preserves its receipts, metadata, and checkpoint parts
as source evidence. It also selects the complete CN01 2026-07-31 weekly release from the explicit
Hermes `data/revisions/cn01-weekly-gap-fill-20260731` copy. The archive exception must retain the
original CN01 path, reason, and exact `release.json` SHA-256; both `READY` and every artifact must
match that manifest. Re-run the provisional build, deep-verification, clean-path, and
identical-input gates, then stage and deep-verify the candidate on CN01.

Final promotion remains blocked until Issue #47's expanded Phase B gates are accepted: standard
`base/daily/weekly` publication, a checksum-bound node-local ODS catalog, the governed recurring
source gaps or an explicit blocking disposition, the coverage matrix, and one normal daily and
weekly cycle on both nodes. Once those gates are accepted, each final promotion must pass
`--expected-current-manifest-sha256 <provisional-manifest-sha256>`; a missing or changed prior
pointer blocks the compare-and-swap.
The first promotion across this policy boundary also passes
`--current-policy config/pipelines/ods-base.v1.json`; later idempotent verification and promotion
use only the final policy.

The July 31 daily release is absorbed logically but remains immutable source evidence. The
provisional Base also remains. Cleanup requires a later reviewed inventory and explicit deletion
authority.

## Failure And Recovery

- Inventory failure: correct the policy or source gap; no package exists.
- Build failure: leave staging for diagnosis only when retained by the tool; no `READY` or pointer
  is authoritative.
- Transfer failure: hidden CN01 staging is not published; retry copy-only.
- Deep verification failure: quarantine by path and build/transfer a new package id as required;
  never edit immutable package bytes.
- Pointer promotion failure: prior `BASE_CURRENT.json` remains authoritative. Verify its package
  before retrying compare-and-swap.

Acceptance evidence belongs under
`docs/acceptance/meta-data-base-001-ods-base-202607/` and records sanitized inventories, commands,
timings, node sizes/free space, manifest summaries, digests, clean-path restore evidence, and
recovery results.
