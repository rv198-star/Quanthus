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

Owner requested P6 startup on 2026-10-22. The explicit successor cycle adapter,
runtime binding, empty-action frame compatibility and CN02 startup gates are
tracked in [P6-R1](../../../work-items/p6-cn02-start.md). Its additive runtime
does not activate official admission or inherit the old OCI activation by itself.

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
Market reconstruction shares one monthly join across three outputs; each native
family shares one bounded version table across its two clock checks. No year-wide
wide-row difference or twelve-way retained join is required.
No network, original Base, Daily, correction, old path or source cache is needed.
Bounded DuckDB spills use a fresh disposable directory outside the immutable
handoff (`scratch_root` or the ordinary temporary directory). Scratch inside the
image is rejected; the directory is removed after the check and is never a cache.

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

## Native source-purpose successor

`base24_p6_purpose.py` and
`config/forward-validation/data/p6-base24-purpose.v1.json` own a separate
`quanthus.p10-base24-p6-purpose-qualification.v1` source qualification. It embeds
the immutable diagnostic handoff, the exact original ODS IPO-rule member, and
the byte-pinned existing price-limit v2 and corporate-action normalization v3
policies. It does not rewrite the strict diagnostic's negative historical result.

One monthly market join serves later-context and execution-source checks.
Canonical and provider status conflicts remain held; UNKNOWN never implies
NORMAL. Provider status must have an exact dated raw observation and a real,
timezone-aware acquisition time visible at the declared snapshot. An old open
interval does not attest that status remained unchanged on a later day. The
original ODS inventory and selected status members travel inside the handoff;
equal-time conflicting observations remain ambiguous. This qualification does
not supply future Daily visibility evidence. IPO duration comes from the
original interval id and native source hash.
Complete provider-session history is required inside an initial window; a proven
lower-bound session count can establish exit from that window. Unsupported or
absent rules, incompatible native bounds, unknown identities and policy
exclusions hold only the affected keys. Native state and prices are never flipped
or repaired by this consumer. Event-day execution stays held without an exact
action reference price.

When both native price bounds are null, a separate pair of qualified bounds may
be derived from the pinned policy, exact dated status, qualified IPO rule and
actual previous provider-trading close. Original nulls remain physical source
values. A nonnull or partially populated native pair that disagrees remains
held; no source value is overwritten. Missing reference prices are not filled.

Actions keep native per-share currency and explicit share ratios. Alternatives,
unknown terms, same-event ambiguity and unavailable knowledge remain held.
Implemented terms are checked against original interval factors and the actual
prior close using the existing Decimal factor tolerance. These later-snapshot
findings do not recover historical publication, an old normalization manifest or
an authority grant. An exact factor match is source evidence, not paper authority.

Evidence identities canonicalize only the unordered native status, lifecycle,
regime and dated-state collections, and native action terms. All typed values,
precision and duplicates remain represented. Database join traversal order cannot
change the identity; changed observations still change it. Cold verification
compares the complete recomputed outputs, including these identities.

`open_base24_qualified_context` verifies the successor offline and requires an
explicit aware decision cutoff at or after the real source snapshot cutoff. It
exposes qualified unadjusted warmup rows and separate state/action qualifications
in bounded batches. This API does not create a historical replay bundle, an
official Daily visibility receipt or an activation. The old v1 consumer and
frozen runtime are unchanged. Future formal integration must bind the exact
qualified scope, satisfy the frozen feature/action gates and record its own
consumer acceptance and node/runtime activation.

## Bounded P6 runtime consumer

`forward_validation/base24_runtime.py` adds a separate, opt-in runtime consumer
for the qualified context. It is a P6 consumer implementation, not another P10
repair pipeline. Its input stage graph is: reuse one unchanged qualified source;
verify the exact target native Daily and original dated provider-status release;
qualify only that target's execution conditions; select the complete 61-session
feature window and retain the full per-listing disposition; seal one portable
input; share it between the existing feature engine and candidate-input reader.
The 22-session numeric candidate rule and its 120-session listing-age requirement
remain unchanged. Listing age is an observed lower bound from the admitted 2026
listing census, not the length of the 61-row feature window.

