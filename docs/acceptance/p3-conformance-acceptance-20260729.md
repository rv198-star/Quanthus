# P3 Rule-Only Capability Conformance Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-07-29 |
| Delivery gate | `config/p3-gates.v1.json` |
| Accepted profile | `close-to-next-open-v1` historical research |
| Governing work | `P3-ACCEPT-001` |
| Consequence | P4 event-library implementation allowed |

## 1. Decision

P3 passes its bounded release criterion: a rule-only multi-year vertical slice survives through
the accepted P2 referee, every candidate and held-position review is auditable, the ledgers and
opportunity funnel reconcile, and an independent invocation reproduces the retained artifacts
byte-identically.

This is capability acceptance, not strategy promotion. The market-breadth trend-breakout V1 is
frozen as a composition and regression fixture. Its hypothesis, thresholds, labels, experts,
position sizing, and performance gates do not form the base of a formal strategy family.
`promotion_authorized=false` and `tuning_authorized=false` are enforced by the P3 gate.

## 2. Conformance Matrix

| Release claim | Implementation/evidence reviewed | Result |
| --- | --- | --- |
| PIT historical replay | continuous bundle gate, lazy frame reader, action/exception enforcement | accepted |
| Deterministic features and labels | registries, lineage checks, eight baseline features, four fixture labels | accepted |
| Governed decision path | typed experts, veto precedence, constraint merge, score/action mapping | accepted |
| Position and risk path | five fixed units, allocation caps, Trial lifecycle, four-source exits, carried sells, cooldown | accepted |
| Conservative execution/accounting | accepted P2 T-close to T+1-open matcher, costs, actions, ledgers | accepted-conditional through P2 profile |
| Auditability | 52,423 decisions, 7,344 engine events, 101,549 reconciled funnel inputs | accepted |
| Multi-year survival | 2012-01-04 through final 2021-12-31 execution/mark | accepted |
| Reproducibility | feature panel, result, and scoreboard hashes match independent rerun | accepted |

The detailed counts, metrics, hashes, final open Trial reconciliation, and input bindings remain in
`docs/acceptance/p3-governed-strategy-development-20260729.md`. That evidence is incorporated here
without reinterpreting candidate performance as product value.

## 3. Fixture Boundary

The fixture remains available for deterministic regression and interface conformance. It must not
be used to:

- justify a V1.1/V2 threshold or capital-utilization tuning cycle;
- establish the default labels, experts, score map, sizing, or exits of a formal strategy;
- claim superiority to buy-and-hold or suitability for paper, email, or real-capital operation;
- open arbitration/blind data or bypass a new strategy proposal and predeclared validation plan.

Future strategies may reuse the accepted platform interfaces. They inherit none of the fixture's
market hypothesis or parameter values by default.

## 4. Deferred Capabilities

| Work item | Disposition | Resume condition |
| --- | --- | --- |
| `P3-LABEL-004` | deferred, non-blocking | a formal label family needs semantic human review |
| `P3-ATTR-001` | deferred, non-blocking | a formal strategy declares module-removal questions before results |
| `P3-DASH-001` | deferred, non-blocking | a formal strategy produces recurring funnel and attribution evidence |

The reusable shadow comparison contract is accepted. Concrete shadow decisions resume only when a
genuine alternative expert or strategy proposal exists; synthetic shadow output for the fixture is
not acceptance evidence.

## 5. Partition And Execution Boundary

Only the 2012-2021 development partition was used for the governed run. The 2022-2024 arbitration
and 2025 permanent-blind partitions remain unopened. P4 authority does not change that guard.

The accepted execution profile remains daily T-close decision to T+1-open eligibility. This gate
does not activate intraday windows, email transport, operator reports, actual ledgers, broker
connectivity, paper operation, or real-capital use. Every retained fixture result remains
`research_only=true` and `execution_authorized=false`.

## 6. Phase Consequence

`P3-ACCEPT-001` is closed and M2 is complete. P4 may define and implement the event library and
three-class dataset through the accepted daily replay interfaces. Event semantics, outcome cards,
isolation, and split rules require their own P4 proposal; this acceptance does not choose them.

## 7. Verification

The conformance review inspected the governing PRD/HLD/LLD claims, frozen configs, accepted slice
evidence, traceability index, fixture hashes, and release boundaries. Repository tests validate the
machine gate, prohibit fixture tuning/promotion authority, and keep document status aligned with
the active phase. Final `make check PYTHON=.venv/bin/python` passed environment doctor, compilation,
367 tests, and the planning synchronization preview. Focused Ruff lint/format also passed on every
affected Python file. No market data, generated replay artifacts, credentials, or later validation
partition was read or committed by this closeout.
