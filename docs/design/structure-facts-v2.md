# Structure Facts v2: time-first semantic model and delivery blueprint

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Governing work item | `P3-STRUCT-004` |
| Current delivery | B1-R: time ownership, source-clock selection and consumption-audit contracts; no runtime integration |
| Runtime v2 acceptance | NOT RUN / NOT CLAIMED |
| B1 checkpoint | `../acceptance/structure-facts-v2-contract-checkpoint-20260906.md` |
| B1-R checkpoint | `../acceptance/structure-facts-v2-temporal-ownership-checkpoint-20260906.md` |
| Parent | `lld/03-label-feature-system.md`; `hld.md` |
| Architecture decision | `../architecture/adr-0014-time-first-structure-semantics.md` |
| Executable catalog | `../../config/structure-semantic-catalog.v1.json` |
| Coverage authority | `../../config/structure-facts-v2-blueprint.v1.json` |
| Contract code/tests | `../../src/quanthus/structure_semantics.py`; `../../src/quanthus/temporal_contracts.py`; `../../src/quanthus/evidence_consumption.py`; `../../tests/unit/test_structure_semantics.py`; `../../tests/unit/test_temporal_contracts.py` |
| Predecessor | `continuous-structure-facts-v1.md`; v1 acceptance remains immutable |

## 1. Purpose and decision

The owner requested implementation of the reviewed semantic boundaries and meaningful naming,
a separately scheduled data/indicator dictionary issue, an explicit answer to the suitability of
ontology for temporal markets, and a blueprint that exposes both delivered and missing capability.

Use **a lightweight, time-first domain semantic model**, not a static stock-to-state graph or a
comprehensive ontology of investing. A vocabulary identifies what a measurement means; typed,
versioned observations and temporal evidence identify what could be known at each decision.
Time-series calculation and lifecycle processing remain procedural, tested Quanthus code.

No RDF store, OWL reasoner, graph database, generic rule language, new service, DWS, or ADS is
required. Ontology here means shared concepts, identities and relationships, not a replacement for
DWD, an executable strategy, a claim that all prices are explainable, or a prediction algorithm.

This design freezes boundaries, identifiers and the delivery route. It does not freeze numerical
windows or authorize every future family at once. No v1 producer, indicator formula, Worker,
qualification seeder, execution threshold or historical Run changes in B1.

## 2. Domain boundary

Structure owns reproducible observations about the price/trading-activity geometry of a listed
instrument, reference objects, interactions with those references, and processes composed of those
interactions. Definitions expose their assumptions. A continuous number is not intrinsically more
factual than a Boolean. A rule-defined close-above-reference can be a derived fact; a claim of
'reliable support', 'accumulation', 'repair success' or 'future appreciation' needs a separate
interpretation, estimate or assessment contract.

| Domain | Ownership | Structure dependency |
| --- | --- | --- |
| DWD observations | Original market facts and corrected source versions | Read only through admitted views |
| Required reference data | Listing/security identity, calendar, currency/units, company actions and tradability | Refer to existing authority; never rebuild inside a structure expert |
| `structure.*` | References, geometry, event-conditioned activity, interactions, episodes | Current modeling scope |
| `indicator.*` | Shared deterministic indicators with exact calculation specifications | Parallel module; may be an explicit upstream dependency, not necessarily a higher layer |
| `disclosure.*` | Source documents and issuer claims, publication and revision | Parallel domain; no text parsing inside pure structure definitions |
| `fundamental.*` | Reported financial values with report period and publication/version evidence | Parallel domain |
| `market.*` | Breadth, benchmark/sector context and legal historical membership | Parallel context; join at the expert's admitted cutoff |
| `outcome.*` | Mature future targets for evaluation | Never an input to that same T decision |
| Experts/policy/P2 | Assessment, risk constraints, allocation, actions and fills | Remain separate existing authorities |