The original concentration mapping stays byte-identical inside the closure.
A separate mapping binds its exact stock-code/market memberships to the dated
native listing and security identities. It preserves industry values, source
retrieval time and static-concentration-only use. Missing or ambiguous identities
hold the affected input; there is no historical industry or alias backfill.
Every implemented action in the consumed feature window holds that security
under this conservative consumer scope. Empty normalized-action tables describe
only the retained action-free window, never market-wide action completeness.
A held target can remain available as context without authorizing a new entry
or forcing a sale. All held listings and reason counts remain explicit.

`open_runtime_inputs` checks the exact physical closure, hashes, schemas,
Decimal columns, keys, full window, calendar/market relations, source knowledge,
concentration bridge, candidate values and target entry flags offline. Original
source paths are not opened. `context_bundle()` and `materialize_feature_panel`
without a clock are read-only input acceptance. They grant no historical event,
prospective observation or order authority. `bundle(clock)` and
`candidate_series(clock)` require the exact target and actual input availability.
The latter streams only the qualified current-entry scope into the existing
numeric scanner; native knowledge instants and Decimal values remain intact.

The opt-in clock is `quanthus.p6-after-close-decision-clock.v1`. It records T's
15:00 observation end, real materialized-input availability and actual decision
as separate instants. Availability cannot be backdated to the source snapshot's
knowledge cutoff. The existing conservative next-calendar-day 09:00 seal guard
remains; it never asserts that the next calendar day is a trading session. An
optional next opening requires an authoritative binding. Actual next-open fills
still wait for the next admitted READY trading session.

`EventRecord` supports this clock only as an explicit `prospective_clock` field
on a prospective event. Legacy serialization, identity and the legacy T-close
knowledge check remain unchanged. The scanner's opt-in policy validates the
bound target and actual decision deadline; the official decision adapter reads
the record's decision instant. These code paths are not a mandate, deployment
or activation of the frozen Base 2.1 runtime. A successor runtime/source binding,
its own activation decision and a legally timed future Daily remain necessary
for official evidence. A past Daily may pass data acceptance and still fail
prospective timing. Never count a read-only acceptance run as an official cycle.

Regressions: `tests/unit/test_p6_base24_runtime.py`, existing event-contract and
scanner tests, and the official-cycle tests. Real acceptance records must retain
safe scope/hold counts, exact input and code identities, unchanged-source proof,
two independent source-free processes and missing/corrupt-copy failures.

## Read-only CN02 runtime projection

`forward_validation/base24_contract.py` owns the canonical repository-only
successor runtime loader. The Worker re-exports that loader through
`base24_cycle.py`; the lean API imports it directly without market readers,
DuckDB, PyArrow or a queue controller. Frozen policy bytes and runtime schemas
are unchanged by this shared loader.

The API explicitly selects this route with all three environment variables:
`QUANTHUS_P6_RUNTIME_CONFIG`, `QUANTHUS_P6_FORWARD_RUN_ROOT`, and
`QUANTHUS_P6_EXPECTED_WORKER_REVISION`. They identify the Base 2.4 runtime JSON,
the absolute read-only P6 run root, and the full expected Worker Git SHA.
Partial configuration fails closed. With none configured, the existing legacy
projection retains its own contract.

`product_view.py` reads only `activation/base24-enabled.json` under that run
root and uses the canonical activation validator. It rejects symbolic paths,
future decisions and a different Worker revision, and never inherits the old
activation pointer or owner mandate. Missing, disabled and paused admission are
shown without granting execution. Official progress is scoped to the exact
runtime and activation digest; earlier activations remain in the ledger and
are not counted as successor observations. A 200-Job census limit fails closed
rather than silently reporting a truncated count. An exact-revision disabled
rehearsal can be shown, but contributes no official sessions.

This read-only display does not prove deployed readiness, verify market payloads
or grant activation. Those still require the corresponding runtime receipts.
Regression coverage is in `tests/platform/test_p6_base24_product_view.py`, with
the original product-view, runtime and controller suites retained.
