# P6 Read-Only Forward Validation Product View

| Field | Value |
| --- | --- |
| Work item | `P6-WEB-001` |
| Parent | `p6-forward-validation.md` |
| Design status | approved |
| Delivery gate | accepted pre-activation read-only projection |
| Product surface | existing candidate detail only |
| Target candidate | `candidate.p4-trend-risk@1.0.1` |
| Activation authority | none |

## Purpose

Project the frozen P6 prospective-validation and Paper engineering state into the existing candidate-detail page without creating another control plane. The product view exists to make admission state, evidence maturity, finite budget, Forward Worker state, Paper policy and immutable source identities inspectable before and after later activation.

## Source boundary

`config/forward-validation/web/p6-forward-product-view.v1.json` binds the exact accepted predecessor chain:

- P6 entry proposal;
- prospective DWD/access/correction boundary;
- role-specific scorecard policy;
- composite Forward Worker contract;
- isolated Paper policy;
- accepted evidence for `P6-GOV-001`, `P6-DATA-001`, `P6-SCORE-001`, `P6-GOV-002` and `P6-PAPER-001`.

`ForwardValidationProductView` validates those bindings before returning data. It may read the platform Job store to report the latest `quanthus.p6-forward-cycle.v1` Job. It does not read DWD, execute P2, create a Paper ledger, submit a Job, settle a target or open a sealed partition.

## Pre-activation projection

Before `P6-ACTIVATE-001`, the target candidate must project:

- admission = disabled;
- evidence = waiting activation;
- official collection progress = `0/66` sessions and `0/3` independent blocks;
- target-maturity tail = 20 trading sessions;
- wall-calendar ceiling = 150 days after the first official market date;
- Paper engineering policy = 5 fixed CNY 100,000 units, no compounding, no profit redeployment;
- Paper ledger / cash / positions / intents / fills = not created / unavailable rather than invented zeros;
- notification, broker, live-capital, Stage-action, parameter-edit and activation-control authority = false.

Other candidates receive only `state=not_applicable`; the P6 state is not generalized across the catalogue.

## UI contract

The existing QuantDinger-derived candidate detail adds one read-only `Forward Validation` section. It shows:

- activation/admission state;
- evidence and block/session budget progress;
- longest maturity tail and calendar ceiling;
- latest Forward Job when one exists;
- fixed-unit Paper policy and current ledger availability;
- source digests for the frozen P6 chain.

The section exposes no activation, notification, broker, live-capital, parameter edit or Forward-specific mutation action. Existing candidate review/governance controls remain separate and cannot activate P6.

## Failure semantics

Binding drift, missing predecessor evidence, product-view config drift or downstream authority in the Web contract fails application composition rather than degrading to an unverified display. A disabled rehearsal Job remains rehearsal evidence and cannot increase official prospective budget progress.

## Verification

Acceptance requires:

- direct product-view tests for target/non-target candidates and queued disabled rehearsal projection;
- existing Platform HTTP candidate-detail and composition suites;
- product-Web contract tests proving no Forward mutation API/action;
- production Vite build;
- repository `make check`.

P6-WEB acceptance grants no activation or official prospective evidence authority.
