# LLD 09: Delivery Roadmap and Milestones

## 1. Build order

The architecture flows data to representation to experts to positions, but delivery order is
data, backtest referee, rule-only vertical slice, event library, research models, simulation,
and live operation. A later phase cannot compensate for an untrusted earlier phase.

## 2. Phase plan

| Phase | Deliverable | Gate | Dependency |
| --- | --- | --- | --- |
| P0 | Policy, PRD, HLD/LLD, configuration, contracts | `make check` and approved decisions | None |
| P1 | Daily data, lifecycle, tradability, snapshots | C1/C2/S1 closed and D1/D2/D3 pass | P0 |
| P2 | Matcher, unit ledger, audit, metrics | Golden cases reconcile and rerun identically | P1 |
| P3 | Rule-only vertical slice and shadow experts | Multi-year closed loop is auditable | P2 |
| P4 | Event library and daily group report | Three leading differentiators | P3 |
| P5 | Research scoring layer | Beats rule baseline and passes promotion gate | P4 |
| P6 | Frozen paper operation and two-window notification rehearsal | Distribution aligns with honest backtest; no live-fill claims | P3 baseline |
| P7 | Operator-assisted small-capital operation | Every advice closes with an accepted actual/no-action report and reconciliation | P6 |

P5 research may run beside P6 frozen simulation, but a development build cannot alter the
simulation policy.

Current state: P2 remains conditionally accepted for the active daily research profile by
`config/p2-gates.v1.json`, and P3 is accepted by `config/p3-gates.v1.json`. Its strategy-neutral
replay, rule contracts, position/risk path, audit funnel, and one frozen capability fixture prove
the multi-year closed loop. That fixture is not a formal strategy lineage and cannot be tuned or
promoted. P4 event-library implementation is open; target two-window activation and forward
notification retain their separate blockers.

## 3. Milestones

- **M0**: repository baseline, versioned P0 configuration, documentation, tests, and issue map.
- **M1**: accepted data foundation and simple baseline strategy through the trusted engine.
- **M2**: rule-only vertical slice over multiple years with complete decision trace.
- **M3**: first event differentiation report and proposal-to-shadow-to-decision loop.
- **M4**: frozen paper operation and idempotent signal notification begin and continuously record
  arbitration evidence without live-fill claims.
- **M5**: first operator-assisted live review and explicit continue/stop decision.

## 4. Release mechanics

Each phase has a GitHub milestone and an epic issue. Child issues carry stable ids from
`docs/work-items/index.md`, list dependency ids, and link the governing LLD section. A gate
changes only through configuration, tests, and evidence in one pull request.

## 5. Stop rules

- If M2 drawdown is uncontrolled and attribution does not converge, revisit labels and entry
  threshold mechanisms; do not add a model to conceal the failure.
- If simulation materially diverges from the honest backtest, stop expansion and attribute the
  difference through data, execution, and decision logs.
- Two failed quarterly reveals freeze live-capital expansion.
- No phase creates a return promise.

M2 is accepted by `docs/acceptance/p3-conformance-acceptance-20260729.md`. Deferred human label
review, constrained attribution, dashboards, and concrete shadow production resume only for a
formal semantic proposal; they do not reopen M2 or authorize changes to its fixture.

## 6. Work items

| ID | Work | Acceptance evidence |
| --- | --- | --- |
| META-DOC-001 | Maintain PRD/HLD/LLD and precedence | Docs and policy agree |
| META-TRACE-001 | Maintain requirement-to-issue traceability | No orphan active issue |
| META-GATE-001 | Automate release gate reporting | CI gate artifact |
| P7-LIVE-001 | Define email-advice and actual-operation reporting boundary; defer broker adapter | Paper-compatible manual protocol |
| P7-RECON-001 | Reconcile advice with operator-reported fills, changes, or no action | Daily discrepancy report |
| P7-SCALE-001 | Implement explicit unit expansion decision | Signed reveal decision |
