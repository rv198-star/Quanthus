# P6-GOV-001 Prospective Mechanism MVP Entry Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted for entry precommitment only |
| Work item | `P6-GOV-001` |
| Entry proposal | `config/forward-validation/entry/p6-prospective-mvp-entry.v1.json` |
| Proposal digest | `sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18` |
| Strategy | `candidate.p4-trend-risk@1.0.1` |
| Strategy semantic digest | `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c` |
| Authority after closure | entry proposal frozen; no prospective activation or downstream P6 implementation authority |
| Next gate | explicit owner value review before `P6-DATA-001` |

## Accepted outcome

`P6-GOV-001` closes the entry-precommitment step for the first Quanthus prospective mechanism MVP.
It does **not** claim that the current two-expert catalogue is a complete Quanthus expert system and
does **not** claim that the current Stage vocabulary is complete. Its bounded purpose is to test, in
a later separately authorized phase, whether the existing DWD -> experts -> aggregator ->
Shadow/paper -> settlement chain can remain outcome-blind, auditable, reproducible and authority-safe
under real future information arrival.

The entry proposal is immutable and fail-closed. It grants no official prospective cycle, data
runtime, scorecard runtime, Forward Worker, paper runtime, Web projection, notification, broker or
live-capital authority. Acceptance therefore stops at the mandatory owner value checkpoint before
`P6-DATA-001`.

## Frozen strategy and expert closure

The proposal binds the already accepted formal strategy without changing its semantics:

- `candidate.p4-trend-risk@1.0.1` at
  `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`;
- `expert.trend-stability@1.0.0`, evaluation-spec digest
  `sha256:d6f8cdc64042ee6795fda6979e08ca98c380a33e53664f8bd0d321d775669723`,
  formal entry-policy digest
  `sha256:8bc371a80f3925451f8139b0af0f86e6542ae0abef589277f17751fef43a9be5`;
- `expert.position-risk-review@1.0.0`, evaluation-spec digest
  `sha256:634d9bed8ed158d6267bf648248e9de270be26d4fb51d2a88a3dbf0bc4066002`,
  risk-policy digest
  `sha256:b8665cea0dda4c6a9d7949a94ff0b2c030ef6f5321aa2564e09ed7b52fbde8cf`;
- frozen target `p4-event.outcome-first-passage@1.0.0`, 20-trading-session horizon, resolver
  `resolver.p4-label-corpus-adapter@1.0.0`, and mean-absolute-ordinal-error scorer;
- the accepted `aggregation.p4-trend-risk@1.0.0` precedence and score threshold with no aggregator
  change authority;
- `close-to-next-open-v1`, position lifecycle `1.1.0`, five fixed CNY 100,000 units, no profit
  redeployment and no compounding.

The accepted P3 lifecycle record remains byte-preserved as `inconclusive + Shadow disabled`.
`P6-GOV-001` does not rewrite that record or enable its old Shadow schedule.

Stage10/11 remain development advisory semantics only. They receive no P6 action authority, and
Stock-Stage phase 1 remains closed.

## Frozen prospective budget

The first prospective mechanism MVP uses a finite, dependence-aware calendar budget:

| Item | Frozen value |
| --- | --- |
| Independent evidence unit | one non-overlapping 22-trading-session market block |
| Collection blocks | `3` |
| Collection sessions | `66` trading sessions |
| Longest target-maturity tail | `20` trading sessions |
| Maximum wall-calendar duration | `150` calendar days from the first official market date |
| Cross-sectional rows/candidates/fills count as independent progress | `false` |
| Result-driven budget extension | `false` |
| Low-activity disposition | `inconclusive_low_activity_no_budget_extension` |

This is a mechanism-validation budget, not a statistical-power claim for profitability. Candidate,
security, expert-observation, decision and fill counts remain coverage diagnostics rather than
independent evidence units.

## Activation and data-time boundary

