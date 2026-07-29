# P3 Expert Protocol Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Typed scoring, constraint, veto, abstention, PIT context, and evidence contracts |
| Governing design | `docs/design/lld/04-experts-aggregator.md` |
| Governing work | `P3-EXP-001` |
| Dependency evidence | `docs/acceptance/p3-label-card-contract-slice-20260728.md` |

## 1. Decision

`P3-EXP-001` passes its bounded protocol slice. `src/quanthus/expert_protocols.py` defines one PIT
expert context over feature facts and LabelResults, evidence references derived from that context,
typed scoring/constraint/veto outputs, explicit abstention, and runners that verify implementation
identity and evidence ownership.

This decision approves no expert judgment. The synthetic fixtures do not implement tradability,
candidate, extension, market, structure, sector, news, company-quality, or model-based opinions and
cannot create an aggregate action, position transition, Signal, OrderIntent, or Order.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| PIT expert context and evidence derivation | `src/quanthus/expert_protocols.py`, `tests/unit/test_expert_protocols.py` |
| Scoring range and abstention semantics | `tests/unit/test_expert_protocols.py` |
| Constraint completeness and bound consistency | `tests/unit/test_expert_protocols.py` |
| Veto allow/action consistency and abstention | `tests/unit/test_expert_protocols.py` |
| Identity and external-evidence forgery rejection | `tests/unit/test_expert_protocols.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- One context cannot mix securities, snapshot ids, or knowledge cutoffs, and duplicate feature or
  label identities fail closed.
- Expert evidence is a digest-bound reference to only the supplied snapshot, feature lineage, or
  LabelResult. A runner rejects evidence outside that set.
- Every output has stable expert id/version, applicability reason, result reason, and non-empty
  unique evidence.
- Scoring opinions use finite signal in `[-1, 1]` and confidence in `[0, 1]`.
- Applicable constraints include minimum/maximum position in `[0, 1]`, scope, stop rule, and hold
  rule; conflicting or incomplete bounds fail closed.
- Applicable vetoes identify system or strategy authority and make `allow=true` equivalent to
  `required_action=none`.
- Abstained scoring, constraint, and veto outputs carry none of their actionable payload. They are
  not silently converted to zero, unconstrained, or allowed opinions.

## 4. Residual boundary

`P3-EXP-002` must implement concrete rule experts with exact label/feature dependencies and golden
cases. `P3-AGG-001/002` separately owns precedence, constraint intersection, scoring, and action
mapping. Until those pass, expert protocol objects are evidence-bearing opinions only and cannot
reach the P2 Signal or execution path.

## 5. Verification

- Focused expert protocol suite: passed 10 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Full `make check PYTHON=.venv/bin/python`: passed 197 tests, environment doctor, compilation,
  and planning synchronization preview.
