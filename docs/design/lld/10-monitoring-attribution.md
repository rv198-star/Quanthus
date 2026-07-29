# LLD 10: Monitoring and Contribution Attribution

| Field | Value |
| --- | --- |
| Phase | Instrumentation in P2; reports after P3 |
| Design status | approved |
| Delivery gate | accepted-conditional |
| Implementation status | P2 event contracts accepted; `P3-FUNNEL-001` reconciliation contracts accepted; fixture funnel populated; attribution and recurring reports are proposal-triggered |
| Gate source | `config/p3-gates.v1.json` |
| Governing requirements | FR-AUDIT-001 through FR-AUDIT-005 |

## 1. Goal

Measure where risk was avoided, where opportunities were captured or missed, and how profit
quality changed. Monitoring is an event contract, not a dashboard added after the fact.

## 2. North-star metrics

| Metric | Official interpretation |
| --- | --- |
| Risk avoided | Capital-constrained baseline minus policy-removed rerun loss |
| Opportunity captured | Executed and adequately sized true opportunities under fixed capital |
| Profit growth | Cost-adjusted PnL quality, including base/add contribution and capital-day use |

Diagnostic children include veto outcomes, exit timeliness, constrained exposure, capture
rate, false-veto cost, cash/cooldown misses, add sufficiency, payoff ratio, and hold efficiency.

## 3. Decision log

One record exists for every candidate and held-position review. It includes time, security,
unit, lifecycle stage, complete expert outputs, aggregate score, vetoes/constraints, proposed
and final action, reason codes, snapshot, policy versions, and linked order ids. Shadow outputs
use the same schema and are explicitly non-executing.

The P2 `DecisionEvent` persists these fields as opaque upstream evidence for accepted, rejected,
and shadow dispositions. Accepted records resolve their Signal and exact Order ids in an immutable
run; rejected and shadow records are structurally forbidden from linking orders. P3 owns the
expert meanings and production of one record per review. Evidence:
`docs/acceptance/p2-round-trip-observability-slice-20260728.md`.

The bounded P3 comparison contract consumes one non-shadow baseline and one or more shadow
`DecisionEvent` values from the same review. It reports action, score, expert-output, veto,
constraint, reason, and policy-version differences with deterministic ids and digests. It does not
persist a `DecisionEvent` or create any execution artifact; P2's immutable audit store remains the
owner of decision persistence. Evidence:
`docs/acceptance/p3-shadow-divergence-contract-slice-20260728.md`.

The strategy-neutral replay now has a separate immutable analysis package. It persists replay
decision traces, equity points, trades, metrics, and final state, then binds their checksum to the
exact P2 run manifest and event stream. This closes replay reproducibility but does not substitute
for the complete expert/veto/constraint fields of a concrete P3 `DecisionEvent`. The governed
candidate runner now produces those concrete records for its own frozen vocabulary; generic replay
and other strategies do not inherit them. Evidence:
`docs/acceptance/p3-strategy-replay-integration-slice-20260728.md` and
`docs/acceptance/p3-governed-strategy-development-20260729.md`.

### Notification log

An operator notification is emitted only from a persisted decision or OrderIntent. It records
notification and signal ids, execution profile/window ids, content hash, provider, attempt,
timestamps, terminal delivery state, and bounded reason code. Provider credentials and recipient
addresses do not enter the event record.

Delivery success never creates an Order or Fill. Delivery failure does not change the decision;
retry preserves one logical notification identity. A manually entered trade is actual only after a
separate operator execution report is accepted under P7. Until then, notification outcomes and the
paper ledger remain separate evidence streams.

The bounded P2 store now atomically publishes an immutable run event stream, then accepts a
notification only when its run contains exactly one matching persisted Signal with the same
profile/window. Attempts append under one immutable notification identity and checksum chain.
This is storage and contract evidence only: it does not send email, persist recipient data, or
implement operator execution reports. Evidence:
`docs/acceptance/p2-audit-window-slice-20260728.md`.

### Operator execution and reconciliation log

