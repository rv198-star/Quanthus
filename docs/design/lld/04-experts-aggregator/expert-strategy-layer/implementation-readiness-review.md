# Expert Strategy Layer MVP Implementation-Readiness Review

| Field | Value |
| --- | --- |
| Parent | `README.md` |
| Design status | approved |
| Delivery gate | not-applicable; this reviews design readiness, not implementation acceptance |
| Review date | 2026-08-19 |
| Baseline | `ac6935c` plus GitHub Issues #54 and #146-#152 |
| Verdict | passed after the design corrections recorded below |

## 1. Review question

Determine whether the seven-child MVP is useful for a personal strategy experiment platform,
implementable on current Quanthus boundaries, statistically capable of falsifying expert claims,
and sufficiently bounded to avoid platform work that does not improve a real research decision.

The review does not assert that the experts work economically. It asserts that each Issue can now
start after its declared dependencies and human decisions, and that success or failure will produce
auditable evidence.

## 2. Product and personal-platform review

| Finding | Resolution |
| --- | --- |
| A two-to-four-expert card plus only one owned expert implementation was not executable. | Freeze the MVP at exactly two active strategy experts; `P3-STRAT-002` now owns the second expert and integration. No extra Issue or expert catalogue is added. |
| Three or more action-changing experts would exceed the declared two-single-intervention budget and leave one mapping without its own economic gate. | Keep two active experts, require both single interventions, and require their one joint intervention. |
| A generic DSL, leaderboard, optimizer, model registry, or expert marketplace would consume more personal-platform cost than the first decision loop justifies. | Explicitly exclude them from every child. Use Python contracts plus versioned JSON cards only. |
| A new expert dashboard or approval workflow would duplicate the candidate workbench. | Extend candidate detail read-only and display the separate lifecycle artifact; add no mutation route or state machine. |
| An unconditional forward-Shadow handoff would make negative evidence unable to close the MVP and bias the lifecycle toward `keep`. | Make Shadow an explicit lifecycle disposition: keep enables it, inconclusive may use a bounded budget, and replacement/retire decisions prove the current version is disabled. |
| Forward Shadow could accidentally become a new live-delivery product. | Submit background observation/settlement Jobs only when the disposition enables them; notification, governance promotion, execution, and P6 stay closed. |

Product verdict: pass. The milestone can answer whether to keep, narrow, remap, merge, retire, or
pause one bounded topology without first building a general expert platform.

## 3. Engineering and runtime review

| Finding | Resolution |
| --- | --- |
| Mutating v1 expert objects would risk accepted DecisionEvent and Run digests. | Put all v2 artifacts in `quanthus.expert_evaluation`; require byte-stable v1 compatibility evidence and no v1 schema edits. |
| An observation that hashes its policy effect would make a mapping-only change rewrite the assessment identity. | Make the reference one-way from `PolicyEffect` to `ExpertObservation`; policy-only observations bind PIT evidence but have no forecast value or settlement. |
| The deployed Worker is candidate-replay-specific. | Add one static job-kind dispatch inside the existing entry point and reuse its PostgreSQL queue, process isolation, lease, cancellation, and fencing. Do not add a daemon or registry. |
| A singular evaluation-spec Job input could run the technical proof but could not represent the frozen two-expert topology. | Require a canonical non-empty spec set, reject missing/extra/duplicate formal-strategy bindings, and reuse one shared projection for the full set. |
| `P3-EXPERT-003` required a result-bearing DWD Worker Run without depending on the Worker issue. | Add `P3-EXPERT-002` as a predecessor; configuration may be prepared earlier, but no DWD result or Issue acceptance bypasses the Worker. |
| Daily opinion rows would make PostgreSQL analytical truth and inflate migrations. | Store one bounded Run summary/reference row; observations and settlements remain immutable columnar artifacts. |
| Job ids, generations, and timestamps cannot be byte-identical across independent retries. | Define determinism over canonical semantic artifacts and a content-derived Run id; keep operational attempt fields in PostgreSQL outside analytical identity. |
| A mutable active pointer could hide evidence replacement. | Do not add one in the Worker issue. Formal cards and product views bind exact Run/release digests. |
| Target settlement and strategy replay have different retry units. | Keep operation-specific stages under one Job contract; settlement appends a new immutable sidecar and never reruns or mutates an observation. |
| UI could silently combine current and stale evidence. | Resolve the entire view through one strategy/card binding and fail closed on any missing or mismatched trusted artifact. |
| Without an exact serving reference, candidate detail would have to guess the latest expert Run. | Add one versioned strategy-to-package serving config; never select evidence by time, insertion order, or directory scan. |
| `P3-STRAT-002` and `P3-ATTR-001` both appeared to own the same formal adapter, allowing experiment code to change after questions were frozen. | `P3-STRAT-002` exclusively seals the adapter; attribution consumes its digest and routes any defect back to a new strategy/adapter version. |
| Candidate detail precedes the lifecycle decision, so neither issue clearly owned its domain schema or the decided-page binding. | `P3-EXPERT-001` freezes the decision contract, `P3-EXPERT-004` owns the serving schema and undecided package, and `P3-EXPERT-005` publishes a new decision-bound package, advances the exact candidate entry, and verifies the decided page. |
| Current P4 work touches shared traceability tests. | This change avoids `src/quanthus/event_library/**`; merge must preserve both sessions' additions to `tests/contract/test_document_traceability.py`. |

