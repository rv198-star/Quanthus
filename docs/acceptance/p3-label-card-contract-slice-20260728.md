# P3 Label Card and Evaluator Contract Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Strict LabelCard registry, typed feature facts, and deterministic evaluator boundary |
| Governing design | `docs/design/lld/03-label-feature-system.md` |
| Governing work | `P3-LABEL-001` |
| Dependency evidence | `docs/acceptance/p3-feature-registry-slice-20260728.md` |

## 1. Decision

`P3-LABEL-001` passes its bounded contract slice. `src/quanthus/label_registry.py` defines strict
LabelCard and registry schemas, typed feature facts, evaluation context, semantic decisions, and
evidence-bearing label results. The evaluator wrapper resolves every declared feature/version
against the accepted feature registry and binds the result to one snapshot and exact lineage
digests.

This decision approves the protocol, not a market judgment. The repository still has no production
LabelCard, primary-stage rule, twelve-state evaluator, expert opinion, position action, or order
authority from labels.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Card completeness, lifecycle, version, and counterexamples | `src/quanthus/label_registry.py`, `tests/unit/test_label_registry.py` |
| Exact feature/source dependency resolution | `src/quanthus/feature_registry.py`, `tests/unit/test_label_registry.py` |
| Snapshot, type, digest, and unavailable-input gates | `tests/unit/test_label_registry.py` |
| Controlled evaluator outputs and deterministic result identity | `tests/unit/test_label_registry.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- A card declares its dimension, semantic version, lifecycle, mathematical rule, versioned
  parameters, lookback/minimum history, exact feature versions and source fields, output values,
  evidence fields, reason codes, coexistence/exclusion rules, validation samples, and frequency
  range.
- A card requires at least two unique counterexamples whose expected values belong to the declared
  output vocabulary.
- Active cards may consume only active feature definitions. A card cannot hide an extra fact or
  source field from its declared dependency set.
- All facts in one evaluation use one security, snapshot id, and knowledge cutoff. Their definition
  and lineage digests must match the registered feature versions and output types.
- An unavailable feature can produce only an explicit unknown result. It cannot be converted into
  an applied label.
- Evaluators cannot return an undeclared value, reason code, or evidence field. Accepted output
  records the card/evaluator identity and every required feature-lineage digest deterministically.

## 4. Residual boundary

The synthetic evaluator in the tests proves only the protocol. It is not a production formula or
frequency claim. `P3-LABEL-002` must separately version concrete features and LabelCards, define
unknown/context-missing behavior for each label, add golden market cases, and show that the selected
vertical-slice labels are useful and point-in-time correct. Human review and conflict/frequency
audits remain `P3-LABEL-003/004`.

## 5. Verification

- Focused LabelCard and evaluator-contract suite: passed 11 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Full `make check PYTHON=.venv/bin/python`: passed 186 tests, environment doctor, compilation,
  and planning synchronization preview.
