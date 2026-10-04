# Base 2.2 independent image engineering contract

| Field | Value |
| --- | --- |
| Parent | [Data foundation](../01-data-foundation.md) |
| Design status | draft |
| Delivery gate | open; synthetic engineering only |
| Owner | P10; Owner's CN02 engineering instruction, 2026-10-04 |
| Data work | #470, #471, #472, #473, #474; no issue acceptance implied |
| Contract | `config/pipelines/base22-independent-image.v1.json` |
| Code | `src/quanthus/pipelines/securities_warehouse/base22.py` |
| Tests | `tests/unit/test_base22_independent_image.py` |

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

READY is written only after that scan. Failures retain a failed-attempt receipt and any completed
history checkpoint. Repeated execution verifies sealed output without rewriting it; changed
inputs or unreadable/damaged checkpoints are refused. A job lock prevents concurrent writers.
The scan's elapsed/row/byte metrics and exact result are retained in the job attempt's
`independent-verification.json`; it does not trigger a second full scan in the same Job.
An abandoned lock requires separate inspection; this version never deletes an unknown writer's
lock. TMP files are owned by the staging root and retained for failure review. Cleanup must target
that job root under a separate explicit retention decision.

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
