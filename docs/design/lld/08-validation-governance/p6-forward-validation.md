# P6 Forward Validation and Paper Operation

| Field | Value |
| --- | --- |
| Parent | `../08-validation-governance.md` |
| Design status | approved |
| Delivery gate | `P6-GOV-001` accepted; `P6-DATA-001` held blocked pending explicit owner value review |
| Governing work | `P6-000` |
| Accepted entry proposal | `config/forward-validation/entry/p6-prospective-mvp-entry.v1.json` |
| Initial execution profile | `close-to-next-open-v1` only |
| Research-model dependency | P5 deferred; not a P6 predecessor |
| Execution authority | paper only; no notification, broker, or live capital |

## 1. Outcome and boundary

P6 must prove that one frozen expert strategy can operate prospectively under real information
arrival, not merely reproduce a favorable historical result. After activation, each accepted DWD
increment drives one durable Worker cycle that records expert assessments and the aggregate action
before future outcomes are known, later settles the declared targets, updates role-specific
scorecards, and projects the same frozen decision into an isolated P2 paper ledger.

The first P6 program remains deliberately narrow:

- one explicitly authorized formal strategy version, initially expected to be
  `candidate.p4-trend-risk@1.0.1` unless the entry decision chooses otherwise;
- the strategy's exact admitted expert topology, with no requirement to build the remaining
  candidate experts;
- the accepted daily `close-to-next-open-v1` profile and fixed-capital-unit accounting;
- one PostgreSQL Replay Worker at concurrency one until measured queue delay justifies expansion;
- one read-only product projection for forward evidence and paper-account state.

The first program is a **prospective mechanism MVP**, not a claim that the current two-expert
catalogue or the current Stage vocabulary is complete. Its primary question is whether the frozen
DWD -> expert -> aggregator -> Shadow/paper -> settlement chain remains outcome-blind, auditable,
reproducible and authority-safe under real future information arrival. Expert quality and economic
results are still recorded, but favorable return is not the primary mechanism-acceptance gate.

P6 does not train a model, search parameters, reopen P3/P4 acceptance, label already-known 2026
history as prospective, reveal the permanent blind partition, send advice, connect a broker, or
authorize real capital. P5 may resume later as an optional research track, but no P5 work item is a
predecessor of this program.

## 2. Baseline and explicit gaps

The repository already has immutable expert observations, settlements, scorecards, lifecycle
decisions, a disabled Shadow schedule, PostgreSQL Job admission, generation-fenced Workers, the P2
paper referee, and candidate-detail evidence projection. The accepted MVP remains
`inconclusive + Shadow disabled` because no prospective observation budget was authorized.

The existing Shadow submission is an admission proof, not an operational loop: it inserts the
placeholder `expert-forward-shadow` kind into queue `expert`, while production Workers claim only
the static `replay` and `event` queues and no handler consumes that kind. P6 must close that gap
without inventing a generic Worker registry or another transport.

## 3. Entry decision

`P6-GOV-001` publishes one immutable `P6EntryProposal` before any result-bearing P6 read. The first
accepted proposal is `config/forward-validation/entry/p6-prospective-mvp-entry.v1.json`; its
acceptance grants no activation or downstream implementation authority. It binds:

- candidate, strategy, expert, policy, target, resolver, scorecard, P2 profile, code, and DWD
  authority identities;
- the effective activation boundary and the first admissible future DWD release rule;
- observation budget value and unit, minimum independent trigger rule, target-maturity tail, and
  maximum calendar duration;
- primary and guardrail metrics, regime slices, expected behavior, controls, stop conditions, and
  rollback;
- the paper-account capital-unit policy and non-compounding rule;
- explicit exclusions for P5, arbitration, permanent blind, notification, broker, and live capital.

The observation budget is not a raw row count. A cross-section containing correlated securities
cannot manufacture independent evidence. The owner-approved first proposal uses three
non-overlapping 22-trading-session market blocks, or 66 collection sessions, followed by the frozen
20-session target-maturity tail. Cross-sectional securities, candidates, observations, decisions
and fills do not increase independent-budget progress. The maximum wall-calendar duration from the
first official market date is 150 calendar days. Low activity closes as an honest inconclusive
result; the budget cannot be extended after result exposure merely to obtain more cases.

The first official market date is not created by `P6-GOV-001`. A later `P6-ACTIVATE-001` decision
must be published first; only the first complete trading session strictly after that decision's
Asia/Shanghai market date whose DWD release is READY and deeply verifiable may enter the official
budget. Earlier 2026 data remains rehearsal-only.

The accepted P3 lifecycle record stays immutable. P6 publishes a new package and append-only entry
decision; it never edits the prior `inconclusive + disabled` bytes or silently changes the strategy
version.

