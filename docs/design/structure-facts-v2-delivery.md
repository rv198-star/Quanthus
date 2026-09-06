# Facts v2: bounded B1-B5 delivery plan

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Parent | `structure-facts-v2.md`; `lld/03-label-feature-system.md` |
| Work item | `P3-STRUCT-004` and its B1, B1-R, B2, B3, B4, B5 children |
| Owner instruction | Issue-manage the whole bounded batch, deepen the design, then complete children in dependency order |
| Issue projection | `../../config/structure-v2-delivery.v1.json`; `../../scripts/sync_structure_v2_issues.py` |
| Authority ceiling | Development engineering/semantic qualification only; no forward, investment, sealed-partition or broker authority |

## 1. Batch and issue ownership

The parent closes ONLY after all six children are accepted with their own evidence. This parent
covers the first vertical Facts v2 delivery (B1-B5), not every planned B6-B11 capability. The broader
coverage blueprint retains those gaps. B1 and B1-R close by reusing their committed tests/reviews;
do not rerun expensive market experiments to make retrospective issue histories look new.

| Child | Dependency | Deliverable and exit |
| --- | --- | --- |
| P3-STRUCT-004-B1 | P3-STRUCT-003 | Naming/domain/blueprint contract; existing 9ea4e84 and final-entry review evidence |
| P3-STRUCT-004-B1-R | B1 | Business/source/production/audit time separation; bc79803 and final-entry review evidence |
| P3-STRUCT-004-B2 | B1-R | Composed bounded input adapter, source/view and coordinate receipt, denominators, missingness reasons; synthetic and real-input readiness |
| P3-STRUCT-004-B3 | B2 | Prior-window 20/60 references with exact formation/target identities; prefix stability and same-valued distinct-target tests |
| P3-STRUCT-004-B4 | B3 | Same-anchor close-above/close-below/reclaim evidence; equality/gap rules and no rolling-reference fabricated reclaim |
| P3-STRUCT-004-B5 | B4 | Minimal append-only episodes, direct expert/aggregator/P2 compatibility, persisted consumption receipts, real cold A/B and blinded sample reconciliation |

Issue statuses are projections of repository delivery evidence. Each issue names owned paths,
inputs/outputs, exclusions, prerequisites, tests, remaining runtime proof and closure evidence.
The synchronizer touches only these seven IDs, is idempotent, reads back writes and never changes
#215 or the rest of the project. Parent and children link to each other. A ready issue does not
mean runtime accepted; a closed contract issue does not imply its successor's integrated gate.

## 2. Frozen first runtime question and scope

Question: can exact daily observations create multiple independent references, preserve same-target
close relationships and loss/reclaim history, and supply reproducible, auditable continuous inputs
to the existing decision/execution interfaces?

Freeze before market result access:

- development partition only; retained authoritative DWD Base/correction via accepted provider;
- fixed securities, in order: `000001`, `300750`, `600519`;
- warm-up starts `2019-10-08`; reporting `2020-01-02` through `2020-03-31`;
- prior-window lengths exactly 20 and 60 exchange sessions with valid uninterrupted daily bars;
- raw unadjusted CNY price coordinate within one verified identity/action-continuity epoch;
- corporate-action/identity or unknown-quality boundaries censor active processes and restart
  warm-up rather than compare incompatible historical and current prices;
- first lane is explicitly `retrospective`, never silently upgraded from provider evidence;
- daily final close observations; no reconstructed intraday path and no finality guessing;
- maximum five round-robin existing P2 close-to-next-open compatibility probes, 100 shares each;
- two independent cold result-bearing Jobs, A then B, same exact revision/config/source identity;
- no parameter search, no economic comparison, no retries of successful Jobs, no shared A/B cache;
- each Job builds its expensive Direct-DWD prerequisite once, then shares it between B2-B5 and P2;
- no new Worker family, no seeder activation, and only an explicitly registered fixed config;
- execution timeout/resource limits are bounded by the accepted Event Worker; before a new
  result-bearing retry after a behavioral repair, record invalidation and budget disposition.

