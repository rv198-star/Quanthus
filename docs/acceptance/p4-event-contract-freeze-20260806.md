# P4 First Event Contract Freeze Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-08-06 |
| Governing design | `docs/design/lld/06-event-library.md` |
| Governing work | `P4-EVENT-001` / GitHub Issue #73 |
| Accepted profile | `close-to-next-open-v1` |
| Approval record | `https://github.com/rv198-star/Quanthus/issues/73#issuecomment-5196483760` |
| Consequence | Stage B contract authority is open for the approved development-only semantics; forward scan remains `P4-EVENT-002` |

## 1. Decision

`P4-EVENT-001` passes its Stage A contract freeze. Human approval applies to the exact candidate
rule-card and split-policy semantic digests listed below. The approved candidate defines a
research cohort, not a buy signal, strategy, target, label, performance claim, or execution
instruction.

| Artifact | Identity | Approved semantic digest |
| --- | --- | --- |
| Candidate rule card | `p4-event.price-volume-state-transition@1.0.0` | `sha256:b04464c838705e71331515332ceb9e796ce26698c784d4615cff00dfdcf41c50` |
| Split policy | `p4-event-date-split@1.0.0` | `sha256:10c58c9f402327b2c1e9e3ba9782cce909216b795018b89586bc5a9b210fb684` |

Any semantic, threshold, partition, purge, embargo, eligibility, isolation, concentration, or input
contract change changes the applicable digest and requires a new version and renewed human
approval. Approval metadata is outside each semantic payload, so recording the approval does not
change the approved digest.

## 2. Accepted Candidate Semantics

- Candidate source is the direction-neutral transition `quiet(T-1) -> active(T)`.
- Daily unadjusted intraday range and CNY turnover are normalized against each security's own
  previous 20 eligible-session medians.
- Quiet requires both activity ratios to be at or below `0.80`; active requires both to be at or
  above `1.50`.
- The complete transition input spans 22 eligible sessions. Listing, tradability, missing-data,
  invalid-bar, observation-gap, and T-1/T corporate-action exclusions fail closed with explicit
  reason codes.
- Same-security cooldown and minimum separation are 22 sessions.
- Static level-one industry membership is a versioned `static_non_pit_concentration_only` input. It
  may control cohort concentration only and cannot become candidate evidence or reconstructed
  historical membership.

## 3. Accepted Split Semantics

- Development is 2012-2021, arbitration is 2022-2024, and permanent blind is 2025.
- The approved policy grants read authority only to the development partition.
- Purge, embargo, and declared maximum overlap are each 22 trading sessions.
- Feature, signal, and outcome slices of one event must remain in one split.
- Arbitration and permanent-blind access fail closed. A later label card whose maximum overlap
  exceeds 22 sessions requires a new split-policy version and approval before activation.

## 4. Contract Evidence

| Concern | Evidence |
| --- | --- |
| Exact-key rule-card parsing, semantic digest, lifecycle, and authority gate | `src/quanthus/event_library/rule_card.py`, `tests/unit/test_event_rule_card.py` |
| Outcome-independent event identity and strict label-free record parsing | `src/quanthus/event_library/contracts.py`, `tests/unit/test_event_library_contracts.py` |
| Date splits, whole-event containment, purge/embargo, and partition access | `src/quanthus/event_library/splits.py`, `tests/unit/test_event_splits.py` |
| Frozen machine-readable semantics | `config/p4-candidate-price-volume-transition.research.v1.json`, `config/p4-event-split-policy.research.v1.json` |
| Design and work-item traceability | `docs/design/lld/06-event-library.md`, `docs/work-items/index.md`, `tests/contract/test_document_traceability.py` |

Negative cases reject unknown or outcome-bearing candidate fields, digest mismatch, forged event
identity, duplicate identities, one event assigned to multiple splits, future knowledge cutoffs,
forbidden partition access, and split-boundary leakage.

## 5. Partition And Runtime Boundary

This acceptance authorizes contract use for the approved development-only Stage B implementation.
It does not implement or run the historical candidate scan, determine T+1 executability, generate
labels, produce comparison reports, open arbitration or permanent-blind data, or access simulation
observations. During this Stage A implementation and approval, no market-data partition was read.

## 6. Verification

- Focused event-contract and documentation traceability suite: 41 tests passed.
- Focused Ruff and strict mypy checks passed for the new event-library module.
- Repository `make check PYTHON=.venv/bin/python` passed environment doctor, compilation, 885 tests,
  and planning synchronization.
