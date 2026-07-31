# META-DATA-BASE-003 Operational ADS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Evidence date | 2026-07-31 UTC |
| Evidence status | Phase A provisional Base accepted; Phase B final Base remains open |
| Policy | `config/operational-ads-base.v1.json` |
| Publication authority | OCI provisional Base package and administrative descriptor published; consumer authority unchanged |

## Verdict

Phase A accepts the immutable OCI package `ads-base-through-20260730-v1` and its administrative
`BASE_CURRENT.json`. The package binds the exact accepted DWD/DWS v2 manifests and corrective
semantic source v2, preserves all ten source table files byte-for-byte, passes seal verification
and isolated one-day replay, and remains `research_only=true` and
`execution_authorized=false`. See `provisional-base-acceptance-20260731.md`.

The read-only preflight found four immutable operational daily ADS revisions and ten retained
portable research exports. Portable exports remain derived, non-authoritative snapshots and their
bootstrap PIT declarations do not prove per-decision publication. They are evidence inputs, not
ready-made authoritative Base versions. See `inventory-preflight-20260730.md`.

This record does not accept the final `ads-base-202607-v1`. Phase A changed only the operational
ADS Base package root and its administrative descriptor. No release set, schedule, existing ADS,
portable export, upstream Base, consumer pointer, execution authority, or `GO_FOR_CUTOVER` changed.
No guessed path or digest was substituted for an exact accepted input.

## Evidence map

| Evidence | Claim |
| --- | --- |
| `provisional-base-acceptance-20260731.md` | Real Phase A build, replay, publication, and pointer evidence |
| `repository-capability-20260730.md` | Code/config/CLI/operator/test boundary implemented |
| `inventory-preflight-20260730.md` | Existing ADS/export objects measured and publication blockers identified |
| `docs/design/lld/01-data-foundation/operational-ads-base.md` | Approved package, role, replay, and failure semantics |
| `docs/operations/dual-lane-data-platform-runbook.md` | OCI-only exact-input publication sequence |

## Open gates

- After the July 31 close, bind the final upstream manifests and accepted July 31 daily revision,
  build a new `ads-base-202607-v1`, and rerun every provisional gate.