A split/dividend announcement can be the source of a normalized company-action record. The
normalized action, not the raw narrative, is needed for comparable coordinates. Removing news
from the structure domain is not permission to ignore company actions, identity discontinuities,
calendar gaps or suspension. 'Issuer forecasts profit X' is a disclosure claim, not realized
profit X. Temporal adjacency to an announcement is not causal attribution.

Pure structure observations depend only on declared price/activity data and necessary reference
context. Adding a news article without changing those admissible inputs cannot rewrite their past
values. Experts may combine multiple domains directly; labels/states are optional projections,
not a mandatory intermediary.

## 3. Object model: identity before columns

| Object | Meaning and key binding |
| --- | --- |
| Concept | Stable meaning; exactly one primary domain/family owner |
| EvaluationSpec | Concept/version, algorithm/version, inputs, parameters, sampling, coordinates, window/cutoff, missingness and numeric policy |
| Observation | Subject/listing identity, spec digest, event interval, result, availability and exact dependency closure |
| Anchor | Immutable reference definition, subject, coordinates, source window and formation evidence; role is not proven strength |
| Interaction | Relation between admitted observations and the SAME anchor identity/coordinate |
| Episode | Stable process identity plus append-only observations of start/progress/end/unknown continuity |
| EvidenceBinding | Exact source versions, generation/revision links and permitted temporal view |

Relationships: `Observation -> subject/spec/evidence`, `Anchor -> formation observations`,
`Interaction -> anchor + observations`, `Episode -> starts/contains/ends-with interactions`, and
`ExpertAssessment -> admitted observations`. Store typed rows and references in existing artifact
mechanisms; these relations do not imply a graph-storage requirement.

An anchor's semantic identity covers definition/spec, economic security AND listing context,
price/quantity coordinate, source-window endpoints and exact formation evidence. Merely retaining
its numeric value does not retain its identity. Episode identity binds its initial interaction;
subsequent observations never overwrite its previously published state.

Fact/observation identity must not change merely because a later report contains more rows or was
rebuilt at another wall-clock time. Exact changed source versions or algorithms create a new
observation revision with a supersedes/derived-from reference, not a silent overwrite. Package
manifests may have different hashes when inventory or run metadata changes.

## 4. Naming contract

Canonical concept IDs use exactly three complete segments:

```text
<domain>.<family>.<kebab-case-concept>
structure.anchor.rolling-high
structure.geometry.signed-distance
structure.interaction.close-reclaim
structure.episode.breakout
indicator.trend.sma
indicator.momentum.macd
```

`structure.anchor` discovery matches that family, not `structure.anchors` or the substring
`structure.anc`. The catalog owns meaning and membership. `family_of()` classifies syntax;
`resolve()` additionally requires concept registration. Neither grants runtime admission.
The B1 catalog entries are RESERVED concept definitions, not active v2 evaluators.

Owned structure families are anchor, geometry, activity, interaction and episode. Scale is an
attribute across families, not a parallel type. Compression/expansion based on width change belongs
to geometry. Generic volume ratio belongs to shared indicator calculation; episode activity can
reference it, not maintain a second near-identical formula. Interaction owns the crossing rule;
episode consumes that evidence instead of recalculating a different crossing.

MA is not a sufficiently specified algorithm. `indicator.trend.sma` is a concept, while a 10-session
SMA is an EvaluationSpec. MA10 may be a display alias for ONE exact spec, never a substitute for
formula, coordinates and history policy. `concept_version`, `spec_digest`, `observation_id` and
`value` are separate fields. Do not embed security, date or mutable 'latest' pointers in concept IDs.
Unknown acronyms have no guessed canonical definition.

### EvaluationSpec minimum closure

Every executable definition must bind:

- concept ID/version and algorithm/version;
- exact input definition IDs/versions, source fields and dependency graph;
- subject grain and sampling interval; exchange/session calendar version;
- price/quantity coordinate ID, currency/unit, normalization and company-action treatment;
- explicit parameters, finite-window bounds or recursive warm-up/initialization;
- whether T is included, minimum history, partial-session and gap/reset behavior;
- numeric precision, rounding, missingness, confirmation and availability rules;
- declared return shape and interpretation ceiling.

