# Operational ADS Base Repository Capability

| Field | Value |
| --- | --- |
| Evidence date | 2026-07-30 UTC |
| Checkout baseline | `531ff2f51f152ecee95b2fe37f855d01cb16e8b6` |
| Branch | `feat/issue-46-ads-base` |
| Scope | repository implementation and synthetic contract evidence only |
| Runtime publication | not attempted |

## Implemented boundary

`config/operational-ads-base.v1.json` fixes OCI authority, provisional/final package identities,
six exact upstream roles, ten semantic tables, decision/runner/outcome roles, and copy-only
publication. `src/quanthus/operational_ads_base.py` implements:

- strict plans with exact non-`CURRENT` manifests, a complete inventory declaration, logical
  object identity, upstream/inventory cross-binding, manifest/content hashes, source table roles,
  logical content and lineage digests, and per-decision PIT evidence;
- generation-ledger hashes cross-bound to canonical catalog and DWD/DWS manifests, plus exact
  final prior-Base and July 31 daily inventory bindings;
- deep Parquet logical-row and JSON artifact verification, duplicate/conflict rejection,
  deterministic compact table materialization, path-free retained lineage, unique staging,
  `READY` last, and atomic directory publication;
- clean-path verification from manifest/`READY` only, role-filtered capabilities, and adaptation to
  the existing typed strategy replay reader without exposing the package root to callbacks; and
- immutable copy promotion followed by a process-locked, SHA-preconditioned atomic update of only
  `BASE_CURRENT.json`.

The CLI exposes separate build, deep-check, bounded replay smoke, and promotion commands. The
root-only `ops/bin/publish-operational-ads-base` requires a clean checkout and executes those gates
before descriptor promotion. Existing replay, baseline, and governed replay commands accept an
explicit operational Base while retaining portable exports as separate authorized scopes.

## Focused verification

The following checks passed against the working tree:

```text
ruff check: passed
mypy src/quanthus/operational_ads_base.py: passed
bash -n ops/bin/publish-operational-ads-base: passed
tests.unit.test_operational_ads_base: 11 passed
tests.contract.test_operational_ads_base_boundary: passed
tests.contract.test_strategy_ads_boundary: passed
```

The synthetic suite covers reproducible content identities, source-unavailable verification,
one complete ten-table clean copied-root replay frame and bounded audit,
decision/runner/evaluator isolation, immutable promotion, descriptor preconditions, tampered parts,
duplicate keys, interrupted publication, floating pointers, absent PIT evidence, unsafe paths,
manifest logical-id mismatch, upstream/inventory divergence, and ledger/upstream divergence.

`make check PYTHON=.venv/bin/python` also passed all 454 repository tests, including the concurrent
Issue #47/#48 and strategy-attribution contracts. The unqualified `make check` uses system
`python3` in this checkout and fails during collection because that interpreter lacks `pyarrow`;
the project `.venv` is the dependency-complete runtime used for the passing repository gate.

These checks do not prove real July coverage, semantic equivalence of retained exports, actual
upstream lineage, real replay output, timing, resource use, or a published package digest. Those
claims require the Phase A and Phase B runtime evidence listed in the evidence README.
