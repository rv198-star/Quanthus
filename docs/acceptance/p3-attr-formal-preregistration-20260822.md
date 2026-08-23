# P3-ATTR-001 Formal Attribution Preregistration (2026-08-22)

| Field | Value |
| --- | --- |
| Status | frozen before result access |
| Work item | `P3-ATTR-001` / Issue #54 |
| Strategy | `candidate.p4-trend-risk@1.0.1` |
| Strategy digest | `sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c` |
| Preregistration digest | `sha256:a9057b028a52a4794c333fe9ca7ae3e8d936df96912375f148f11fb419bb19bb` |
| Result access before persistence | none |

The machine-readable preregistration is
`config/expert-strategies/p4-trend-risk-attribution.v1.json`. It derives the exact three questions,
expected directions, materiality rules, and uncertainty method from the approved strategy card.
It adds no observed return, drawdown, action count, chosen expert, or result-dependent threshold.

Four and only four unique complete-P2 variants are admitted: native baseline, trend-policy
fallback, position-risk fallback, and both-experts fallback. They share the exact effective DWD
v2.1 identity, parent Event Run, development partition, candidate order, capital, matcher, costs,
settlement, and seeds. The two single effects are not additive. The joint contrast is reported as
`native - trend fallback - position-risk fallback + joint fallback` and cannot be allocated to
either expert.

The existing generic attribution planner represents the pair as an explicit interaction component
so the four unique replay variants remain one baseline plus three declared intervention nodes.
This is a planner representation only: it does not make the pair a third expert, create positive
entry authority, or alter action precedence.

Uncertainty uses 2,000 deterministic paired non-overlapping 22-session block-bootstrap resamples
at 95% confidence. The seed derives from the strategy-card digest and question id. The development
window is 2020-01-01 through the last entry decision on 2021-12-30, with T+1 execution context
through 2021-12-31. Arbitration 2022-2024 and permanent-blind 2025 remain sealed.

Execution remains one PostgreSQL Replay Worker at concurrency one with serialized heavy Jobs, a
14 GiB working-set stop, 16 GiB hard container limit, 30-minute per-replay wall budget, and 1 GiB
combined published-artifact ceiling. Crossing any boundary stops without publishing a partial
release.