Hash canonical serialized specification content. Cache identities additionally bind source/effective
view, code/evaluator, temporal/partition scope, subject subset and full dependency closure. No
sharing between differently initialized recursive indicators or incompatible coordinates.

### Existing names are not silently migrated

The catalog's legacy links are `related_not_equivalent`, deliberately not executable aliases.
`price.sma20@1.0.0` and `price.sma60@1.0.0` retain their total-return-index input and index units.
`price.rsi14@1.0.0` retains its existing return/simple-window definition. An imported standard library
function or a raw-price SMA with a similar name does not replace these definitions. Versioned
migration and exact equivalence evidence are future dictionary work. Old Runs remain reproducible.

## 5. Time is a first-class model, not a suffix on a stock label

### 5.1 Three owners, not one oversized Fact timestamp envelope

The owner explicitly corrected the temporal review: **actual usage time belongs to operation /
decision audit logs, not to market Fact modeling**. Adopt that correction for all Facts and indicator
results. A value can have many consumers and repeated uses, or no use at all. Reading a Fact must
never mutate its row, last-used field, semantic digest, quality, or business validity.

| Owner | Fields or referenced record | What it answers |
| --- | --- | --- |
| Business observation | `result_as_of` | Which business instant does this result describe? |
| Business observation / spec | `window` with explicit endpoints, inclusion, calendar, counts | Which observations formed it? This is not result time or validity |
| Event / reference / process observation | `event_at`, optional `confirmation_event_at` | When did the market event occur, and which later market observation completes logical confirmation? |
| Business rule / reference when applicable | `valid_from/valid_to` | When is the object applicable? Not every scalar measurement expires |
| Source-version metadata | public bounds, received/validated evidence and view-specific knowledge interval | When was THIS version public or admitted to the platform? |
| Dependency metadata | view-qualified `confirmation_ready_at` / latest required-input readiness | When were all exact required inputs and confirmation evidence available? |
| Generation / delivery receipts | actual `completed_at`, `servable_at`, run/code/spec/output/package binding | When was this materialization completed and made available? |
| Operation / decision audit only | `consumed_at`, `decision_at`, `recorded_at`, consumer/run/decision identity and exact selected revisions | Who actually used which output, when? |
| Query context, persisted in decision audit | `knowledge_cutoff`, knowledge view, query purpose, selection policy | Which information horizon was the operation allowed to use? |

The first four rows are semantic business time. Source/publication clocks are metadata with distinct
owners. The last two rows are query/audit concerns and MUST NOT be embedded as usage attributes of
an `Observation`, `Anchor`, `Episode`, indicator value, or their semantic identity. Source evidence
can be referenced by immutable IDs; no arbitrary duplication of every timestamp on every fact row.

`BusinessTime` separates result time, input window and validity. A measurement can describe an old
business instant while its physical creation is recent. A delayed pivot retains its occurrence time
without becoming confirmed earlier. Ordinary indicators need a declared window/finality rule, not
an invented pattern-confirmation stage. `partial/final/unknown` data finality is independent of market
confirmation. Observation counts and exchange-session counts remain separate; sampling and partial-
session activity denominators belong to the EvaluationSpec.

`GenerationReceipt` and `PublicationReceipt` may describe an atomic batch, with membership proved by
the immutable artifact. A shared batch completion timestamp is not fabricated per-row timing. A
verified direct handoff can stand in for package publication. Both are production provenance, not
usage attributes or trading authority. Unknown source times remain unknown; received, validated and
servable are not interchangeable.

Do not force all object types into the same chronology: a financial forecast can refer to a future
report period, but a Structure observation cannot claim a confirmed future price. Public and system
clocks are not interchangeable. Data received after T close are not available at T close simply
because their bar is dated T. Publishing after the last trade is normal; an actual decision clock
must incorporate that availability, not invent a 15:00 receipt.

Examples:

- A high occurs on T but needs T+2 confirmation: its formation points to T; before T+2 it cannot be
  consumed as a confirmed pivot. No backfill of the earlier decision.
