# Base 2.2 independent image engineering contract

| Field | Value |
| --- | --- |
| Parent | [Data foundation](../01-data-foundation.md) |
| Design status | draft |
| Delivery gate | open; synthetic image engineering and authorized real source assessment |
| Owner | P10; Owner's CN02 engineering instruction, 2026-10-04 |
| Data work | #470, #471, #472, #473, #474; no issue acceptance implied |
| Contract | `config/pipelines/base22-independent-image.v1.json` |
| Code | `src/quanthus/pipelines/securities_warehouse/base22.py`, `base22_sources.py` |
| Tests | `tests/unit/test_base22_independent_image.py`, `test_base22_real_sources.py` |

This additive construction contract implements an independent physical candidate through
2026-08-31 by business date in Asia/Shanghai. The accepted July model, extension and production
builders remain frozen. The exact table set and column types come from the checksum-pinned
33-table model and its additive extension. The candidate has a separate schema-v2 kind and
`consumer_admission=not_granted`; it is not a legacy CURRENT or P6 input.

## Construction boundary

The DAG is freeze/validate -> repaired history -> August typed projection/merge -> seal ->
independent scan. The v1 entry accepts **synthetic typed domain projections only**. It does not
accept existing Daily packages or run provider/ODS transformations. Actual raw-to-domain adapters,
source admission and evidence qualification remain prerequisites of a separately authorized real
execution contract. A caller's `purpose` string cannot enable real execution in this version.

The separately authorized CN02 second stage may read retained real ODS/DWD for P10 quality
assessment. `base22_sources.py` adapts the actual July ODS content inventory, July DWD physical
inventory, daily/weekly ODS release schema and legacy correction inventory without rewriting
their contracts. It verifies original manifest bytes, READY, every member hash/size, physical
file sets and every Parquet footer. The resulting assessment is **not** an admitted image plan:
schema/count integrity cannot establish row semantics, source qualification, repair completion
or whole-domain August coverage. Each missing prerequisite continues to block its affected step.

The versioned assessment entry is:

```sh
PYTHONPATH=src .venv/bin/python -m quanthus.pipelines.securities_warehouse.base22_sources \
  --selection /absolute/path/to/frozen-selection.json --output /new/isolated/assessment-run
```

The selection has `schema_version=1`, `purpose=p10_base22_source_assessment`, the exact
`image_contract_sha256`, absolute `source_roots`, and `packages` with a unique safe `label`,
absolute `root` (inside a frozen source root),
`manifest_name` (`manifest.json` or `release.json`) and original `expected_manifest_sha256`.
All source/output/selection path and symlink checks precede directory creation. Each invocation
recomputes payload identities, keeps a failed package's evidence and continues independent
packages. Existing output is never overwritten. Assessment results bind evaluator bytes and
runtime, grant no candidate/consumer admission, and never write READY or a production pointer.

Bounded adapter primitives compare rolling announcements by `(stock_code, ann_id)` and business
body while retaining acquisition observations; conflicting revisions are not silently collapsed.
The retained Baostock tail adapter requires its exact raw envelope/hash, unique dated keys,
unadjusted flag and lossless Decimal(38,10) decoding. It preserves null/zero/status fields and
does not invent missing acquisition or knowledge time or historical accepted-child authority.
The governed `none` to `UNADJUSTED` daily-basis adapter leaves every other Arrow column unchanged,
including Decimal precision and timestamp instants; unknown daily bases fail closed. These
primitives do not implement every domain's real projection or admit corporate-action evidence.

Real execution also requires a measured resource gate. The existing synthetic SQLite engine
stores a full canonical document per row and builds merge/verification indexes. Its minimum
document-byte floor can be computed from the pinned schemas and verified footer census without
sampling prices. Failure of even that conservative floor blocks using this engine for the real
corpus. A partition-bounded immutable history implementation and independent verifier are still
required; healthy partition reuse must preserve exact bytes/provenance inside the new image.

Each source binds an exact manifest SHA, READY, role, authority and declared business cutoff.
Its manifest inventories typed Parquet projections and their raw evidence members by full hash
and size. Rows must bind a source/revision/hash triple in their own source's verified closure.
Historical sources retain July declarations; August inputs have a separate role. The input plan
binds all sources, code revision, per-table source selection, exact partition row/key census,
August projection/no-change evidence and all five quality-gate dispositions. Empty or absent
evidence never means no required changes. Nonempty artifacts without a handler block construction,
including the known seven-row dividends_delta case.

The contract lists every table's source families, business and knowledge-version keys, cutoff
field, partition rules, merge priority, reuse/rebuild reasons, coverage and missing disposition.
The concrete job freezes its exact required partitions and expectations before scanning rows.
Foreign keys require referential closure and containment by effective/knowledge intervals.
The shared build/cold scan checks daily market bars and `dwd_tradability_state` against the
referenced calendar's Shanghai business date. Tradability state has no `interval_size`; its
`observation_start` date must match `trade_date` independently of that field.
Where the model also declares a listing reference, the calendar venue must match
a temporally visible referenced listing. Nullable index-calendar references remain nullable;
this check does not prescribe common open/close windows for events or other tables.
Unknown knowledge fails closed; known late timestamps remain late. Effective interval endpoints
are retained even when they extend beyond August. Build time is recorded separately.

