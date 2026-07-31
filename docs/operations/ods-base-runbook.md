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
| Policy | `config/pipelines/ods-base.v1.json` from one reviewed commit |

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

## Final July Base

The close-release gate is expected to become runnable only after the final 5m job completes. Run it
independently on Hermes and CN01 using each node's release root:

```bash
ops/bin/check-ods-base-release-gate \
  --policy config/pipelines/ods-base.v1.json \
  --release-root /root/work/astock/data/shadow/hermes-ods-v1/releases \
  --base-current /srv/quanthus/ods/base/BASE_CURRENT.json \
  --market-date 2026-07-31 \
  --expected-parent-bundle-id ods-base-through-20260730-v1 \
  --expected-parent-manifest-sha256 sha256:d38b8332ddd5427104bbc6f71bf89df1c81f85cca507a1f9a80ac58662b7352e \
  --expected-parent-through-date 2026-07-30
```

CN01 substitutes `cn01-ods-v1` in `--release-root`. The command verifies the exact provisional
Base pointer, market `CURRENT`, the latest eligible event/1m/5m releases, every artifact checksum,
and free space. Its result deliberately sets `authorizes_base_promotion=false`: passing this gate
authorizes freezing the Hermes inventory and constructing a final candidate, not publishing it.

After both nodes pass, freeze a new Hermes inventory through July 31 and build
`ods-base-202607-v1` with
`--parent-base /srv/quanthus/ods/base/ods-base-through-20260730-v1`. Re-run the provisional build,
deep-verification, clean-path, and identical-input gates, then stage and deep-verify the candidate
on CN01.

Final promotion remains blocked until Issue #47's expanded Phase B gates are accepted: standard
`base/daily/weekly` publication, a checksum-bound node-local ODS catalog, the governed recurring
source gaps or an explicit blocking disposition, the coverage matrix, and one normal daily and
weekly cycle on both nodes. Once those gates are accepted, each final promotion must pass
`--expected-current-manifest-sha256 <provisional-manifest-sha256>`; a missing or changed prior
pointer blocks the compare-and-swap.

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
