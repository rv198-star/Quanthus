# Data & Indicator Catalog post-acceptance audit — 2026-09-07

| Field | Value |
| --- | --- |
| Audit scope | `P3-DICT-001` Data & Indicator Catalog after initial production acceptance |
| Baseline | `e9d1feba5277faa0a4eb2651ea0fa9a46e023458` |
| Repository audit disposition | PASS after bounded repairs |
| Production re-deploy / Web-image GC | PENDING at this checkpoint |
| Trading / formula authority change | NONE |

## 1. Review method

The review re-read the governing Catalog design, Structure blueprint, semantic catalog, Catalog
builder/query service, Platform API/RBAC, `/catalog` UI, production image build path and focused/full
contract tests. It treated existing acceptance files as point-in-time evidence rather than rewriting
old 664-entry / 8-gap results.

## 2. Findings and repairs

### CATALOG-AUD-001 — non-equivalent links suppressed executable-spec gaps

The v1 builder counted `related_not_equivalent` legacy links as if they established an executable
specification for the target indicator concept. This caused `indicator.trend.sma` and
`indicator.oscillator.rsi` to omit `executable_spec_missing`, even though their legacy Feature specs
are explicitly not equivalent to the generic concepts.

Repair: only an explicit `exact_equivalent` executable relationship may suppress the missing-spec
gap. SMA and RSI now remain discoverable through their non-equivalent legacy links while also
showing that no generic executable spec has been accepted.

### CATALOG-AUD-002 — Structure blueprint implementation state was not projected

`structure-facts-v2-blueprint.v1.json` was digest-bound as a Catalog source but its B3-B5 accepted
runtime state was not consumed. Consequently all Structure semantic concepts appeared
`declared_only / contract_only`, including concepts actually emitted and accepted by the bounded
Facts v2 runtime.

Repair: the blueprint now owns an explicit seven-concept `semantic_runtime_bindings` list. The
Catalog projects `implemented / accepted` only for those exact bindings and only when their
capability row remains `accepted_bounded_v2 / real_dwd_ab`. No runtime status is inferred from names,
source imports or prefixes. The naming registration itself remains a separate axis.

Bound concepts are:

- `structure.anchor.rolling-high`
- `structure.anchor.rolling-low`
- `structure.geometry.signed-distance`
- `structure.interaction.close-cross-above`
- `structure.interaction.close-cross-below`
- `structure.interaction.close-reclaim`
- `structure.episode.reclaim-sequence`

### CATALOG-AUD-003 — B8 blueprint status was stale

The Structure blueprint still described the dictionary as a planned/separate backlog after
`P3-DICT-001` had been accepted in production.

Repair: B8 now records the accepted Catalog v1 while preserving the claim ceiling that many reserved
indicator concepts still lack executable specs.

### CATALOG-AUD-004 — Web status filter was incomplete

The backend accepted the complete lifecycle/implementation/verification vocabulary, but the UI
status selector omitted several valid states including `implemented` and `source_gap`.

Repair: the selector now exposes the complete supported status vocabulary; API semantics are
unchanged.

### CATALOG-AUD-005 — obsolete production Web images had no bounded retention path

Repeated production Web deployments can leave historical `quanthus-platform-web:git-*` images.
The general DevSpace Docker daemon is separate from the rootful Quanthus production daemon, so a
rootless prune does not address this inventory.

Repair: the governed Web build path now runs a repository-scoped image retention script. It retains
the target release tag and every exact image reference used by any running or stopped container,
removes only older unreferenced `quanthus-platform-web:git-*` tags, never uses `--force` or global
`docker image prune`, and never touches volumes or other repositories. Actual rootful cleanup is
pending the next governed Web deployment at this checkpoint.

## 3. New deterministic projection

The Catalog schema remains `quanthus.data-indicator-catalog.v1`; builder behavior is versioned as
`data-indicator-catalog-builder-v1.1`.

```text
entries: 664
gaps: 10
snapshot digest:
sha256:a21f3595d551378221054a7291652454e61f8281a91d58f766b66776b2692f6b
```

The two additional gaps are the correctly retained generic SMA and RSI executable-spec gaps.

## 4. Repository verification

Focused semantic/Platform/document gate:

```text
95 passed
Catalog snapshot --check: PASS
Web lint: PASS
Web build: PASS
Web tests: 17 / 17 PASS
Ruff changed Python: PASS
bash -n Web image build/GC scripts: PASS
git diff --check: PASS
```

Full repository gate, using the existing repository test virtualenv because host `python3` currently
does not contain pytest:

```text
make check PYTHON=/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python
1555 passed
7 skipped
```

The seven skips remain the existing unmounted production expert artifacts and unconfigured
PostgreSQL integration fixtures; they are not counted as passes.

## 5. Claim ceiling

This audit repairs Catalog truthfulness and operations only. It does not change DWD/ODS values,
Feature formulas, Label rules, Facts v2 numerical semantics, expert policy, P2 behavior, or any
Shadow/paper/live authority. Production acceptance for this repaired projection and the actual
rootful Web-image cleanup require a subsequent governed deployment and smoke record.
