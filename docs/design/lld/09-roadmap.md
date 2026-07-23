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
| P6 | Frozen simulation | Distribution aligns with honest backtest | P3 baseline |
| P7 | Small-capital live units | Fill/slippage assumptions reconcile | P6 |

P5 research may run beside P6 frozen simulation, but a development build cannot alter the
simulation policy.

## 3. Milestones

- **M0**: repository baseline, versioned P0 configuration, documentation, tests, and issue map.
- **M1**: accepted data foundation and simple baseline strategy through the trusted engine.
- **M2**: rule-only vertical slice over multiple years with complete decision trace.
- **M3**: first event differentiation report and proposal-to-shadow-to-decision loop.
- **M4**: frozen simulation begins and continuously records arbitration evidence.
- **M5**: first quarterly reveal and explicit live/no-live decision.

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

## 6. Work items

| ID | Work | Acceptance evidence |
| --- | --- | --- |
| META-DOC-001 | Maintain PRD/HLD/LLD and precedence | Docs and policy agree |
| META-TRACE-001 | Maintain requirement-to-issue traceability | No orphan active issue |
| META-GATE-001 | Automate release gate reporting | CI gate artifact |
| P7-LIVE-001 | Define broker adapter and live safety boundary | Simulation-compatible contract |
| P7-RECON-001 | Reconcile live fills, costs, and slippage | Daily discrepancy report |
| P7-SCALE-001 | Implement explicit unit expansion decision | Signed reveal decision |
