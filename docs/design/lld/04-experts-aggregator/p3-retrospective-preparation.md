# P3 fixed-snapshot retrospective preparation

| Field | Value |
| --- | --- |
| Parent | [Expert Strategy Layer](expert-strategy-layer.md) |
| Design status | approved for preparation only by the Owner on 2026-10-23 |
| Delivery gate | blocked for real source/support admission and result execution |
| Work items | P3-STRAT-003 / #317 and consumer admission #412; source defect P10 #555 |
| Contract | `config/research/p3-strat-003/retrospective-comparison.v1.json` |
| Code | `src/quanthus/expert_strategy/p3_retrospective_preparation.py` |
| Tests | `tests/unit/test_p3_retrospective_preparation.py` |
| Evidence | [Current preparation record](../../../work-items/p3-base24-goal-audit-and-plan-20261022.md#19-回溯比较准备与-p10-数据缺口) |

This additive, purpose-specific design leaves the frozen parent document and every old
request/acceptance byte unchanged. It does not redefine historical PIT or grant real result
execution.

## Scope and behavior


The Owner separately approved preparing the retrospective question in
`config/research/p3-strat-003/retrospective-comparison.v1.json`. It preserves the strict
`P3-STRAT-003` request and its unaccepted B/NOT_RUN D. The new purpose uses the declared
information snapshot to inspect the original frozen business dates; it cannot establish that
the inputs were historically available or support a production/P6 claim.

The result-blind `p3_retrospective_preparation` consumer owns one immutable job-local typed port
journal and one prefix-safe feature materialization. Candidate rules, the common complete-case
mask and all four variant definitions reuse their existing numeric rules. The journal preserves
Decimal scale and real source/business clocks; each variant binds the same full input, support,
static-industry, ordinary-code/config and runtime identities. Neither a schema/digest nor a
native executable flag qualifies missing listing, normalized-action or execution support.

`PREPARATION_COMPLETE.json` attests only this engineering materialization, and is deliberately
distinct from a data READY or research admission. Cold checking takes an externally bound
manifest digest and reads only the package and ordinary code/config. The strict EventRecord,
StrategyReplayFrame, BacktestSignal and legacy P2 entry points keep their original clock gates;
the preparation cannot relabel its late-clock records as those types. An explicit two-clock
simulation boundary and purpose-specific source/support admission remain prerequisites for a
future result run. No result execution is authorized by this preparation approval. Shared
calendar defects go to P10 #555; historical-PIT evidence remains with P10 #474. Only the affected
step waits; independent consumer and synthetic engineering may continue.