P7 accepts an append-only `OperatorExecutionReport` linked to one advice and signal. Its outcome is
`not_executed`, `executed_as_advised`, `partially_executed`, or `executed_modified`; it retains zero
or more actual fill rows, fees, evidence reference, operator/report times, and any deviation reason.
A correction uses `supersedes_report_id` and preserves both versions.

Reconciliation compares advice, notification, paper execution, and accepted actual fills without
making any stream authoritative for another. It reports response/skip rate, action and size
divergence, timing delay, price/slippage difference, fee difference, and unreconciled reports.
Only accepted report events feed the actual ledger; paper and hypothetical metrics remain labeled.

## 4. Hypothetical ledger

Rejected and near-threshold signals are followed under the frozen baseline entry/exit policy
for at most 20 sessions. These isolated paths diagnose a decision but ignore competition for
capital and must never be summed as official risk/opportunity money.

The P2 diagnostic runner enforces the 20-capital-day bound, requires an explicit rejected or
near-threshold source decision and baseline version, and marks output as not capital constrained
and not aggregatable as official. It reuses the accepted matcher/accounting path rather than a
second pricing engine. P3 still supplies the frozen baseline entry/exit decisions and later owns
the full capital-constrained attribution rerun.

Official monetary attribution reruns the full strategy under identical capital and execution
constraints with one module removed or neutralized. The reported value is the delta from the
versioned baseline. Module deltas are not additive because interactions exist; material claims
receive pairwise reruns or an explicit interaction caveat.

## 5. Opportunity funnel

Fixed stages are candidate to score, score to veto, entry to add, and hold to exit. Each stage
reconciles input/output counts and joins realized or diagnostic outcomes. A funnel that does
not reconcile to the event total fails publication.

`src/quanthus/opportunity_funnel.py` now implements the bounded reconciliation contract. A caller
must declare the exact input-event set for all four fixed stages, including an explicit empty set
for a stage with no input. Every source event receives exactly one advanced, stopped, or unavailable
classification and exactly one realized, diagnostic, or pending outcome state. Missing, extra,
duplicate, or out-of-period observations fail publication. When one stage output is also present as
another stage input, the link must enter the next fixed stage and retain opportunity identity.
Input order cannot change the report id, digest, stage summaries, or canonical bytes. Evidence:
`docs/acceptance/p3-opportunity-funnel-slice-20260728.md`.

The reusable reconciler still consumes already-produced evidence, declares
`execution_authorized=false`, and cannot create a decision, signal, order, fill, position
transition, or notification. The governed runner supplies a populated capability-fixture funnel:
101,549 source events reconcile across all four stages, including 48,591 candidate/score inputs,
535 entry-allocation inputs, and 3,832 held reviews. Twenty held-review outcomes remain explicitly
pending because one legal Trial position is open at the final mark; pending is reconciled, not
silently treated as a closed trade. This closes populated-funnel production for the P3 capability
fixture. Constrained attribution and dashboard work are deferred until a formal strategy supplies
predeclared module-removal questions and recurring monitoring semantics.

## 6. Reporting cuts

Reports support month/quarter, market regime, lifecycle stage, expert/aggregator/position rule,
and policy version. The first dashboard row always has exactly the three north stars. Funnel,
contribution matrix, and failure ranking appear below it.

## 7. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P2-OBS-001 | Define decision/shadow/notification log schema | P2 events | Accepted, rejected, delivered, and no-implied-fill fixture |
| P2-HYP-001 | Implement diagnostic hypothetical ledger | Matcher/exit policy | 20-session path fixture |
| P3-FUNNEL-001 | Implement reconciled opportunity funnel | P3 vertical slice | Count reconciliation |
| P3-ATTR-001 | Implement constrained remove/neutralize reruns when a formal strategy requires them | Trusted engine and predeclared attribution questions | Reproducible contribution matrix |
| P3-DASH-001 | Produce three-layer monitoring report for a recurring formal strategy | Funnel/attribution and reporting cadence | Monthly report fixture |

## 8. Acceptance

The reusable P2/P3 boundary is accepted when every fill and rejection resolves to a complete
decision log and all populated funnel stages reconcile exactly. A later formal-strategy reporting
release additionally requires reproducible monthly north stars and a manifest-bound contribution
matrix. Notification and actual-ledger acceptance remain under their P6/P7 gates.
