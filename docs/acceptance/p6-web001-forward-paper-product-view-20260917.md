# P6-WEB-001 Forward/Paper Product View Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted pre-activation read-only projection |
| Work item | `P6-WEB-001` |
| Target candidate | `candidate.p4-trend-risk@1.0.1` |
| Product config | `config/forward-validation/web/p6-forward-product-view.v1.json` |
| Authority after closure | read-only product projection only; no activation or execution authority |
| Next gate | `P6-ACTIVATE-001` pre-activation integration and explicit activation decision |

## Accepted outcome

The existing candidate-detail API and Product Web now expose one digest-bound `forward_validation`
section for the frozen P6 target candidate. The view validates the complete accepted P6 engineering
predecessor chain before projection and remains fail-closed on binding drift.

Pre-activation state is explicit rather than inferred:

- admission is disabled;
- evidence is `waiting_activation`;
- official budget progress is 0/66 sessions and 0/3 independent 22-session blocks;
- the longest maturity tail remains 20 trading sessions;
- the maximum wall-calendar duration remains 150 days from the future first official market date;
- the Paper lane is engineering-ready with five fixed CNY 100,000 units, non-compounding and no
  profit redeployment, but no runtime ledger is fabricated before activation;
- notification, broker, live-capital, Stage-action, parameter-edit and activation-control authority
  remain false.

Non-target candidates receive `not_applicable` and do not inherit P6 state or authority.

## Product and API boundary

`ForwardValidationProductView` may read only frozen repository artifacts plus existing Platform Job
metadata for `quanthus.p6-forward-cycle.v1`. It does not read DWD, open sealed partitions, execute P2,
create a Paper ledger, settle outcomes, submit a Job or expose a mutation method.

The Product Web adds one `Forward Validation` panel to the existing candidate detail. The panel shows
admission/evidence status, finite budget, latest Forward Job when present, Paper policy and immutable
source digests. Existing candidate review/governance controls remain separate and cannot activate P6.
There is no Forward-specific activation, broker, notification, live-capital or parameter-edit action.

## Verification

Executed verification:

- backend product-view + existing candidate-detail/composition focused suite: `28 passed`;
- Product Web static product-contract suite after the P6 panel: `7 passed`;
- Product Web Vite production build: passed after lockfile-exact `npm ci --ignore-scripts`;
- target/non-target projection tests prove budget zero, disabled admission and no cross-candidate P6 projection;
- queued disabled-rehearsal Job projection does not change official evidence progress.

The dependency installation reported existing npm dependency advisories. No dependency versions,
package manifests or lockfile semantics were changed by this work item; those advisories do not grant
or alter P6 authority.

## Claim ceiling

P6-WEB-001 proves only that the accepted pre-activation P6 engineering state can be projected
read-only and digest-bound through the existing product surface. It does not prove production
deployment, rollback, official prospective execution, future DWD availability, Paper performance,
expert quality or economic value.

`P6-ACTIVATE-001` remains a separate gate and may be considered only after repository persistence,
exact production deployment, disabled-state rehearsal and rollback/readiness checks pass.
