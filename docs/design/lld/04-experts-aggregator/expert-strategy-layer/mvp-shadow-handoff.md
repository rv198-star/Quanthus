# P3-EXPERT-005: MVP Acceptance and Forward-Shadow Disposition

| Field | Value |
| --- | --- |
| Parent | `../expert-strategy-layer.md` |
| Work item | `P3-EXPERT-005` |
| Design status | approved |
| Delivery gate | blocked only by `P3-EXPERT-004`; other predecessors accepted |
| Implementation readiness | ready after dependencies and human lifecycle decision |
| Authority after closure | forward Shadow only when explicitly enabled; no promotion, notification, or execution |

## 1. Outcome and boundary

Execute and independently verify the complete development loop, record one human lifecycle
decision for the frozen formal strategy/expert versions, and resolve whether those exact versions
enter daily forward Shadow or remain disabled. This issue closes the MVP product loop on favorable,
negative, or inconclusive evidence; it does not claim forward performance, open a P6 gate, reveal
sealed partitions, notify an operator, or authorize capital.

## 2. Required development package

The acceptance package binds one exact chain:

- DWD Base plus effective corrections/increments and development split;
- target decision/card, resolver, scorer, and complete evaluation-spec set;
- both expert versions and every policy mapping;
- formal strategy card and native topology;
- Worker Job/Run and deterministic rerun;
- observation, settlement, sample-ledger, and scorecard artifacts;
- baseline, two single interventions, joint intervention, receipts, and attribution release;
- candidate-detail API/Web projection and its exact product-candidate binding;
- resource cost, limitations, stop-condition disposition, and rollback manifest.

The package includes a machine audit showing every read date and partition. Any arbitration or
permanent-blind access is an acceptance failure even when results otherwise look favorable.

## 3. Lifecycle decision

A human reviews the complete package and chooses exactly one parent-approved decision:
`keep`, `narrow_with_new_version`, `remap_policy`, `merge`, `retire`, or `inconclusive`.

The append-only `ExpertLifecycleDecision` records actor identity, decision time, rationale,
limitations, exact assessment, policy, target, resolver, DWD, split, strategy, scorecard,
attribution, and acceptance-package digests. It also records a `shadow_disposition` with
`enabled`/`disabled`, a reason code, effective boundary, and either a bounded observation budget or
a disable receipt. Shape validation and digest closure are automated; the choice and rationale are
not.

The compatibility rule is fail-closed: `keep` requires `enabled`; `inconclusive` permits either
state but requires a finite observation budget when enabled; `narrow_with_new_version`,
`remap_policy`, `merge`, and `retire` require `disabled` for the current version. Replacement
decisions create a later proposal and never mutate the accepted version.

## 4. Forward-Shadow operation

When the disposition is enabled, the handoff adds the exact accepted strategy/expert versions to a
static Shadow schedule configuration. After an effective DWD increment is verified, the existing
node-local controller invokes a Quanthus submission command. The command inserts a deterministic
PostgreSQL Job; it does not execute analysis in the scheduler or API process. When disabled, the
version is absent from the schedule and the submission command rejects its identity.

For each DWD release the Worker may run:

1. `forward_observation` for newly eligible T-close subjects;
2. `mature_settlement` for prior observations whose frozen horizon is complete.

Idempotency binds operation, strategy, the complete evaluation-spec set, DWD release, and
observation date. Forward settlement appends sidecars and never rewrites an observation or
development scorecard. The same one-Worker, concurrency-one deployment is used until measured queue
contention meets a separately approved expansion trigger.

No email, push, broker, Order, Fill, review transition, governance transition, or execution
authorization is emitted. Shadow data becomes later evidence and cannot retroactively change the
development acceptance statement.

## 5. Inputs and owned paths

The issue owns or may extend:

- `src/quanthus/expert_evaluation/lifecycle.py`
- `src/quanthus/expert_evaluation/cli.py`
- the lifecycle-decision package update and serving-entry advance in
  `config/product-expert-evidence.v1.json`; `P3-EXPERT-004` continues to own its schema and reader
- `config/expert-evaluations/shadow/`
- one narrow `ops/bin/` submission wrapper and node-local service/timer hook
- `tests/acceptance/test_expert_mvp_acceptance.py`
- `tests/platform/test_expert_forward_shadow.py`
- one immutable `docs/acceptance/` record created by the implementation run

It does not modify DWD build logic, move warehouse schedules into PostgreSQL, or add a generic
scheduler service.

## 6. Implementation route and acceptance sequence

1. Freeze every semantic/configuration digest and run the complete development evaluation.
2. Repeat the same invocation independently and compare canonical semantic bytes and manifests;
   Job ids, claim generations, progress receipts, and timestamps are excluded from this comparison.
3. Verify the attribution baseline and three bounded interventions.
4. Verify candidate-detail desktop/mobile presentation against artifact sources.
5. Audit resource time, storage, human review cost, stop conditions, and zero sealed access.
6. Obtain and seal the human lifecycle decision plus compatible Shadow disposition.
7. Publish a new immutable evidence-package manifest containing the decision/disposition digests,
   atomically advance the exact candidate serving entry, and verify the decided Web/API projection.
8. For `enabled`, perform a one-date forward-Shadow submission, Worker execution, restart/retry
   drill, mature-settlement dry boundary, and disable/re-enable rollback. For `disabled`, prove the
   schedule has no binding, submission is rejected, and no Job exists for the version.

## 7. Failure semantics and stop conditions

Any digest drift, nondeterministic semantic artifact, incomplete ledger, attribution mismatch,
UI/source disagreement, missing or incompatible decision/disposition, Shadow execution edge, or
sealed-partition read blocks closure. A negative expert result, a declared stop condition, or a
resource finding is not an infrastructure failure; it must produce an honest lifecycle decision and
a disabled current version where required.

When a parent stop condition fires, the accepted action is simplify, retire, or pause. This issue
cannot add experts, search mappings, extend data scope, or automate tuning to rescue the result.

## 8. Verification and acceptance

Tests and operational evidence cover deterministic semantic rerun, immutable decision append,
decision/disposition compatibility, exact serving-entry advance, decided-page provenance, enabled
duplicate submission, cancellation, lease loss, stale generation, restart recovery, observation
then mature settlement, disabled submission rejection, zero ordering side effects, and schedule
rollback. The acceptance record names all commands, artifact digests, runtime revisions, costs,
limitations, human decision, and forward-Shadow disposition.

`P3-EXPERT-000` closes only when every child Issue is accepted and this record passes an independent
conformance review. Forward evidence remains open-ended operational data rather than a reason to
keep the Epic open.

## 9. Rollback

Remove or disable the static Shadow schedule binding and reject new submissions for the affected
version. Running Jobs may be canceled through the accepted contract. Published observations,
settlements, decisions, and acceptance evidence remain immutable. Candidate replay, DWD operation,
and v1 strategies are unaffected.