The window set is a minimal engineering fixture, not an optimized trading prescription. Three
fixed stocks do not establish all-market or forward readiness. Missing-data denominators include
all configured security/session attempts; absence does not silently remove dates or identities.

## 3. B2: input boundary and complete admission

Required sources: decision calendar, exact economic/listing identity, final raw daily H/L/C and
optional volume, normalized actions/quality exceptions, and retained P2 execution context. Reuse
the provider's verified manifests and canonical identity bridge. Do not reparse announcements.

Input receipt binds provider/config/materializer, Base and correction identities, admitted view,
required dataset and column closure, calendar/profile, security list, report/warm-up bounds,
coordinate policy and source-view limitations. Public/platform timestamps without genuine evidence
remain absent; `per_decision_publication_proven=false` is a claim ceiling, not a field to repair by
inference. Whole effective-view identity stays in the package receipt; row/anchor identity binds
only its exact causal source-member closure, not unrelated future report inventory.

Each security/session attempt produces either one admitted row or one explicit rejection record.
Retrospective mode still refuses market events beyond result time, incomplete/invalid prices,
unknown required coordinates or identities, partial/finality-unknown inputs and undeclared gaps.
Missing optional volume suppresses only volume measures. Suspensions are no-observation boundaries,
not flat candles. Corporate-action or quality uncertainty never becomes bearish structure or a
forced sale. No later exception becomes a hidden earlier all-interval stock exclusion.

Stronger public/platform views must use complete exact-version families and compositional checks
from `temporal_contracts`, not a single clock helper. The initial real-data adapter refuses those
stronger views when the source contract does not prove them. Synthetic adversarial tests exercise
late availability, correction, future-effective references, finality and quality/coordinate gates.

Output: typed admitted daily evidence and rejection rows plus an input-readiness report. No source
mutation. Audit integration uses the existing immutable Run evidence boundary; no per-field read
logging service.

## 4. B3: references and complete grain

For each security, each admitted T and each of 20/60 prior sessions, create high/low reference
objects only after a full valid history window. T is excluded. The object is not a claim of strong
support/resistance. Identity includes security AND listing context, reference side/window rule,
source-window endpoints, price coordinate epoch and exact formation-observation closure.

Separate the logical anchor key from its revision. Different formation windows or sides remain
different targets even when they have identical prices. Source correction is a revision of the
same logical slot, not a second simultaneous target. A distance or interaction key additionally
includes its logical anchor target. The same spec applied to A and B must not collide. Physical
run id, creation/use time, package digest and later report extension are forbidden in these keys.

Bound runtime work: current rolling references are created for description; only references with
an observed initiating cross enter the active process set. There is no quadratic all-history
recomparison and no perpetual monitoring of every past rolling reference. One retained episode per
security/window/side is sufficient for this first fixture; active references remain frozen while
new rolling reference descriptions may change. Episode cancellation/retention rules belong to B5,
not a hidden overwrite of reference identity.

## 5. B4: same-reference close relations

All comparisons use exact Decimal CNY values in the same coordinate epoch. The previous valid
close must be in the same uninterrupted session segment. Equality rules are explicit:

- cross above: previous close <= anchor and current close > anchor;
- cross below: previous close >= anchor and current close < anchor;
- hold: on the same side or at equality under the episode's declared convention;
- reclaim: cross above the SAME frozen reference after a recorded below-reference observation.

A changing rolling reference by itself cannot create reclaim. For an active frozen target, later
rolling highs/lows never change its level. Bar-range straddle may be separately described but does
not prove touch execution, high/low ordering, limit-up reseal or participant intent. No thresholds
such as volume >=1.2 are hidden in reference existence or crossing rules.

Every interaction references previous/current admitted observation revisions and the exact anchor
key/revision. Event business time is current close; admissible knowledge view and consumption times
remain respectively provenance/query and operation-log concerns.

## 6. B5: minimum process and existing consumer

