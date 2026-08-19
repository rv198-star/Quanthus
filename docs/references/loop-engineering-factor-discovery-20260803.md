# Loop Engineering For Automated Factor Discovery

| Field | Value |
| --- | --- |
| Reference status | Non-governing external research input |
| Article | 中金：《大模型系列（7）：基于 Loop Engineering 的自动化因子发现引擎》 |
| Publication date | 2026-08-03 |
| Public excerpt | <https://mp.weixin.qq.com/s/hrKdYATh_9rdASVAdjGymg> |
| Private source snapshot | [Full supplied excerpt](cicc-loop-engineering-factor-discovery-20260803-source.md) |
| Authors named in excerpt | 郑文才、周萧潇、刘均伟 |
| Reviewed | 2026-08-05 |

The supplied excerpt is retained in this private repository for internal research at the
repository owner's direction. It remains subject to the source's copyright and redistribution
restrictions and is not an approved Quanthus design or validation artifact.

## Summary

The article describes a stateful `generate -> review -> validate` loop for price-volume factor
search. Candidate expressions come from mutation, crossover, parameter perturbation, random
generation, and LLM-guided mechanism hypotheses. Atomic checkpoints retain tested hashes,
survivors, lineage, optimization state, and iteration progress. Generation and semantic review use
separate LLM roles; deterministic code performs expression checks, backtests, multi-metric
filtering, and IC-correlation deduplication. Failure patterns and frequently reused expression
subtrees redirect later exploration.

The reported experiment searched 16,939 candidates over roughly 600 trading days and retained 69.
Those reported survivor metrics are study claims, not independent Quanthus evidence.

## Applicability To Quanthus

Useful engineering patterns are:

- immutable checkpoint and resume behavior;
- a typed expression DSL with unit, PIT, and boundary checks;
- separate generator, semantic reviewer, and mechanical validator responsibilities;
- a complete attempt ledger, including rejected candidates and parent/mutation lineage;
- mechanism-family coverage and explicit exploration/exploitation budgets; and
- bounded structural-diversity controls.

The appropriate role is a candidate-generation laboratory after P4 freezes event, target, and
sample semantics and before P5 performs governed research scoring. It may emit explicit proposals;
it must not directly mutate the active feature registry, expert/veto policy, strategy policy, or
promotion decision.

## Validation Boundary

The article's validation procedure is not adopted as Quanthus validation policy. Repeatedly using
visible annual returns, Sharpe thresholds, or other metrics turns those observations into a search
objective. More metrics do not by themselves correct multiple testing, IC correlation does not
establish mechanism independence, and an LLM explanation remains a hypothesis rather than
evidence.

For Quanthus:

- search is confined to authorized development data;
- arbitration, permanent-blind, and simulation outcomes are unavailable to the search loop;
- trial accounting and error control apply to candidate families and all attempts, not only
  survivors;
- selected hypotheses freeze before arbitration; and
- survivors still require portfolio-constrained replay, costs, tradability, capacity, exposure,
  attribution, shadow evidence, and existing promotion governance.

Governing boundaries remain in
[`03-label-feature-system.md`](../design/lld/03-label-feature-system.md),
[`06-event-library.md`](../design/lld/06-event-library.md),
[`07-research-scoring.md`](../design/lld/07-research-scoring.md),
[`08-validation-governance.md`](../design/lld/08-validation-governance.md), and
[`strategy-attribution.md`](../design/lld/10-monitoring-attribution/strategy-attribution.md).
