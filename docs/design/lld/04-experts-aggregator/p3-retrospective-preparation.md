# P3 fixed-snapshot retrospective preparation

| Field | Value |
| --- | --- |
| Parent | [Expert Strategy Layer](expert-strategy-layer.md) |
| Design status | approved for bounded retrospective continuation; real input admission remains separate |
| Delivery gate | blocked for real source/support admission and result execution |
| Work items | P3-STRAT-003 / #317 and consumer admission #412; source defects P10 #555 / #556 |
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

## Separate execution boundary (2026-10-23 continuation)

The Owner subsequently requested continuing until P3 can actually start. The additive
`config/research/p3-strat-003/retrospective-execution.v1.json` records that continuation;
its admitted consumer-receipt list is empty while the source/support gates are unresolved.
A caller flag, source READY or synthetic success cannot populate it or activate protected
results. An admitted receipt must bind the exact prepared input/support/industry identity,
actual information cutoff, unchanged business window and primary/cold role. Original strict
A/B/C/D acceptance and P6 remain unchanged.

`src/quanthus/expert_strategy/p3_retrospective_replay.py` connects the prepared candidates,
common mask and existing feature panel to the accepted B/T/R/TR allocation, experts,
P2 matching, fees and ledger. Separate retrospective event/frame/signal types retain actual
snapshot knowledge and business decision time. Candidate-source knowledge also remains
explicit. The ordinary P2 engine rejects retrospective signals; its strict clock guard is
retained and checked at runtime. The retrospective engine only admits its bound nonofficial
purpose/snapshot and never constructs a backdated strict signal or ExpertObservation.

One job-local compressed session index, sorted by date/security, is built from the journal.
Every day and all four variants reuse it and the one prepared feature panel. The current day
is the only in-memory session cache; a cold reproduction rebuilds its own index. Industry
bytes and preparation members are pinned; input changes fail closed. Write/RSS/spill/wall/free
space checks bound the job. Success writes COMPLETE last; a failed variant writes FAILED
without presenting partial variants as a completed comparison.

The current connected path requires explicit qualified no-action support. Known or unknown
action boundaries without admitted normalized terms stop the affected held-action calculation,
not a forced sale or invented zero action. The actual snapshot has unresolved action
composition/revision/no-action semantics, so normalization must first be delivered by P10
#556 and then connected/accepted at the consumer boundary. This design does not certify that
path through a synthetic zero-action example. Similarly no session clock is inferred and
no NORMAL state is treated as proven non-ST merely from its label.

`tests/unit/test_p3_retrospective_replay.py` exercises all four variants with actual synthetic
fills and reconciled ledgers, equality of economic arithmetic with the strict synthetic control,
late-clock isolation, unknown held-action rejection, output reuse refusal and two relocated
new-process executions under kernel filesystem/network restrictions. These are engineering
results, not any of the four real protected-window comparisons.