- A T fact is corrected later: preserve the old version for queries as known then, record the new
  version with its later knowledge start, and link the revision. Current truth is a separate view.
- A suspended instrument may accumulate exchange sessions without valid observations. Publish
  exchange-session age and observed-bar count separately; never concatenate valid bars across an
  undeclared gap or equate a calendar day with a trading session.
- At a missing-data boundary, process continuity can become unknown/censored. It is not a confirmed
  price failure. V1's reset behavior remains preserved; v2 must explicitly account for this loss of
  continuity in episode observations.

### 5.2 Three distinct query/evidence views

1. `platform_as_known`: select the exact source version in its recorded platform knowledge interval
   at cutoff; all needed confirmations must already be available. This is about actual platform
   knowledge, not merely historical market dates.
2. `public_reconstruction`: use source-version-specific public availability evidence. The platform
   may have collected that historical evidence later. This is a labeled reconstruction, not proof
   that the platform operated with those data then. Revisions still require an explicit version
   selection policy and no overlaps/ambiguity at the requested public-information cutoff.
3. `retrospective`: use an explicitly authorized historical corrected view for diagnosis/research;
   no claim of full historical availability. Calculations still cannot read later market events or
   confirmations as inputs to an earlier observation.

Knowledge view is independent of query purpose. `counterfactual_recomputation` may run today's
specified algorithm over an authorized historical information view; its actual computation/use
clocks remain today. `historical_actual_consumption_audit` requires the original platform output,
delivery and decision-use evidence. A 2026 rebuild cannot prove an expert consumed that output in
2020. Auditing an old event today may give the audit record a later `recorded_at`, never an invented
old `consumed_at`. Query context is stored in the decision log, not in the Fact's business attributes.

Retained `TemporalEvidence` / `check_clock_at()` are B1 local source-clock helpers only. Their
`confirmed_at` is the logical market confirmation endpoint; the original phrase 'first available'
was ambiguous and is superseded here. Their late `computed_at` support is historical recomputation,
not proof of actual use. Their old behavior remains available; new contracts do not silently replace
v1 runs or reinterpret old evidence.

B1-R's `select_source_version()` tests one caller-declared complete revision family using evidenced,
view-specific selection intervals. It rejects overlapping eligible revisions, unknown availability,
and ambiguous same-instant boundaries rather than choosing an arbitrary latest file. Retrospective
selection requires an explicit revision pin. No public supersession interval is guessed from a
filename or from the existence of another version. Actual-use audit compares the logged selection
under its recorded selection policy; it does not rewrite that selection to whichever file is latest
now. A query for currently valid source truth remains a separate policy from audit of actual use.

`dependency_ready_at()` calculates the conservative latest bound of all supplied required inputs
and confirmation evidence. It is NOT output completion or publication time. A completeness flag or
nonempty reference is not proof: source receipt authentication, family completeness, actual as-of
joins, dependency/cross-section completeness, finality, quality, coordinate compatibility, partition
permission and expert admission remain B2/runtime gates. Local checks grant no official-PIT, market-
truth or order authority.

Every input contributes its earliest admissible availability; the output cannot be known before the
latest required input and required confirmation. Any future outcome is isolated until its target
window matures and remains outside the same T prediction's dependency closure. Future records must
not determine an earlier universe or missingness filter without a disclosed retrospective bias.

The current Direct-DWD provider explicitly retains `retrospective_dwd_record_view` and
`per_decision_publication_proven=false`. B0's bounded v1 engineering acceptance is unchanged;
repeatable arithmetic does not upgrade that source evidence. B2 first determines which evidence view
is supportable per source. Missing evidence blocks stronger claims, not all retrospective research,
and is not an automatic instruction to rebuild the whole warehouse.

### 5.3 Causal and observational counterexamples

- Stationary price 98, rolling reference changing 100 -> 95 is not reclaim of the old 100 anchor.
- Nested same-endpoint windows imply H20 <= H60 <= H120 by definition; this is not three independent
  confirmations. Store shared-input lineage and overlap instead of counting duplicate evidence.