## 4. Independent state axes

P6 does not compress domain, evidence, Job, and review state into one status field.

| Axis | States | Authority |
| --- | --- | --- |
| Admission | `disabled`, `enabled`, `paused`, `completed` | immutable entry/activation/pause decision plus static binding |
| Evidence | `waiting_first_release`, `collecting`, `settling_tail`, `review_ready` | verified forward Run and closed sample ledger |
| Job | `queued`, `running`, `succeeded`, `failed`, `canceled` | PostgreSQL Job and generation-fenced lease |
| Review | `not_due`, `pending_human`, `continue`, `narrow`, `remap`, `retire`, `inconclusive` | append-only human review decision |

A failed Job does not retire a strategy. A poor result does not mean infrastructure failure. A
paused admission does not rewrite observations. UI percentages remain operational stage progress
and never imply evidence maturity or promotion.

## 5. Prospective data boundary

Simulation data begins in 2026 by policy, but evidence becomes prospective only after the exact P6
activation decision is published. Earlier 2026 data may support a clearly labeled engineering
rehearsal; it cannot count toward the official observation budget.

Every cycle binds one deeply verified DWD effective-view identity, market date, knowledge cutoff,
publication/visibility time, code revision, and entry-proposal digest. The access ledger records
every input partition and date. Before `P6-REVIEW-001`, arbitration 2022-2024 remains sealed and
permanent blind 2025 remains sealed without exception.

Late DWD corrections never rewrite a prior observation. A correction creates an append-only
data-quality disposition that either preserves, degrades, or censors the affected evidence under a
versioned rule. Missing or invalid data blocks new paper entries and records an alert; it does not
invent data or force a sale.

## 6. Worker and artifact flow

P6 adds one admitted `quanthus.p6-forward-cycle.v1` kind to the existing static Replay Worker and
`queue=replay`. The node-local DWD controller may submit the immutable envelope only after the DWD
increment is READY; it performs no analysis. API and Web processes never read DWD or run the cycle.

One Job builds each semantically identical prerequisite once. Its stage graph is:

1. verify entry proposal, activation, DWD release, cutoff, and prior paper-ledger head;
2. build one Job-local effective-view projection and reuse it for all downstream stages;
3. settle eligible prior paper intents at the current session open through P2;
4. produce outcome-blind expert observations and the aggregate T-close decision;
5. append a Shadow record with no Signal, Order, Fill, notification, or execution edge;
6. project the exact aggregate decision into a separate paper Signal/intent for the next open;
7. settle matured forecast targets and close the sample ledger where possible;
8. build expert, aggregator, strategy, and paper-account scorecards;
9. seal one immutable forward Run and publish only a bounded PostgreSQL Run reference.

Job identity covers the entry proposal, admission decision, strategy/evaluation closure, DWD
effective view, observation date, prior paper-ledger head, code, and schema. Exact retries reuse the
same result. Conflicting output, stale generation, lease loss, partial publication, or semantic
input drift fails closed. Large evidence remains file-first and immutable; PostgreSQL is not a
second analytical result store.

## 7. Scorecards

Metric cards are frozen before the first official observation. P6 does not create one universal
expert score or cross-target leaderboard.

| Role | Required evidence |
| --- | --- |
| Assessment/scoring expert | target error or calibration, monotonicity, coverage, abstention, lead time, regime stability |
| Veto | loss-event recall, veto precision, false-veto opportunity cost, portfolio-constrained avoided loss |
| Constraint | activation rate, matched risk change, return cost, drawdown and concentration effects |
| Aggregator | consensus/disagreement outcomes, final-action quality, blocked-new behavior, regime slices |
| Complete strategy | fixed-unit return, drawdown, turnover, fill/reject/carry rates, exposure, divergence from honest replay |
| Paper operation | balanced cash/position ledger, pending intents, fills, costs, reconciliation, no compound-capital claim |

Dependence-aware confidence and effective sample size accompany headline counts. Every material
loss is classified as expert error, missed veto, constraint error, aggregation/action mapping,
execution assumption, or data-quality incident. An unclassified material loss blocks review.

## 8. Paper-operation boundary

Forward Shadow and paper operation are sibling evidence lanes. Shadow records what the frozen
strategy would decide and remains disconnected from orders. The paper lane consumes the exact
sealed aggregate decision through P2's accepted Signal, OrderIntent, Order, matcher, Fill, cost,
and fixed-unit ledger contracts.

Paper state uses its own ids and `paper` authority marker. A paper Fill cannot become an actual
Fill, execution authorization, notification delivery, or operator-reported action. The initial
account does not compound profits and does not use the inactive two-window profile.

## 9. Product view

The existing candidate detail gains one read-only Forward Validation section rather than a new
dashboard product. It shows:

