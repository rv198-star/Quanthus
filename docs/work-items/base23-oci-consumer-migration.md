# OCI native Base 2.3 consumer migration

| Field | Value |
| --- | --- |
| Work item | P10-DATA-BASE23-OCI-001 |
| Design status | approved |
| Delivery gate | blocked pending the receipts below |
| Governing design | [Permanent P10 Data Line](../design/lld/12-permanent-data-line.md) |
| Machine plan | [Consumer migration](../../config/releases/base23-oci-consumer-migration.v1.json) |

At 2026-10-12 01:06 Beijing the Owner selected a complete migration of new OCI runs to native Base 2.3 through 2026-09-30, using a new source contract and input-projection baseline. This supersedes the unresolved choice in the 00:12 compatibility decision; the exact old-input compatibility-view alternative is not selected. The previously approved 22:59 maintenance window remains valid. Historical frozen contracts and research conclusions remain unchanged.

## Observable source change

The bounded 2019-03 comparison preserves every business value of the 75,450 old bars and adds nine bars for 000919, 600805 and 600810 on March 27–29. Current production projection joins include these records. For example, 000919 on March 27 changes from a null close to 7.93. Raw source revision, artifact identity and knowledge time remain visible. Nine is this bounded probe's result, not a claimed full-window difference count. Evidence is `frozen-projection-specific-delta-20261011T1610Z.json`, SHA-256 `63e56392cd7f3ef0bcbfcbee3045f53458281a7dd218e57f4292ed98ad2cc1d9` in the OCI migration records.

## New contract and correction continuation

New runtime provider, event source policy and canonical input receipt use distinct Base 2.3 identities listed in the machine plan. The old provider/source policy/canonical receipt stay unchanged. Keep retrospective source semantics, development scope, raw knowledge evidence and fail-closed exceptions; this decision does not establish historical raw publication proof or activate any research, P6, training or strategy job.

The exact original normalized-action and exception bytes are retained as an independently identified attachment. The old sealed correction keeps its original parent. A separate continuation contract binds the new native image, original correction identity and verified state preimages: 398,108 state replacement keys match the old preimage, with 428 native/old files byte-identical. Raw action/term/deletion overlays are not automatically transplanted: term comparison found 178 values different from both previous values and one absent key. Native limitation #483 and sealed `NOT_GRANTED` metadata are not rewritten.

## Implementation and acceptance boundary

Use the native inventory to resolve physical tables and validate pinned source bytes; build one bounded job-local input projection and pass it to its in-job consumers. Generate and independently verify a new canonical input digest. Admission must bind the exact native image, correction continuation, scope, policy, materializer, and new projection receipt. API submits the admitted new configuration; event and replay workers consume it. Existing API research catalog entries retain their historical provenance.

Reuse accepted native delivery/cold checks, parent and October increment closure, and correction preimage evidence. Add meaningful native authority, missing/corrupt member, correction mismatch, old-receipt rejection, protected scope and actual input-delta tests. Distribute code through GitHub and preserve each deployed worker's baseline; do not replace all worker images with an unrelated broad revision.

Before maintenance, check current jobs and leases and retain the operational rollback record. On 2026-10-12 the Owner cancelled further OCI cloud re-verification and confirmed that CN01 had already verified recoverability. This is user confirmation, not independent re-verification by this OCI line; its 5/9 checkpoint is retained as historical evidence and is not a remaining 9/9 gate. Do not contact or operate CN01. Stop the approved API/event/replay containers briefly, pause one daily-core sync and retire the old sync timer, publish the admitted release, verify actual mounts/reads/October lineage, restore services and verify the first scheduled sync. Retire recoverable old Base payload and duplicates only after the other acceptance gates, using that explicit user-confirmed recovery basis. Keep unique correction bytes and historical evidence. OCI reads cloud data directly; Mac may carry task control and small code/evidence, never data archives. CN01, Hermes and P6 remain outside this work item.


## Candidate validation, 2026-10-12

The input/runtime focused suite passed 57 tests, direct projection regression passed 6,
and the repaired planning/catalog suite passed 190. A complete check in a task-local
dependency overlay (DuckDB 1.5.5, Polars 1.43.0, BaoStock 0.9.3) completed with
3235 passed, 20 skipped and one existing frozen P3 semantic-probe failure.
The original unqualified-environment run had 3183 passed / 51 failed / 20 skipped;
22 documentation/catalog failures were repaired, and 28 version-bound failures passed
with the declared dependencies. No shared environment was changed.

