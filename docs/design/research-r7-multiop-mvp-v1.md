# R7: minimum multi-operator, two-expert decision review

| Field | Value |
| --- | --- |
| Design status | approved — Owner 2026-11-13 instructed R7 execution |
| Delivery gate | open |
| Work items | Feature #651; implementation #652; acceptance #653; research Epic #608 |
| Parent | [expert/aggregator LLD](lld/04-experts-aggregator.md); [exploration governance](exploration-governance.md) |
| Runtime request | `config/research/research-r7/request.v1.json` |
| Proposal | `config/planning/r7-multiop-delivery-request.v1.json` |

## Purpose and smallest useful delivery

Give the Owner one queryable real-data report showing trend, structural position and volume
evidence, the existing Trend Stability and Position Risk outputs, and the original governance
action. The reader can identify what each expert actually consumes and why entry is blocked.
This is a fixed security-day engineering review, not a replay of original P4 opportunities.
R8 owns any new action mapping and complete economic comparison; R9 owns a qualified local update.

The first-hour inventory confirmed that the historical decision-review/chain job directories
were removed by an earlier cleanup. R7 therefore stops archive recovery and uses the existing
checksum-bound native input closure. No old trade, position or execution receipt is reconstructed.

## Fixed data and clocks

Use the original 126-member Base 2.5.1 native closure, frozen by its full SHA and HF revision.
Select listing codes 000001, 300750 and 600519, warmup 2020-01-02, report every open session
2020-04-01 through 2020-04-30. Preserve all listing/session denominator rows including missing
data. Source knowledge cutoff remains the actual 2026 timestamp in the request; simulated
T-close is a separate field. Neither the capital clock nor the old P4 opportunity identity is reused.

R7 uses direct `FormalExpertContext` with the two original risk facts and real source evidence.
The subject is `r7-security-day-review`, and `classify_trend_stability` explicitly permits reuse
of accepted semantics on a new subject population. The source and business clocks are both
shown. Labels, future outcomes, training, P6 and protected 2022+ rows are outside this request.

## Native adapter and coordinate admission

The existing native action/quality tables qualify only the selected securities and fixed history.
For each listing, reject trend/structure derivation if an implemented action has an unknown ex-date
or falls in the selected history, or a relevant unresolved quality exception affects that history.
R7 does not interpret action terms or repair sources. This deliberately narrow action-free adapter
is sufficient when that guard passes; otherwise report the exact unsupported listing and reason.

Map native daily bars, open calendars and directional states into job-local legacy feature views,
then call the unchanged baseline feature SQL. With a verified action-free history the total-return
index is the existing initial-100 compounded return formula. Keep the original 61-price/60-return
readiness gate; short rolling means alone do not qualify a trend. Structure uses the original
continuous Facts function on raw OHLC with identity, availability and gap resets. Volume uses its
independent prior-20 ratio. Each operator's coordinate and actual consumption are explicit.

## Decision path and sidecar

Run the original two-expert mapping on the two original risk facts plus the trend observation.
The fixed sample is not an admitted P4 opportunity, so `entry_evidence_ready=false`; system
guardians block entry while preserving the actual trend score and risk opinion. Order, fill and
position changes remain `NOT_RUN_DECISION_REVIEW`. This makes the missing opportunity admission
visible rather than inventing a trade.

The three-operator view is a sidecar. It never appends optional facts to the original context:
`evaluate_entry` checks every context feature for null values, so such an append would silently
change safety behavior. Evaluate the original context before and after sidecar construction and
require identical full decision digests. Keep all expert, veto, constraint and action outputs.

Real report rows are entry review probes with an empty hypothetical position. Held-position,
missing and conflict cases are explicitly synthetic contract tests, never historical positions.

## Artifacts and reader

One input scan builds a shared panel, continuous facts and the report rows. An immutable package
contains `rows.jsonl`, `summary.json`, `manifest.json` and a READY marker. The manifest binds full
request, input member identities, source/code/policy hashes and outputs. A trusted manifest SHA
is required for later verification/query; no archived source paths are followed by the reader.
Query supports security, business date, final action and reason; matched/returned/truncated are
reported separately. The HTML is an offline report of exactly those rows, with no remote assets.

## Budget, failure and acceptance

R7's six-hour batch ceiling includes design, implementation, checking, review, integration and
delivery. The request pins per-run memory, CPU, timeout, scratch and output limits. Formal and
independent construction each have one attempt; failed inputs remain explicit. One cached-source
copy and one independent physical copy are allowed; network input budget is zero.

Reject wrong source hashes, identities, cutoff, duplicate/ambiguous rows, unsafe paths, output
overlaps and corrupt packages. Optional operator unavailability remains a reasoned state and does
not modify baseline expert inputs. Shared-source defects are routed to P10; no parameter/window
changes follow from observed outputs. R4/R6, Head2 and former volume/held-use conclusions remain.

Acceptance requires real values for all three operators on valid rows, real calls to both experts,
the complete denominator, unchanged baseline decisions, reproducible semantic output, cold
package reads, relevant negative cases, a visible usable report and independent role review.
Report engineering use, recognition quality, economic value and admission separately. The full
learning MVP remains governed by #540/#546.
