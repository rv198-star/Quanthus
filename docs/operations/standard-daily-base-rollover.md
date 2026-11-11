# Standard Daily and Base rollover

Parent: [Standard Base operations](base-build.md). Work item: P10-DATA-BASE25-001,
[#600](https://github.com/rv198-star/Quanthus/issues/600). Governing implementation:
standard_daily.py, daily_base_alignment.py and daily_aligned_runtime.py.
Tests: test_standard_daily.py and test_daily_aligned_runtime.py.

CN02 owns the scheduled producer and all source/product storage. Hugging Face
stores native DWD Parquet files for remote selective reads. Google Drive stores
sealed backup objects. Upload completion is independent of Daily production.

The sealed Base 2.5.1 is the current baseline, through 2026-10-31. Its native
dual-revision proof is read directly. No synthetic Base wrapper is required.

## Repeatable sequence

1. Select the current sealed ODS/DWD and an explicit cutoff. Consume exactly one
   authoritative standard delivery per calendar date after the Base cutoff.
   Keep market and closed dates; preserve actual source clocks and bytes.
2. Use `python -m quanthus.pipelines.securities_warehouse.base_build_standard_daily` (the standard Base build adapter) to derive
   selection/roots/config from these delivered sources. Freeze, build, audit and
   seal the next paired Base through the requested cutoff.
3. Run `ops/bin/align-daily-base build --base NEW_SEALED_DWD --source-runtime
   CURRENT_RUNTIME --through LATEST_COLLECTED_DATE --output NEW_RUNTIME`.
   Dates at or before the new cutoff are already in Base and are excluded.
   Remaining dates replay their exact indexed sources, without recollection.
4. Require NEW_RUNTIME/VERIFIED.json. This records cold replay, complete date
   coverage and independent business-value equivalence checks.
5. Copy the generated governance.json into a new immutable governance directory.
   Run `ops/bin/publish-aligned-daily --alignment NEW_RUNTIME --cycle-template
   OLD_CONFIG --cycle-output NEW_CONFIG --pipeline-revision FULL_SHA --governance
   IMMUTABLE_GOVERNANCE_JSON`. This publishes cycle-compatible date authority,
   copies original source catalogs and source releases, and points native sources
   to independent copies already retained in the aligned projection.
6. Build a new immutable image from the verified runtime image and a tracked Git
   source bundle. Mount the exact Base, governance directory and NEW_RUNTIME.
   Switch only daily/recovery service instances while idle; retain previous
   config/image and verify effective unit, mounts, hashes and actual reader.
7. Scheduled market/closed deliveries use the same standard delivery publisher.
   Consumer admission and strategy execution remain separately governed.

The cycle kind and compatibility pointer filenames retain their historical
BASE24 names. Actual Base identity comes from the manifest hash in config,
index, core, projection and standard delivery. Weekly configuration is independent.

## Upload order and completion

HF accepts native DWD paths, never tar chunks. Verify an immutable Hub commit
against the full expected source set, including root manifest.json and READY.
Use LFS SHA256 or Git blob identity as supplied by the Hub. Local hashes are
checked against the sealed manifest. Source bytes and remote member sets must
match exactly.

After HF verifies completely, resume GDrive from existing object/session
checkpoints. Do not delete the verified ODS objects or discard a valid session.
Record source bytes and compressed transfer bytes with separate denominators.
