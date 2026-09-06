# Structure Facts v2: time-first semantic model and delivery blueprint

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Governing work item | `P3-STRUCT-004` |
| Current delivery | B1: boundary, naming, time-envelope contracts and coverage blueprint only |
| Runtime v2 acceptance | NOT RUN / NOT CLAIMED |
| B1 checkpoint | `../acceptance/structure-facts-v2-contract-checkpoint-20260906.md` |
| Parent | `lld/03-label-feature-system.md`; `hld.md` |
| Architecture decision | `../architecture/adr-0014-time-first-structure-semantics.md` |
| Executable catalog | `../../config/structure-semantic-catalog.v1.json` |
| Coverage authority | `../../config/structure-facts-v2-blueprint.v1.json` |
| Contract code/tests | `../../src/quanthus/structure_semantics.py`; `../../tests/unit/test_structure_semantics.py` |
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

### 5.1 Independent clocks

| Clock | What it answers |
| --- | --- |
| `event_start/event_end` or `valid_from/valid_to` | When was the phenomenon observed, or when does the referenced condition apply? |
| `confirmed_at` | When was the last required confirming observation first available under the declared method? |
| `public_available_at` + evidence | When was THIS source version publicly available? Unknown stays null |
| `system_known_from/system_known_to` + receipt | When did the platform possess/select THIS version? Half-open knowledge interval |
| `computed_at` | When did this physical calculation run? A 2026 rebuild of historical data is allowed |
| consumer `knowledge_cutoff` | What knowledge was this decision permitted to use? |

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

The new `TemporalEvidence` and `check_clock_at()` implement local envelope consistency and these
clock distinctions ONLY. A nonempty evidence-reference string is not proof of its authenticity.
Integrated source receipt verification, whole-dependency closure, as-of joins, partition permission,
quality, coordinate compatibility and expert authorization remain mandatory in B2 and existing
policy. `clock_eligible=true` is never a market-truth, official-PIT or order permission.

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

## 6. Blueprint and staged delivery

`config/structure-facts-v2-blueprint.v1.json` is the machine-readable coverage ledger. Each row has
separate design, implementation and verification status, v1 coverage, missing capability, dependencies,
next gate and evidence. A planned row is not implemented; a contract test is not Real-DWD acceptance.

| Slice | Scope | Delivery boundary |
| --- | --- | --- |
| B0 | Retained Facts v1 | Existing bounded engineering baseline, not full v2 |
| B1 | This delivery: meaning, namespace, object/time contracts and coverage map | Contract implementation; no v2 market-data build |
| B2 | Temporal/source/coordinate/identity adapter | Next input-readiness work; verify only the inputs needed by the first slice |
| B3 | Small multi-scale reference set | Freeze parameters before result access; no global optimum claim |
| B4 | Same-anchor close crossings and reclaim | Standalone traceable interactions, no inferred intrabar order |
| B5 | Minimal loss/reclaim process and one consumer | First v2 vertical qualification boundary, not all possible repair geometry |
| B6 | Geometry/width/compression extensions | Planned; demand and counterexample driven |
| B7 | Episode activity relations | Planned; shared primitives, independent volume availability |
| B8 | Complete data/indicator dictionaries | Separate P3-DICT-001 backlog, no dependency of B1-B5 |
| B9 | Disclosure/fundamental/market context | Parallel domains, not missing Structure-owned capabilities |
| B10 | Formal Structure Expert and economic value | Separate assessment/mapping experiment after a sufficient slice, not after every planned family |
| B11 | 13-state, ML and forward execution | Separate authorization; unopened by this blueprint |

First runtime path is B2 -> B3 -> B4 -> B5. It must freeze a finite resource budget, scope, exact
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
