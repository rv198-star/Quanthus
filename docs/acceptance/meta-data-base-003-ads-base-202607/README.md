# META-DATA-BASE-003 Operational ADS July Base Evidence

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Evidence date | 2026-07-30 UTC |
| Evidence status | repository capability and read-only inventory preflight complete; Phase A and Phase B not accepted |
| Policy | `config/operational-ads-base.v1.json` |
| Publication authority | unchanged; no Base package or descriptor was published |

## Verdict

The repository now has the bounded machinery required to build and consume one immutable,
OCI-authoritative operational ADS historical Base from an exact plan. Synthetic contract evidence
proves deterministic table materialization, deep verification without source paths, role
isolation, replay-loader adaptation, failure containment, immutable promotion, and a locked
descriptor compare-and-swap. See `repository-capability-20260730.md`.

The read-only preflight found four immutable operational daily ADS revisions and ten retained
portable research exports. Portable exports remain derived, non-authoritative snapshots and their
bootstrap PIT declarations do not prove per-decision publication. They are evidence inputs, not
ready-made authoritative Base versions. See `inventory-preflight-20260730.md`.

This record does not accept `ads-base-through-20260730-v1` or `ads-base-202607-v1`. No exact
published `dwd-base-through-20260730-v1` and `dws-base-through-20260730-v1` manifest family was
available for a valid plan, and the July 31 close/final generation had not occurred. No guessed
path or digest was substituted; no `BASE_CURRENT.json`, release set, schedule, existing ADS,
portable export, upstream Base, or consumer pointer changed.

## Evidence map

| Evidence | Claim |
| --- | --- |
| `repository-capability-20260730.md` | Code/config/CLI/operator/test boundary implemented |
| `inventory-preflight-20260730.md` | Existing ADS/export objects measured and publication blockers identified |
| `docs/design/lld/01-data-foundation/operational-ads-base.md` | Approved package, role, replay, and failure semantics |
| `docs/operations/dual-lane-data-platform-runbook.md` | OCI-only exact-input publication sequence |

## Open gates

- Freeze a complete exact plan after the real Issue #48 provisional DWD/DWS packages exist.
- Resolve every portable object and table to a final reuse, rebuild, exclude, or evidence-only
  decision with exact source lineage and semantic comparison evidence.
- Build `ads-base-through-20260730-v1` from a clean reviewed commit, repeat it identically, run the
  clean-path bounded replay smoke, measure resources, and only then advance the Base descriptor.
- After the July 31 close, bind the final upstream manifests and accepted July 31 daily revision,
  build a new `ads-base-202607-v1`, and rerun every provisional gate.