Proposal acceptance does not activate P6. A future `P6-ACTIVATE-001` append-only decision must
occur first. Only the first complete trading session **strictly after** that activation decision's
Asia/Shanghai market date whose daily DWD release is READY and deeply verifiable may begin the
official budget.

Earlier 2026 data is engineering rehearsal only and cannot be backfilled into prospective evidence.
Every later cycle must bind one future DWD release identity, knowledge cutoff and publication/
visibility evidence. Late corrections append a versioned disposition and never rewrite an existing
observation.

Arbitration 2022-2024 and permanent blind 2025 remain sealed. The proposal gives no authority to
open either partition.

## Primary gate and descriptive evidence

The first prospective MVP's primary gate is the conjunction of mechanism-integrity conditions, not
investment return:

- every official observation and aggregate decision is sealed before its future settlement evidence
  can enter the decision path;
- zero post-cutoff/PIT evidence violations;
- zero strategy/expert/policy/target/resolver/aggregation/P2 semantic drift;
- zero unauthorized authority events;
- zero same-release independent-reproduction semantic mismatches;
- zero paper cash/position/fill reconciliation breaks;
- zero correction rewrites of prior evidence.

Economic and expert-quality outputs remain mandatory descriptive evidence: trend-target error versus
the frozen neutral baseline, coverage/abstention, risk-expert activity, aggregator disagreement,
fixed-unit paper return/drawdown/turnover/exposure, fill/reject/carry behavior, honest-replay
divergence and date-block/regime slices. Weak, negative or inconclusive economics are valid evidence
and cannot trigger in-place tuning.

## Stop, rollback and authority ceiling

The proposal fails closed on semantic binding drift, unprovable DWD publication/visibility time,
post-cutoff evidence, correction rewrite, reproduction mismatch, paper-ledger imbalance,
Shadow-to-Order or paper-to-actual-execution leakage, sealed-partition access, scope expansion to
notification/broker/live capital, or expiration of the finite calendar ceiling before review
readiness.

If continuing the same proposal would require a new expert, Stage action mapping, aggregator
mechanism or P5 model, the program stops for a new owner decision rather than silently expanding the
proposal.

Rollback disables the later P6 admission binding and rejects new submissions while preserving all
entry proposals, activation decisions, Runs, observations, settlements, incidents, paper ledgers and
historical P3/P4 evidence. Published evidence is superseded by new versions, never repaired in place.

## Implementation and verification evidence

`P6-GOV-001` adds only governance/configuration contracts and deterministic validators:

- `src/quanthus/forward_validation/contracts.py`;
- `src/quanthus/forward_validation/serialization.py`;
- `src/quanthus/forward_validation/validation.py`;
- `config/forward-validation/entry/p6-prospective-mvp-entry.v1.json`;
- focused entry and planning/traceability contract tests.

The deep loader verifies repository-file SHA-256 bindings, exact accepted strategy/expert/target/P2
semantics, the finite budget, mechanism-integrity metric precommitment, stop/rollback rules, and the
authority matrix. Mutation tests prove that budget inflation, shortened maturity tail, profit as a
primary gate, Stage/downstream authority, lifecycle activation, binding drift and automatic
successor creation fail closed.

Focused governance/traceability verification passed `48` tests. Repository `make check` then passed
`2592` tests with `7` expected skips and `0` failures. The skips are the existing optional production
expert-artifact or external PostgreSQL-DSN suites; they do not grant missing runtime authority.

No production service was deployed, no P6 Worker Job was submitted, no prospective DWD evidence was
read, no paper account was run, and no activation record was created in this work item.

## Mandatory next decision

`P6-GOV-001` is closed. `P6-DATA-001` remains blocked even though its technical predecessor now has
accepted evidence. The owner must separately review whether the frozen 66-session collection budget,
20-session maturity tail, 150-calendar-day ceiling and prospective mechanism value justify the
calendar/operational cost of building the data boundary, scorecards, Forward Worker, paper lane and
activation machinery.

There is no automatic successor.
