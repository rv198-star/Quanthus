# Base 2.4 successor input interface for P6

| Field | Value |
| --- | --- |
| Parent | [Validation governance](../08-validation-governance.md) |
| Design status | approved: Owner requested readiness items 2 and 3 |
| Delivery gate | open: engineering and source-purpose qualification are separate |
| Work items | P6-R1 / #511, P6-BASE-SEED / #514, P10 purpose qualification / #526 |
| Code | `src/quanthus/pipelines/securities_warehouse/base24_p6_handoff.py`, `src/quanthus/forward_validation/base24_inputs.py` |
| Configuration | `config/forward-validation/data/p6-base24-inputs.v1.json` |
| Contract tests | `tests/unit/test_p6_base24_inputs.py` |

This is an opt-in successor input interface. It preserves the frozen P6 runtime,
entry proposal, activation history, access ledger and prior failed official jobs.
It does not activate a CN02 worker or count a historical day as a prospective day.
The old Base 2.1/correction/exact-prior Daily reader remains unchanged.

## Ownership and dependencies

P10 owns the one source materialization and source-purpose assessment. P6 verifies
and consumes the sealed handoff. No candidate scanner, feature evaluation, paper
order, strategy result or research repair runs as part of this interface.

The stage graph is: verify the exact prepared Base 2.4 + selected CN02 Daily
projection and one explicitly selected native security-status member; isolate
2026 SSE/SZSE observations and overlapping reference intervals; reconstruct all
seven table meanings; assess original knowledge versions; seal; independently
verify; let the consumer bind the exact manifest. One immutable job-local source
closure serves every consumer. It is governed source handoff data, not new ADM.

The manifest binds code revision, original source/member identities, exact Daily
selection, original prior revisions and the explicit revision transitions. A
transition is not an equivalent legacy chain. The handoff contains its own native
observations, evidence, contract and assessment; source paths are never runtime
fallbacks. READY is written last. Existing output is never overwritten.

## Scope and purpose

Only 2026 SSE/SZSE daily market observations are physically delivered. Reference
intervals may start earlier when they overlap the permitted period; that is not
permission to read earlier market observations. Source knowledge/effective times,
Decimal values, precision, null/zero and unavailable states remain unchanged.
Actual build time is a separate manifest field. No 2025 market observation is
exposed to the P6 reader.

The assessment separates original session-close visibility (15:00 Shanghai,
the strict historical source diagnostic inherited from #526) from availability
at the declared later snapshot cutoff. It examines every retained knowledge
version, including exclusive `knowledge_to`, and requires unambiguous coverage.
This diagnostic does not redefine the formal cycle's decision cutoff or invent
provider acquisition times. The 120-session census is a source history check;
61/22-session feature/candidate rules still belong to the frozen P6 consumer.

`source_diagnostics` permits safe source engineering. The two context flags report
market-context availability, not execution authority. This v1 interface does not
contain an execution-status/IPO qualification or company-action authority grant:
`historical_replay` and `paper_entry_source` remain false. Neither zero exceptions
nor changed booleans in a repacked file can establish that grant. Missing source
qualification stops only the corresponding use; unrelated qualified data remains
readable for its declared purpose. Native status/regime row counts alone do not
prove a tradable state, and action terms alone do not prove accepted normalization
or factor agreement.

## Consumer API and failure semantics

`open_base24_inputs(root, manifest_sha256=...)` performs offline verification and
returns an immutable exact-identity binding. `iter_batches(dataset, purpose=...,
batch_size=...)` reads only seven known datasets in batches of at most 65,536 rows.
It rejects a purpose that did not pass, changed files and unknown dataset names.
`historical_bundle()` is the existing frame-reader integration point and refuses
to return a capability before its historical replay qualification passes.

Offline verification checks the exact physical file set, hashes, schemas, row and
key census, calendar/date/venue relationships, source closure and recomputed
assessment. It reconstructs all seven projections from the local native records,
so a fully repacked manifest cannot hide altered values or invented authority.
No network, original Base, Daily, correction, old path or source cache is needed.

## Acceptance and next boundary

Tests cover known/unknown clocks and original earlier versions, Shanghai dates,
physical exclusion of BSE/2025 observations, Decimal/time preservation, purpose
rejection, schema/value tampering, mutation after open and corrupt/missing files.
A real handoff must additionally supply safe subset counts, untouched-source
proof, exact source/Daily identities and independent source-free cold receipts.

Passing this module's checks establishes input engineering and the reported
source-purpose findings. Formal admission still needs the missing P10 purpose
qualifications, P6 consumer acceptance and the separate node/runtime activation,
rollback, legal future-cycle and independent reproduction steps. No frozen
cutoff, observation budget, policy or visibility exception is relaxed here.