- The same daily OHLC permits both high-then-low and low-then-high paths. Publish only observed
  range/close relations, not intrabar ordering, touch fills, or a tick-level rejection story.
- A split changes the price/quantity coordinate. Convert both observation and historical reference
  with a verified causal transformation, or mark the relation unavailable; do not move a support to
  preserve an interpretation. A transformation revision has explicit provenance.
- Missing volume suppresses volume-derived measurements only when price evidence remains valid.
  Amount is not turnover rate; a rate additionally needs the right historical denominator.

### 5.4 Precision, revision and operation-log binding

Preserve timestamp precision, timezone, source evidence and conservative bounds. A date-only source
must not become a fictitious midnight publication. B1-R refuses a cutoff inside the uncertainty
interval and refuses equal availability/use timestamps without ordering evidence. No artificial
millisecond offset is allowed. B2 may later consume a verified sequence or causal receipt; it must
not treat a caller-supplied order flag as evidence. UTC-equivalent instants have the same semantic
identity. Validity/version intervals remain half-open under their declared time model.

`ObservationRevision` separates a business `observation_key` from the exact source/value revision.
Spec and coordinate are part of the key. Source corrections, finality changes, confirmation evidence
or values can create a distinct revision; a new calculation specification is a new evaluated key.
Wall-clock generation, run inventory, package size and use never enter these semantic digests.
Generation and publication receipts keep their own physical identities. Existing v1 FeatureLineage
continues to bind its original ADM package; B2 must bridge exact v2 observations, not silently
reinterpret v1's package-dependent evidence digest as a v2 stable row identity.

`FactConsumptionEvent` belongs to the append-only operation/decision evidence boundary (LLD 10),
not the `structure.*` concept catalog. It records consumer/run/decision, actual consumed/decision/log
clocks, the query context and exact `ConsumedRevision` references: observation key, revision, spec,
generation receipt, publication receipt and artifact digest. One output may be referenced by many
events. Fact creation does not require a consumer. Corrections or retractions append events rather
than overwriting earlier facts/logs. `validate_consumption_binding()` checks local receipt identity
and delivery-before-use consistency; it does not grant source/PIT or execution approval.

Do not introduce a second audit database, a new event bus, or a data-read log for every arithmetic
access. B2 integrates the minimal input-use receipt with the existing decision/operation evidence
path at the declared expert invocation or decision boundary. A physical batch may aggregate input
references under a manifest, but exact member selection must remain recoverable. Debug text alone
is not sufficient evidence of historical consumption. The legacy P2 DecisionEvent format is not
modified by B1-R.

For episodes, a later end/censoring status is a new time-qualified process observation, never a
backfill of the terminal state into earlier knowledge. Missing bars can censor continuity without
asserting price failure. Effective rules and forecasts use domain-specific time profiles; do not
apply observed-price chronology to an already-announced future-effective action.

### 5.5 Bounded observability and remaining runtime gates

Daily high/low ranges cannot establish first-touch order, reseal timing, executable queue position,
actor identity or fund intent. Every specification declares sampling and its capability ceiling;
more precise ingestion timestamps do not create missing intrabar observations. Amount, volume and
turnover rate retain separate units/denominators. Announcement adjacency is not causal attribution.
Names own classification only; algorithm, unit, initialization and clock profile come from the spec.

B1-R closes ownership/contract ambiguities, not source authentication or integrated consumption
acceptance. B2 is limited to the necessary inputs and one daily profile for B3-B5. Evidence gaps
restrict the permitted view/claim or stop that profile; they do not trigger an unlimited all-market
history rebuild. Dictionary issue #215 stays independently scheduled. No fresh v1/economic replay,
new research Job, 13-state, model, Shadow or live activation is authorized by this amendment.

## 6. Blueprint and staged delivery

`config/structure-facts-v2-blueprint.v1.json` is the machine-readable coverage ledger. Each row has
separate design, implementation and verification status, v1 coverage, missing capability, dependencies,
next gate and evidence. A planned row is not implemented; a contract test is not Real-DWD acceptance.

