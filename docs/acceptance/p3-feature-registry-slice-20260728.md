# P3 Feature Registry and Lineage Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Strict feature definitions, immutable registry identity, and PIT snapshot lineage |
| Governing design | `docs/design/lld/03-label-feature-system.md` |
| Governing work | `P3-FEAT-001` |
| Release gate | `config/p2-gates.v1.json` |

## 1. Decision

`P3-FEAT-001` passes its bounded acceptance slice. `src/quanthus/feature_registry.py` provides a
strict versioned registry and deterministic lineage record over accepted P1 `PublishedSnapshot`
and `DatasetManifest` contracts. A successful lineage retains the complete snapshot identity,
universe policy, definition digest, exact source columns, dataset versions, manifest ids, source
cutoffs, and history/completeness evidence.

This decision does not publish a production registry or approve a concrete feature. It adds no
label evaluator, twelve-state classification, expert logic, strategy threshold, or order path.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Strict registry loading and lifecycle | `src/quanthus/feature_registry.py`, `tests/unit/test_feature_registry.py` |
| Immutable P1 snapshot and manifest inputs | `src/quanthus/data_foundation.py` |
| Deterministic definition, registry, and lineage digests | `tests/unit/test_feature_registry.py` |
| PIT and completeness failure behavior | `tests/unit/test_feature_registry.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- Registry JSON has an exact schema and cannot gain behavior from implicit defaults or ignored
  fields.
- Feature definitions include formula, exact dataset/column inputs, unit, null policy, lookback,
  minimum history, cutoff rule, output type, owner, lifecycle, and semantic version.
- Reordering definitions or source fields cannot change canonical registry or lineage identity.
- The same feature/version key cannot carry two definitions, and one feature cannot have two active
  versions.
- Every lineage resolves required datasets from one immutable snapshot and carries the manifest id,
  dataset version, source column, and source knowledge cutoff.
- Insufficient history, missing datasets, incomplete T-close cross sections, and future source
  evidence fail closed with stable reason codes. No future row or current sector membership is
  substituted.

## 4. Residual boundary

The slice does not execute formulas, validate that a named column exists in materialized Parquet,
calculate cross-sectional completeness, store feature values, or define label cards. Those belong
to the concrete feature/evaluator and `P3-LABEL-001` slices. Production activation additionally
requires a versioned registry with real definitions and exact dataset schema evidence.

## 5. Verification

- Focused feature registry and lineage suite: passed 10 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Full `make check PYTHON=.venv/bin/python`: passed 174 tests, environment doctor, compilation,
  and planning synchronization preview.