- candidate/version, admission and evidence states, activation boundary, budget unit/progress,
  longest maturity tail, and stop-condition status;
- last admitted DWD release, latest Job, pending/settled/censored counts, and next due settlement;
- role-specific expert and aggregator scorecards with limitations and regime slices;
- paper cash, positions, pending intents, fills, fixed-unit performance, drawdown, and reconciliation;
- immutable proposal, activation, Run, scorecard, paper-ledger, incident, and review digests.

The page does not edit parameters, reveal sealed data, approve promotion, manufacture metrics, or
submit DWD analysis from the request process. Existing Job cancellation may remain available under
its current authority; admission pause/disable is a separate append-only governance command.

## 10. Activation and calendar-time acceptance

Implementation completion is not activation. `P6-ACTIVATE-001` requires all engineering children,
an exact production deployment, a signed entry decision, a finite budget, a clean disabled-state
rehearsal, and a rollback drill. It then publishes a new enabled binding and accepts only the first
post-activation DWD release as official prospective evidence.

Production activation was enabled on 2026-09-19 and is recorded by
`docs/acceptance/p6-activate001-production-activation-status-20260919.md`. At that closeout point,
o legal post-activation trading-session release existed, so official collection correctly remained
`0 / 66` with zero official-cycle Jobs. Historical 2026 data remains ineligible for backfill.

The engineering checkpoint closes only when the first legal official cycle and its independent
reproduction pass. The P6 Epic remains open while the budget accumulates. `P6-REVIEW-001` already
exists as GitHub #120 and cannot be accelerated with a historical rerun: it waits for the declared
sessions/triggers and maturity tail, a closed ledger, and a complete paper reconciliation.

At review, a human may continue, narrow with a new version, remap, retire, or remain inconclusive.
An arbitration reveal requires a separate decision inside the review packet and is immutable once
performed. Permanent blind remains sealed. No outcome authorizes P7 automatically.

## 11. Work items and dependency order

| ID | Existing Issue | Outcome | Dependency |
| --- | --- | --- | --- |
| P6-000 | new | Coordinate the bounded P6 program and close only after every child | META-DOC-001, accepted P3/P4 and Expert Strategy MVP |
| P6-GOV-001 | #116 | Freeze entry proposal, expectations, budget, stop rules, and rollback | P6-000 entry authority |
| P6-DATA-001 | #119 | Enforce prospective boundary, DWD identity, access ledger, and correction disposition | P6-GOV-001 plus explicit post-entry owner value review |
| P6-SCORE-001 | #118 | Freeze role-specific scorecards and loss attribution before results | P6-DATA-001, P3-ATTR-001 |
| P6-GOV-002 | #117 | Implement the composite Forward Worker and disabled-state lifecycle rehearsal | P6-SCORE-001, PK-WORKER-000 |
| P6-PAPER-001 | new | Add the isolated P2 paper lane and balanced fixed-unit ledger | P6-GOV-002, P2-ACCEPT-001 |
| P6-WEB-001 | new | Project forward and paper evidence in candidate detail | P6-PAPER-001 |
| P6-ACTIVATE-001 | new | Production activation enabled; complete the first legal official post-activation cycle and independent reproduction | P6-WEB-001 |
| P6-REVIEW-001 | #120 | Complete the time-matured simulation review and any authorized arbitration reveal after the frozen real-calendar budget closes | P6-ACTIVATE-001 plus closed budget |

Only `P6-GOV-001` is implementation-ready at the initial P6 gate. Acceptance of `P6-GOV-001`
creates a mandatory value checkpoint rather than automatic downstream authority: `P6-DATA-001`
remains blocked until an explicit post-entry owner review accepts the frozen prospective-MVP cost,
calendar budget and boundaries. Every later child remains dependency-gated after that decision.

## 12. Failure semantics and rollback

Before activation, any contract, identity, resource, recovery, product-source, or authority defect
blocks activation. After activation, a deterministic data or policy defect pauses new cycles; an
infrastructure failure follows bounded retry and generation fencing. A poor score produces honest
evidence and a review decision rather than automatic tuning.

Rollback disables the static binding and rejects new submissions. Running Jobs may be canceled;
published observations, settlements, paper ledgers, incidents, and decisions remain immutable.
Candidate replay, P4, DWD operation, and accepted historical evidence remain unaffected.

## 13. Program acceptance

`P6-000` closes only when all eight children are accepted. Evidence must prove one genuinely
prospective observation sequence, mature settlement, role-specific scorecards, balanced paper
ledger, read-only product projection, bounded resource/recovery behavior, completed observation
budget, signed review, and zero notification/broker/live-capital authority. A negative or
inconclusive strategy result may close P6 honestly; missing prospective time or incomplete evidence
may not.
