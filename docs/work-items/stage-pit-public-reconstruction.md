# P3-STAGE-030 — Strict PIT Stage10/11 public reconstruction

| Field | Value |
| --- | --- |
| Design status | approved bounded validation route |
| Delivery gate | closed blocked at capability audit |
| Parent | `P3-STAGE-029` |
| Preregistration | `../../config/stage-learning/stage-pit-public-reconstruction-prereg.v1.json` |
| Route review | `../reviews/stage-p030-pit-route-four-party-review-20260916.md` |
| Production / strategy / trading authority | none |

## Objective

Determine whether the exact P029 Stage10/11 real-history population can be recomputed under a strict
historical **public-reconstruction** clock at the existing T 15:30 Asia/Shanghai decision cutoff.
The work tests historical knowability and source/version lineage. It does not tune Stage semantics,
search thresholds, test returns, or infer actual 2020 Quanthus platform receipt state.

## Frozen phase plan

### P030-A — Route review

Closed when the four-role review freezes the clock target, decision cutoff, population, evidence
rules, acceptance states, and mandatory re-review triggers.

### P030-B — PIT capability audit

Read repository/source authority and, only through an already governed read-only runtime when needed,
inspect the exact dependency metadata required by the P029 causal closure. Answer whether complete
historical source-version/availability evidence exists for bars, realized calendar, stable identity,
action-epoch dependencies, and quality/source revision.

No Stage10/11 result may be published in this step. A structural evidence gap closes the step with an
explicit blocker and returns to four-party review.

### P030-C — Protocol freeze

Only if P030-B finds a defensible route, freeze the exact evidence bindings, source revision family,
availability-time evidence, 10-observation denominator, causal warm-up closure, output metrics and
fail-close semantics before implementation sees any PIT Stage result.

### P030-D — Implementation

Implement the smallest bounded selector/replay path required by P030-C. Reuse
`temporal_contracts.py`; do not create a parallel time model or silently map retrospective DWD
`knowledge_from` to historical visibility. Deterministic output and deep verification are required.

### P030-E — Exact replay

Run one frozen result-bearing replay over the exact ten P029 observation identities and their causal
dependency closure. A pass requires 10/10 reconstructable observations. PIT/retrospective predicate
differences are recorded as evidence and are not implementation failures.

### P030-F — Four-party result review

Short-term/event-driven, fund/risk, quant and data-governance perspectives receive the same sealed
result. Close one of the preregistered outcomes and stop; do not automatically create P031.

## Acceptance outcomes

- `PASS_STRICT_PUBLIC_RECONSTRUCTION_STAGE10_11` — all ten observations have authenticated historical
  input/version closure and can be recomputed at the frozen cutoff.
- `INCONCLUSIVE_PARTIAL_PIT_COVERAGE` — at least one observation cannot be reconstructed without an
  unavailable dependency, while some can.
- `BLOCKED_SOURCE_VERSION_OR_AVAILABILITY_LINEAGE` — a required evidence class is structurally absent
  or cannot be uniquely selected at the historical cutoff.

P030 closed on the third outcome at P030-B. The decisive blocker is the absence of a complete,
authenticated 2020 daily-bar source-version/public-availability family under the current governed
ODS/DWD authority. No Stage PIT predicate was run. See
`../../config/stage-learning/stage-pit-capability-audit-result.v1.json` and
`../reviews/stage-p030b-pit-capability-blocker-four-party-review-20260916.md`.

No outcome changes Stage10/11 evidence grade or consumer authority by itself.

## Non-goals

- no `PLATFORM_AS_KNOWN` claim for 2020;
- no current-file/build timestamp backdating;
- no new Stage vocabulary or threshold search;
- no partial-population success claim;
- no 2022-2024 arbitration or 2025 blind read;
- no prediction, return, strategy or trading evaluation;
- no automatic successor.

## Change-control rule

If implementation discovers that the frozen clock, cutoff, population, source authority, strict
evidence rules, Stage semantics, or complete-population pass criterion must change, work stops before
the changed behavior is implemented or run. A fresh four-role review must explicitly decide whether
to revise, narrow, block or close P030.