Repaired history is materialized once into an immutable job-local SQLite stage (`TMP`), indexed
by complete version key. All August inputs bind its recomputed semantic digest. The same stage
feeds the merge; no final August image is used as an August dependency. Arrow batches and the
SQLite disk index bound memory. This is job-local reuse, not a cross-job cache or governed ADM.
The identity includes the complete plan, source manifest identities, pinned model/extension,
contract and evaluator bytes. Formal independent construction uses a fresh output and staging
root; checkpoint reuse is never evidence of independent construction.

Identical rows from distinct rolling releases deduplicate; duplicate keys inside one source
fail. Different rows with the same version key require an exact repair authority binding the
before/after row digests and replacement source. Other knowledge versions are retained, and
overlapping knowledge intervals fail. Price-basis normalization requires the already versioned
P10 alias contract; Decimal precision, all other fields and original time instants are retained.
Zero/null/unavailable and late statistics remain explicit. Missing OHLCV is never manufactured.

## Image, failures and cold verification

The image contains its own typed partitions, pinned schema contracts, input plan, stage metrics,
source manifests as archival objects, hashes and recomputable semantic summaries. Files are
physically written; no hard links, symbolic links, runtime external views or source fallbacks
are permitted. The verifier never opens an archived source root or requires ODS/correction/raw
payloads to be available. It scans every table, checks its schema, all byte hashes, exact physical
file/table/partition sets, counts, key uniqueness/census, temporal FK closure and semantic digests.
Calendar and per-table August qualification evidence is copied into the image and bound to
the exact archived raw-member identities. Both entry paths use the same offline plan validator:
scope, quality dispositions, source roles/selections, partition census and August evidence remain
mandatory after relocation. Row lineage is admitted only from that table's own `source_ids`.
Zero-row Parquet files and directories participate in the physical partition comparison; a
genuinely empty table has exactly its declared `empty/part-00000.parquet` partition.

READY is written only after that scan. Failures retain a failed-attempt receipt and any completed
history checkpoint. Repeated execution verifies sealed output without rewriting it; changed
inputs or unreadable/damaged checkpoints are refused. A job lock prevents concurrent writers.
The scan's elapsed/row/byte metrics and exact result are retained in the job attempt's
`independent-verification.json`; it does not trigger a second full scan in the same Job.
An abandoned lock requires separate inspection; this version never deletes an unknown writer's
lock. TMP files are owned by the staging root and retained for failure review. Cleanup must target
that job root under a separate explicit retention decision.
Before any mkdir, lock, attempt or identity write, both the output and sibling `.<output>.job`
must be physical paths without symbolic-link ancestors and disjoint in both directions from
every source root. A valid existing staging directory retains normal checkpoint reuse.

Tests copy and relocate the synthetic image twice, remove its disposable input/staging roots,
and verify in a new isolated Python process with source-network and old-root reads prohibited.
Corruption and deletion tests affect synthetic copies only. Passing these tests establishes
engineering behavior, not historical coverage, repair completion or consumer acceptance.

## CN02 running and outstanding gates

Run engineering checks in the isolated checkout with its declared development/P1/ODS dependencies:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_base22_independent_image.py
.venv/bin/python -m ruff check src/quanthus/pipelines/securities_warehouse/base22.py tests/unit/test_base22_independent_image.py
make check PYTHON=.venv/bin/python
```

`build_base22(plan=..., config_root=..., output=...)` is a pointerless synthetic API. For an
already sealed candidate, `verify_base22(image_root)` needs only the image and installed ordinary
code. There is no real-build, promotion, publication or synchronization CLI in this increment.

Before a real source verification/build, approve only exact CN02 source roots and manifest/member
identities, necessary read access, permitted row/date scope and a bounded isolated output/resource
budget. Source admission must prove the authoritative August calendar/all-domain census,
weekend/rolling event keys, nonempty dividends handler and weekly closure; 21 daily packages
alone are insufficient. Qualify #470's native 5,201 and exact 50 exceptions/dated aliases;
#471's original normalization manifest/full members or explicitly authorized new revision;
#472's raw -> admitted source -> output key/hash and state/gap evidence; #473's complete numeric,
basis and clock preservation; and #474's field-specific availability with unknowns unavailable
and all 4,173 frozen factor exceptions preserved. Historical validation receipts are not files.

The local audit report path was permission-denied during this task. No payloads were read and
none of those real gates are declared passed. P3/P5/P6 admission remains separate.

After qualification, a new authorized execution contract can enable bounded real adapters and
isolation on CN02. Release, node synchronization and pointer changes require separate approval:
compare exact image digests, independently cold-verify the transferred physical copy, retain the
old production package and pointer digest, then use the existing release/CAS/rollback gates.
CN01 remains the baseline throughout preparation. September Daily packages must subsequently
bind the exact accepted Base 2.2 manifest/content identity; they are not a dependency of reading it.