| Slice | Scope | Delivery boundary |
| --- | --- | --- |
| B0 | Retained Facts v1 | Existing bounded engineering baseline, not full v2 |
| B1 | This delivery: meaning, namespace, object/time contracts and coverage map | Contract implementation; no v2 market-data build |
| B1-R | Temporal ownership and consumption-audit contract revision | Local contract tests only; use belongs to logs, generation/publication to provenance |
| B2 | Temporal/source/coordinate/identity adapter | Next input-readiness work; verify only the inputs needed by the first slice and bind existing audit ports |
| B3 | Small multi-scale reference set | Freeze parameters before result access; no global optimum claim |
| B4 | Same-anchor close crossings and reclaim | Standalone traceable interactions, no inferred intrabar order |
| B5 | Minimal loss/reclaim process and one consumer | First v2 vertical qualification boundary, not all possible repair geometry |
| B6 | Geometry/width/compression extensions | Planned; demand and counterexample driven |
| B7 | Episode activity relations | Planned; shared primitives, independent volume availability |
| B8 | Complete data/indicator dictionaries | Separate P3-DICT-001 backlog, no dependency of B1-B5 |
| B9 | Disclosure/fundamental/market context | Parallel domains, not missing Structure-owned capabilities |
| B10 | Formal Structure Expert and economic value | Separate assessment/mapping experiment after a sufficient slice, not after every planned family |
| B11 | 13-state, ML and forward execution | Separate authorization; unopened by this blueprint |

The contract path is B1 -> B1-R; the first runtime path is B2 -> B3 -> B4 -> B5. It must freeze a finite resource budget, scope, exact
specifications, rejected counterexamples, outcome-blind samples and independent reconstruction
before result-bearing qualification. Do not repeat the already completed v1 or C0/C1/C2 experiments
merely to validate names. Do not start B6/B7 to make an impressive inventory without a consumer need.

### Exit criteria for the first runtime slice

One admitted development input -> time/coordinate adapter -> small anchor set -> same-anchor
interaction -> minimal episode -> ordinary FeatureFact/expert interface -> existing Aggregator/P2
compatibility -> immutable artifact and independent reconstruction. Verification includes:

- schema/ID, spec/coordinate and exact temporal-view binding;
- late-arrival, corrected-version, delayed-confirmation and prefix-replay stability;
- session/gap/censoring and split-adjacent counterexamples;
- multi-scale shared-input disclosure and no moving-anchor false reclaim;
- row/object-local explanation plus deterministic outcome-blind sampling;
- build-once DAG ownership and stage runtime/counts/bytes/reuse receipts.

This boundary proves engineering/semantic fitness, not predictive sufficiency or economic value.
An unsatisfied gate stays visible as missing. No new full-market PIT claim follows from a small
slice. Next milestone expansion requires an explicit decision, not a change in the coverage label.

## 7. Data and indicator dictionary work (non-blocking)

`P3-DICT-001`, specified in `../work-items/data-indicator-dictionary.md`, consolidates discovery and
maintenance over existing warehouse/feature/label contracts. The minimal in-scope catalog above is
needed now; complete inventories, generation/UI, broad indicator libraries and migration are not.
Authoritative formulas and source dictionaries keep their present owners. Derived indicators are
normally computed once under Quanthus-owned specifications (existing implementation or pinned
library); vendor outputs have separate identities and are corroboration until separately qualified.

## 8. Reference rationale, not runtime dependencies

- W3C OWL-Time: https://www.w3.org/TR/owl-time/ — instants, intervals, ordering and calendars.
- W3C SOSA/SSN: https://www.w3.org/TR/vocab-ssn-2023/ — separate phenomenon and result times.
- W3C PROV-DM: https://www.w3.org/TR/prov-dm/ — derivation and revision relationships.

These support using temporal concepts, not a claim that a general ontology alone supplies market
causality, low-latency replay, backtest validity or trustworthy source timestamps. Quanthus's exact
contracts above and its admitted source evidence own those responsibilities.