First process: price cross above a frozen prior high starts one breakout episode per window. A
later close below marks a loss observation, subsequent below/equal observations retain that loss,
and a later strict cross back above records reclaim. Loss history is never erased. A reclaimed
process may close with an explicit terminal observation; a fresh future rolling-high cross can
then initiate a new episode. The downside context remains the frozen prior-window low and current
rolling-low distances; symmetric short-selling processes are outside this batch.

History gap, identity change or action-coordinate boundary produces `censored` at the first observed
boundary, never backdated `failed`. Scope end is reported as right-censored/open in report metadata,
not injected into a last-day historical business row. Session age, valid-observation count and
post-cross hold count are separate. Episode ID binds initial interaction and window/target.

The compatibility expert consumes continuous distances and active/loss/reclaim evidence directly
through existing FeatureFact/ExpertContext and aggregator contracts. No renderer is used to compute
its score. Its mapping is deterministic and research-only; it proves ingestion, not good investment
advice. No modification to accepted formal strategy or economic metrics.

P2 uses existing BacktestSignal/BacktestEngine with accepted next-open execution. Up to five probes
are selected deterministically by security round-robin among eligible entry-compatible rows.
Generation/publication receipts and FactConsumptionEvent instances are separate audit artifacts;
Fact identity has no consumed_at, decision_at, run_id or package inventory. Actual physical audit
clocks may differ between cold A/B. Compare semantic facts/interactions/episodes/consumer/P2 digests
and validate each run's receipt chain separately; never fake matching wall-clock timestamps.

## 7. Stage graph and artifact protocol

Stage DAG: provider materialization -> B2 admission -> B3/B4/B5 causal build -> expert/aggregator
consumption -> P2 probes -> immutable publication -> independent verifier. Provider projection is
TMP and job-local; business objects and consumption evidence form a governed thin research ADM/Run.
Record stage elapsed seconds, attempted/admitted/rejected/output rows, bytes and build/reuse counts.
Do not call the provider separately for each stage or stock.

Artifact includes the pinned input-readiness receipt, schema-qualified business objects, rejected
attempts, consumer outputs, P2 reconciliation, bounded outcome-blind sample and physical runtime/
audit receipts. Manifest binds exact filenames, byte hashes, row counts, code/config/source and
semantic identity. READY is written last, publication is immutable and verifier fail-closes on
unknown fields, mismatched keys, tampering, incomplete membership or authority expansion.

A/B must be new independent Jobs with different idempotency keys and no cross-run intermediate
reuse. Semantic identities must match; receipt timestamps/run IDs need not. Worker control-plane
integration remains static and stdlib-only at the artifact boundary.

## 8. Acceptance and stop conditions

Reuse retained B1/B1-R evidence. For B2-B5 require synthetic counterexamples before real runs:
late/corrected sources; no partial data under final profile; same-valued distinct anchors; multiple
targets; unchanged-prefix identities; excluded-T extrema; moving-anchor false reclaim; equality;
gaps/action censoring; missing volume; explicit attempts denominator; immutable consumption logs;
independent semantic reconstruction and artifact tampering.

Outcome-blind sample rule is frozen here: fixed security order, then earliest start, loss and reclaim
per 20/60 window; deduplicate logical observation keys and fill in security round-robin by earliest
remaining report row to at most 12. Missing categories are explicitly reported, not manufactured.
Inspect T-visible inputs, frozen targets, arithmetic, transition evidence and provenance only. No
future returns or P2 outcomes participate in sample selection or semantic judgment.

A child may have implementation PASS while real qualification remains OPEN. Close B2 after real
input readiness; B3/B4 after their real object/interaction evidence and adversarial tests; B5 only
after same-revision cold A/B, independent verification, sample review and audit/P2 compatibility.
Parent closure does not activate B6-B11, #215, models, full experts or forward execution. Admission
blocked by actual tool/control-plane capability is recorded specifically; do not swap the new
command into an old registered config or enable hidden auto-submission to bypass the boundary.
