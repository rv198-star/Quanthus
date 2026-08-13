# ADR 0011: QuantDinger-Vue selective derivative as Product Web

## Status

Accepted for the corrected PK4 product-Web path on 2026-08-12; candidate approval workflow
amended on 2026-08-13.

## Context

The first PK4 delivery treated a Quanthus-owned operator console as the primary Web product. It
proved authentication, MFA, RBAC, jobs, and platform administration, but did not preserve the
QuantDinger product workflows the migration was intended to retain. User acceptance rejected that
claim. The operator console remains useful evidence for the secondary control plane only.

QuantDinger-Vue is available as source and builds reproducibly at commit
`f2b5968bb815ed7c0c2301d61523dca98dcdddde`. Its source-available license permits the current
non-commercial evaluation derivative, requires attribution, and requires a separate license for
commercial use.

## Decision

The primary Web is a selective derivative of the pinned QuantDinger-Vue product source. It retains
Vue 2.7, Ant Design Vue, side navigation, product workspace composition, candidate-list patterns,
research-result presentation patterns, and responsive behavior. The first product cut contains:

1. A-share research overview.
2. A candidate-validation workbench with a searchable queue, verified replay detail, closed-trade
   ledger, lineage, policy-divergence explanation, and bounded review actions.
3. Published attribution analysis.

Quanthus owns all product data, PIT semantics, replay, strategy status, metrics, and evidence. The
product Web consumes a versioned catalogue and digest-bound replay-evidence projection. Platform
PostgreSQL stores only append-only approval workflow events. It does not import QuantDinger providers,
backtest execution, strategy runtime, broker execution, or AI decision paths.

Every listed item is a versioned candidate. Four state axes remain independent:

1. replay run: `not-run -> queued -> running -> succeeded|failed|canceled`;
2. evidence review: `pending-review -> reviewing -> changes-requested|review-passed|review-failed`;
3. governance: `governance-not-submitted -> governance-under-review -> governance-returned|
   governance-withdrawn|governance-rejected|governance-admitted`;
4. execution: `not-authorized -> shadow -> authorized -> suspended|revoked`.

The current catalogue supplies immutable replay facts only. Evidence-review and governance state
are projected from digest-bound append-only events, while execution remains `not-authorized`.
Admin and operator roles may review; only admins may govern; viewers remain read-only. Governance
submission requires a current review pass. Governance return resets review to pending and requires
a newer pass before resubmission. Withdrawal preserves a still-current pass. Review changes or
failure close the current evidence package. Governance reject and admit are terminal for the same
candidate version. No approval action grants execution authority.

Data or policy reruns create a new Run and evidence package without overwriting old evidence.
Strategy semantic or parameter changes require a new candidate version. The approval page does not
fabricate a rerun control while no replay-job integration exists.

The product catalogue also keeps warehouse coverage separate from replay evidence. The current
DWD authority is `V2-Build202607 / dwd-base-202607-v2.1` plus immutable correction
`dwd-base-202607-v2.1-correction-20260813-001`, through 2026-07-31; its historical start is
dataset-specific rather than one invented global date. Strategy-replay ADS partitions remain
development 2012-2021, arbitration 2022-2024, permanent blind 2025, and recent research through
2026-07-30. Each candidate declares the exact partition and decision/execution window that produced
its displayed metrics. Availability of a later DWD or ADS date never implies that a candidate was
already replayed over it.

The bounded product evidence file contains real monthly equity observations and the complete
closed-trade ledger projected from accepted replay artifacts. Its content digest is checked against
the catalogue version at API startup/read time. It is a serving projection, not a new strategy ADS,
and cannot replace the source replay artifacts.

The old console remains at `/operator` as a secondary administration surface. The public root and
the three primary routes are product workflows.

## Removed and restorable modules

AI analysis, Copilot, Fast Analysis, live trading, broker accounts, grid orders, billing, community,
MCP, agent tokens, AI review/calibration, and authoring are absent from the build and navigation.
Their restoration requires a new named consumer, an authority decision, a dependency review, and
focused tests. Strategy and indicator generation may return later only as untrusted draft creation
behind Quanthus validation and replay.

## License gate

`product-web/LICENSE.quantdinger-frontend` and `product-web/UPSTREAM.md` are shipped with the source,
and `Powered by QuantDinger` remains visible. Commercial deployment is prohibited until a separate
Open Byte Inc. license is recorded.
