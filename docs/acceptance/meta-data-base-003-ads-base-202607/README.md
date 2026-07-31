# META-DATA-BASE-003 Operational ADS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Evidence date | 2026-07-31 UTC |
| Evidence status | Phase A provisional Base and Phase B final Base accepted |
| Policy | `config/operational-ads-base.v1.json` |
| Publication authority | OCI final Base package and administrative descriptor published; consumer authority unchanged |

## Verdict

Phase A accepts the immutable OCI package `ads-base-through-20260730-v1` and its administrative
`BASE_CURRENT.json`. The package binds the exact accepted DWD/DWS v2 manifests and corrective
semantic source v2, preserves all ten source table files byte-for-byte, passes seal verification
and isolated one-day replay, and remains `research_only=true` and
`execution_authorized=false`. See `provisional-base-acceptance-20260731.md`.

Phase B accepts the separate immutable `ads-base-202607-v1` through 2026-07-31. It binds the exact
final DWD/DWS pair and July 31 operational daily ADS, reproduces the two independently verified
candidate table sets byte-for-byte from a clean pushed revision, passes seal and bounded replay
verification, and atomically advances only the administrative Base descriptor. See
`final-base-acceptance-20260731.md`.

The read-only preflight found four immutable operational daily ADS revisions and ten retained
portable research exports. Portable exports remain derived, non-authoritative snapshots and their
bootstrap PIT declarations do not prove per-decision publication. They are evidence inputs, not
ready-made authoritative Base versions. See `inventory-preflight-20260730.md`.

Neither acceptance changes a release set, schedule, retained ADS, research bundle, upstream Base,
consumer pointer, execution authority, or `GO_FOR_CUTOVER`. No guessed path or digest was
substituted for an exact accepted input.

On 2026-07-31, a separately approved naming-only migration atomically renamed the derived offline
bundle store from `/srv/quanthus/portable-ads` to `/srv/quanthus/research-bundles`. It changed no
bundle bytes, manifests, roles, Base package, or authority. Historical evidence retains the path
that existed when it was recorded. See `research-bundle-root-migration-20260731.md`.

## Evidence map

| Evidence | Claim |
| --- | --- |
| `provisional-base-acceptance-20260731.md` | Real Phase A build, replay, publication, and pointer evidence |
| `final-base-acceptance-20260731.md` | Final exact-input rebuild, replay, publication, and pointer evidence |
| `repository-capability-20260730.md` | Code/config/CLI/operator/test boundary implemented |
| `inventory-preflight-20260730.md` | Existing ADS/export objects measured and publication blockers identified |
| `research-bundle-root-migration-20260731.md` | Offline bundle-store rename, identity preservation, and role-access evidence |
| `docs/design/lld/01-data-foundation/operational-ads-base.md` | Approved package, role, replay, and failure semantics |
| `docs/operations/dual-lane-data-platform-runbook.md` | OCI-only exact-input publication sequence |

## Remaining authority gates

- Consumer-visible release-set publication, strategy activation, schedule changes, and production
  cutover remain outside this Base acceptance and require their own evidence and authority.
