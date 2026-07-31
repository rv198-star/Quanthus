# Strategy Attribution Publication And Report Consumption

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked |
| Parent boundary | `docs/design/hld.md` attribution publication contract |
| Governing detail | `docs/design/lld/10-monitoring-attribution/strategy-attribution.md` |
| Gate source | `config/p3-gates.v1.json` |
| Implementation status | bounded synthetic publisher/catalog/Web closure accepted; formal strategy attribution and recurring reports remain deferred |

## Scope

This topic fixes the trust boundary between the Python attribution system that produces analysis
facts and the Web workbench that displays them. The HLD owns the cross-module flow; LLD 10 owns
adapter, planning, Pipeline, kernel, artifact, publication, failure, and acceptance algorithms.
This contract does not define a strategy topology or attribution method.

The stable flow is:

`strategy adapter -> immutable plan -> checkpointed Pipeline -> verified kernel claim -> closed workspace -> atomic release/catalog -> read-only Web workbench`

## Producer authority

The Python path is the sole owner of attribution calculations, evidence resolution, verification
receipts, validation flags, classifications, and the three explanation projections. Publication
accepts only a completely evaluated Pipeline result, rebuilds the kernel result from verified
bytes, stages a closed workspace, writes `CHECKSUMS.sha256`, writes `READY` last, deep-verifies the
release, and makes it visible with one atomic rename.

The catalog is derived state. It is rebuilt atomically from complete releases that independently
pass deep verification; caller-supplied catalog entries are never authoritative, incomplete staging
and symlinks are ignored, and one malformed or tampered `READY` release fails the refresh before a
new catalog becomes visible.

## Consumer boundary

The browser may discover releases, validate the supported envelope, filter and select records,
translate registered labels, render already-published values, and preserve the original JSON text.
For a published release it uses `READY` to bind the raw canonical-checksum-ledger bytes, then checks
that ledger against the exact raw release/workspace responses before fatal UTF-8 decoding and JSON
parsing of those checksummed files.

The browser cannot calculate effects, resolve evidence, issue receipts, validate semantic digests,
upgrade evidence, rank components, aggregate risk/opportunity money, or recommend policy changes.
A manual import remains unverified. Browser transport verification means the consumed bytes match a
publisher release; authoritative semantic verification remains a Python responsibility.

## Source and activation isolation

Every release binds a source kind. The bounded fixture remains
`synthetic_fixture_not_strategy_evidence`, has no formal publishable claim, and cannot be converted
to formal evidence by editing flags and resealing checksums. Attribution output is diagnostic and
never creates a signal, order, fill, execution right, policy promotion, or bypass around the
expert/veto/constraint path.

The reusable synthetic capability is accepted in
`docs/acceptance/p3-attribution-synthetic-publication-20260731.md`. Formal strategy execution and
recurring reporting remain governed by the still-deferred `P3-ATTR-001` and `P3-DASH-001` resume
conditions; synthetic acceptance does not activate either one.