Engineering verdict: pass subject to the declared compatibility, PostgreSQL integration, and
two-Worker recovery tests. A single Worker at concurrency one remains the default deployment.

## 4. Statistical and governance review

| Finding | Resolution |
| --- | --- |
| A target chosen after seeing outcomes would invalidate the first expert scorecard. | Require an outcome-blind, human-signed target decision before any resolver or result query. |
| `P3-EXPERT-003` could duplicate the outcome semantics being built in P4 Issue #75. | Add `P4-LABEL-001` as a predecessor; reference its card by digest when semantics match, or record an explicit non-reuse decision before creating a new card. |
| Row counts over overlapping horizons and common market shocks would overstate evidence. | Require event/time-block or clustered uncertainty and disclose effective dependence beside raw counts. |
| An expert scored on a broad market population could look valid while failing on the formal strategy's selected candidates. | Predeclare the intended strategy population as a scorecard slice and block `P3-STRAT-002` when its applicability population does not match. |
| Accuracy or calibration alone could hide poor action value, while favorable replay could hide a false assessment. | Keep assessment-quality and complete-strategy economic gates independent; both must be visible. |
| Letting an assessment scorecard point at a later attribution release would either mutate accepted evidence or create a temporal identity cycle. | Keep portfolio effects out of scorecards; a later immutable evidence-package manifest composes scorecard and attribution digests. |
| Removing a strategy-risk expert could accidentally remove hard safety. | Interventions replace strategy effects only; PIT, data, tradability, accounting, funding, and hard position guardians remain fixed. |
| Single effects are non-additive under capital competition. | Require the one joint intervention and report interaction without allocating ownership. |
| Development iteration could leak into sealed periods. | Every Job binds the development partition digest, target tails censor at its boundary, and MVP acceptance audits every read date. |

Statistical/governance verdict: pass. The design can produce a valid negative or inconclusive
result without redefining success, tuning against sealed data, or hiding abstentions and missing
outcomes.

## 5. Remaining human gates

These are intentionally unresolved product decisions, not missing implementation design:

- exact first target, horizon, population, primary metric, and stop conditions;
- exact formal strategy hypothesis, candidate generator, policy mappings, expected changes, and
  resource budget;
- final expert lifecycle decision after complete evidence exists.

Each gate has a named owner, a no-result decision packet, a stopping boundary, and an immutable
record. Work may proceed up to, but not through, the applicable gate without the human decision.

## 6. Final disposition

All seven Issues now have a bounded outcome, owned paths, input/output contract, implementation
route, typed failure behavior, verification matrix, acceptance artifacts, decision gates, and
rollback. The dependency chain is internally closed after adding the P4 target-card and Worker
predecessors, assigning the second expert to `P3-STRAT-002`, separating semantic from operational
identity, and making negative lifecycle outcomes terminally executable.

The implementation plan is approved for sequential execution beginning with `P3-EXPERT-001`.
Passing this review does not mark any implementation Issue accepted and does not grant access to
arbitration, permanent-blind, P6, notification, or execution capabilities.