The remaining probe expects daily_domain SHA-256
`9dd94a250782a259701a030cb9a1b6a6e2591ad382ec12bf478fe3b609a06368`;
both pre-migration HEAD `3d74d1f` and this worktree contain
`d21f0e1b84696a882ede67eb1d895e99020659bc0e33b2195469e3d2a63ca241`.
That frozen research contract remains unchanged and is not admitted by this migration.
The real native canonical receipt passed two independent constructions at 03:18:08 Beijing;
`scripts/qualify_native_base23_input.py` measures a candidate, removes its TMP payload,
then independently rebuilds through the normal admitted reader before retaining the receipt.
No production source flag, container, timer or old payload has been changed.


## Production image and historical-contract boundary

The retained P3 semantic-equivalence probe is dispatched only for its explicitly frozen research run configuration. The old single-security DWD conformance module, product research catalog evidence, and admitted #148 expert evaluation likewise describe historical source bindings. They are not a requirement to keep the old physical Base mounted or to rerun research during this migration. Their sealed evidence remains readable; a new native-source research request must retain its own scientific admission boundary. Generic new replay and event inputs select the independent Base 2.3 contracts.

`scripts/build_base23_consumer_overlays.py` applies only versioned native-input patches to each pinned deployed image and rejects nonmatching patches. It keeps inherited entrypoints, environment, P6 code and other deployed changes. Candidate images are built without publishing or changing running containers. Source and correction mounts are supplied read-only during separate acceptance.

The first OCI qualification at 02:55 Beijing stopped at the operator-added 2 GiB spill cap before a canonical receipt existed. No service changed. Commit `70a9bac49f72698b97e383da8cbe1fc44bfc17eb` permits bounded 6 GiB native spill and requires at least 9 GiB of temporary space for this input-only qualification. Its native tests passed (9 tests). The retry uses an isolated memory-backed TMP directory with more than 18 GiB host memory available at admission; ordinary production spill remains disk-backed within the existing worker memory limits.


## Native input accepted, 2026-10-12 03:18 Beijing

The OCI input-only qualification at `70a9bac49f72698b97e383da8cbe1fc44bfc17eb` passed. Both independent constructions produced bundle `sha256:8c84fbc03224023e82364870613394ed3d4edcb47d9451b7c3e3395004a1cc01` and provider lineage `sha256:e4af1bc4e5c097d7c17567b6e9332052bd8a9e942c4ab1829fb6584508017655`. Each contains seven input tables and 24,812,729 total rows; the daily bars, universe and execution constraints each have 8,260,825 rows. All nine known new non-null bars were observed with their raw 2026 source knowledge times retained. This is retrospective input qualification, not historical publication proof or research acceptance.

The canonical receipt SHA-256 is `bf819bfe08a149678332ece216203c352ed8463621b86698ab8ec1e52e23af51`; the full qualification record `native-input-qualification-70a9bac-20261011T1906Z.json` is `04cc9abce8b4b72ddaac85f6fff8a4a8551a8475e33f05e68ece284e4971211a`. Temporary payloads were removed. The updated native/provider/catalog/documentation focused suite passed 55 tests. Image, maintenance, first scheduled sync and retirement remain separate pending gates. The generic P4 wrapper now delegates provider and source selection to the versioned CLI defaults instead of forcing a legacy Base path.


## Consumer image defects and repair

The first candidate images were rejected before deployment. API/event overlays were copied only into `/app/src`, while the pinned images import `/usr/local/lib/python3.11/site-packages/quanthus`. The builder now verifies each source/runtime preimage and copies the same narrow changes into both locations without changing inherited environment or entrypoints. Replay strict application rejected a formatting hunk whose context included an unrelated temporary-size argument absent from the pinned replay image. The minimal physical-scan patch is AST-equivalent to every native-source semantic change, is checked against the exact replay preimage SHA, and preserves that existing replay difference. Strict patch checks remain mandatory.

The full check at `bd7aa2b24d3218259361737fbf835aeb5652b9a4` ended at 03:25:20 Beijing with 3235 passed, 20 skipped and the same isolated frozen P3 daily-domain hash mismatch; separate planning checks passed. This does not claim the entire test suite is green. No new research was run and the accepted dual native projection is reused. Defect evidence is `consumer-image-defects-20261011T1925Z.json`, SHA-256 `8aad83773c6659bbe0a6ab2f1cd71ac3ffa328ae6448af8d9707e04191e06cc1`.

The pinned event image was also found to contain DuckDB 1.5.6, while the accepted canonical input and replay image use 1.5.5. The candidate event image installs only the official arm64 CPython 3.11 DuckDB 1.5.5 wheel with SHA-256 `2e72f9e1a4f90a5c8483ad4d540e495bf0834ba61c360b52499a573d7ed62a3f`. The build verifies the downloaded wheel and installs without build network access; both worker probes require an exact match to the canonical engine version. No projection or research rerun is required.
