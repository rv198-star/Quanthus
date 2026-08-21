# Expert Strategy Layer MVP Implementation Map

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Design status | approved |
| Delivery gate | `P3-EXPERT-001` and `P3-EXPERT-002` accepted; later children dependency-gated |
| Governing work | `P3-EXPERT-000` |
| Review | `implementation-readiness-review.md` |

## 1. Purpose

This index turns the approved Expert Strategy Layer boundary into seven independently executable
work items. Each child owns one acceptance boundary, its implementation paths, failure semantics,
verification matrix, human decision gates, and rollback. The parent remains authoritative for the
cross-cutting expert model, partition rules, lifecycle semantics, and product stop conditions.

## 2. Frozen MVP shape

- The first formal topology has exactly two active strategy experts. System guardians are not
  counted as strategy experts and cannot be disabled by research attribution.
- `P3-EXPERT-003` owns the first market assessment expert. `P3-STRAT-002` owns the second,
  policy-only position-risk expert and the executable formal topology. This closes the previous
  implementation ownership gap behind the phrase "two to four experts."
- The two action-changing expert mappings permit complete coverage by two mandatory
  single-expert interventions and one predeclared joint intervention. No combination search is
  admitted.
- Historical evaluation reads one exact effective DWD snapshot directly and executes economic
  comparisons through P2. It does not create or maintain a strategy-specific ADS.
- PostgreSQL owns Job/Run state, summaries, and immutable artifact references. Observation and
  settlement rows remain compact immutable analytical artifacts rather than database rows.
- One existing PostgreSQL Worker deployment executes both candidate replay and expert evaluation
  serially at concurrency one. A new broker, cache, event bus, registry, or always-running service
  is outside this milestone.
- One expert-evaluation Job accepts a canonical non-empty set of evaluation specs so the same
  contract serves the single-expert proof and the later two-expert topology. Operational attempt
  metadata is not part of deterministic analytical identity.
- Development is the only historical partition. Arbitration and permanent-blind partitions stay
  unopened, including during target selection, attribution, acceptance, and rollback.
- MVP closure records a lifecycle decision plus an explicit Shadow disposition. Negative and
  replacement decisions prove the current version is disabled instead of forcing a handoff.

## 3. Dependency route

```text
P3-EXPERT-001 --> P3-EXPERT-002 --> P3-EXPERT-003 --> P3-STRAT-002
                                      ^                    |
                                      |                    v
                                P4-LABEL-001           P3-ATTR-001
                                                           |
                                                           v
                                                      P3-EXPERT-004
                                                           |
                                                           v
                                                      P3-EXPERT-005
```

`P3-FEAT-001`, `P3-REPLAY-001`, `P3-POS-003`, and the accepted `PK-WORKER-000` remain external
prerequisites at the nodes that consume them. A downstream issue becomes ready only when all
predecessors are accepted and its own human decision gate has a recorded, digest-bound answer.

## 4. Child map

| Work item | Owned result | Detailed design | Current gate |
| --- | --- | --- | --- |
| `P3-EXPERT-001` | Additive v2 contracts and canonical identities | `evaluation-protocol-v2.md` | accepted; evidence: `docs/acceptance/p3-expert-evaluation-protocol-v2-20260819.md` |
| `P3-EXPERT-002` | PostgreSQL Worker evaluation and immutable publication | `worker-evaluation-pipeline.md` | accepted; evidence: `docs/acceptance/p3-expert-evaluation-worker-20260820.md` |
| `P3-EXPERT-003` | First target, resolver, assessment expert, and scorecard | `first-target-resolver-expert.md` | exact 2020-2021 engineering-proof target decision approved; implementation in progress |
| `P3-STRAT-002` | Executable two-expert strategy topology | `first-formal-topology.md` | blocked by `P3-EXPERT-002` and `P3-EXPERT-003` |
| `P3-ATTR-001` | Bounded formal-strategy counterfactual evidence | `../../10-monitoring-attribution/formal-strategy-attribution.md` | blocked by `P3-STRAT-002` |
| `P3-EXPERT-004` | Read-only candidate-detail evidence projection | `candidate-detail-evidence.md` | blocked by `P3-ATTR-001` |
| `P3-EXPERT-005` | Development acceptance and forward-Shadow disposition | `mvp-shadow-handoff.md` | blocked by `P3-EXPERT-004` |

## 5. Human authority

Three semantic decisions deliberately remain human-owned:

1. `P3-EXPERT-003` selects and signs the exact target, horizon, eligible population, primary
   metric, and stop conditions before any result-bearing resolver run.
2. `P3-STRAT-002` signs the exact strategy card, expert policy mappings, expected decision
   changes, intervention set, and resource budget before the development baseline.
3. `P3-EXPERT-005` records the lifecycle decision and compatible enabled/disabled Shadow
   disposition after reviewing the complete evidence package.

Scripts may validate the shape and digest binding of those records. They cannot choose an answer,
infer approval from a passing test, or promote a version.

## 6. Readiness rule

An issue is implementation-ready when its predecessors are accepted, every required decision input
is available, and its child design has no unresolved interface ownership. A green unit test is not
enough to close an issue: the acceptance artifacts named by that child must exist, be digest-bound,
and be reviewed against the parent invariants.
