# P4-REPORT-001 Production A/B Acceptance — 2026-08-23

## Scope

This record closes `P4-REPORT-001` after two independent production Jobs reproduced the same
P4 daily group comparison report under one exact Worker revision and one exact governed input.
It is acceptance evidence only; it does not change the frozen report policy or authorize
arbitration, permanent-blind reads, strategy promotion, or execution.

## Frozen reporting authority

- Work item: `P4-REPORT-001`
- Policy: `p4-event.daily-group-comparison@1.0.0`
- Semantic digest:
  `sha256:4b7e6f931b06615940a9a72a5bd05f800be40f825716093e04637ab5fd8134a0`
- Primary contrast: `positive_vs_neutral`
- Window: T through T-20
- Leading-feature count: 3

The policy was frozen before the real report result was read. The production identity-boundary
remediation and deterministic floating-point reduction remediation changed implementation only;
they did not change the policy digest, feature definitions, statistical thresholds, FDR rule, or
selection criteria.

## Accepted implementation chain

- PR #177: deterministic comparison-report implementation and Event Worker v3
- PR #180: explicit Event Library `listing_code` to P3 feature-panel identity bridge
- PR #182: deterministic single-thread DuckDB publication reductions

The final A/B Jobs ran on production Event Worker revision:

`063724a1fb07f36d7975271966befb871d3cf378`

This revision is a descendant of the accepted report implementation revision. A bounded diff audit
confirmed no change to the comparison-report implementation, Event Worker, or frozen report policy;
the only shared P3 feature-panel change was an additive optional security-selection parameter that
was not supplied by these P4 Jobs.

## Governed input identity

Both Jobs used the exact same input digest:

`sha256:f4f27f28a12cb6ad7a6c720170f5f01d959170e67f8ad314573a67bb00ed2f85`

The governed input bound the same source policy, direct-DWD provider, candidate rule card, split
policy, outcome card, structural-break card, P3 feature registry, and comparison-report policy.

## Production Run A4

- Job: `job_870e174940e947b785d9663efd28b169`
- Run: `run_870e174940e947b785d9663efd28b169`
- State: `succeeded`
- `report_acceptance_pass=true`

## Production Run B4

- Job: `job_121bc870861248cc827361d8e1879a59`
- Run: `run_121bc870861248cc827361d8e1879a59`
- State: `succeeded`
- `report_acceptance_pass=true`

## Exact A/B reconciliation

| Evidence | A4 | B4 |
| --- | --- | --- |
| candidate events | 36,820 | 36,820 |
| executable events | 36,022 | 36,022 |
| not executable | 798 | 798 |
| positive | 4,519 | 4,519 |
| neutral | 4,159 | 4,159 |
| failure | 25,963 | 25,963 |
| unavailable | 1,381 | 1,381 |
| censored | 0 | 0 |
| three-class events | 34,641 | 34,641 |
| Event content digest | `sha256:80dc078199a94dc4c5009de1b4a211ee400561a97993a71f61a0dc0e06b745ac` | same |
| Event manifest SHA | `sha256:8de97ef3da15feda79461630f9740ce098925992b77a046f3f83841d3c196316` | same |
| Label content digest | `sha256:02fa0057936d9b59d0058325448b7ef638cda3ef28803cd8306e43b0675c1580` | same |
| Label manifest SHA | `sha256:c55d66737270526d561c52e7f0719f21e4566b81c477654b4a6895d337b9340d` | same |
| Report content digest | `sha256:d4fe89b80d8d2b1322a99669935a9eff1758c7857cd5f2af06607347e99db158` | same |
| Report manifest SHA | `sha256:daec6f84da1b4535b74f8f48b42f424ca7991a32980c2b9a02cbb00ec95b1184` | same |

There is no unexplained candidate, label, or report identity drift between the two accepted Jobs.

## Accepted leading differentiators

The same three leading features were selected in the same order in both Runs:

1. `risk.realized_volatility20`
2. `price.breakout20`
3. `price.sma20_to_sma60`

These are descriptive research differentiators under the frozen P4 report policy. They are not a
trading rule, model promotion, execution signal, or permission to tune the candidate rule after
seeing outcomes.

## Acceptance decision

`P4-REPORT-001` passes its production A/B gate:

- same production Worker revision;
- same governed input digest;
- same Event and Label identities;
- byte-identical report content and manifest identities;
- same three leading feature ids and ordering;
- true report acceptance flag in both independently sealed Run v3 manifests.

`P4-REPORT-001` is accepted. Arbitration and permanent-blind access remain blocked by their
existing authorities.
