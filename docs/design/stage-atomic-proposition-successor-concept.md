# P3-STAGE-012 Concept — Atomic Proposition Decomposition and Compact-Disorder Boundary

| Field | Value |
| --- | --- |
| Design status | draft |
| Delivery gate | blocked |
| Work item | `P3-STAGE-012` |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Predecessor | `P3-STAGE-011 / REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Post-closeout review | [P011 expert meeting](../reviews/stage-orderly-consolidation-p011-post-closeout-expert-review-20260913.md) |
| A-C result | `../../config/stage-learning/stage-atomic-proposition-successor-abc-result.v1.json` |
| D expert review | `../reviews/stage-atomic-proposition-d-expert-review-20260913.md` |
| Work-item route | [P3-STAGE-012](../work-items/stage-atomic-proposition-successor.md) |
| E synthetic acceptance | `../acceptance/stage-atomic-proposition-e-synthetic-acceptance-20260913.md` |
| Authority | Owner-approved E1/E2/E3 synthetic implementation is complete; post-E review is next; no market-data/runtime/parameter/new-Fact authority |

> P3-STAGE-012 remains a **design/synthetic research work item**. A-D are closed and E1/E2/E3 are
> synthetically accepted without fresh market data or parameter search. The parent design remains
> draft and delivery stays blocked until the required post-E review chooses whether any formal Fact
> or projection repair deserves a later owner-approved route.

## 1. Why a different successor shape is needed

P011 closes with zero eligible Stage0/Stage2 pairs, but the failure is no longer well described as a
single classifier-threshold problem.

Two distinct Stage0 failure surfaces remain:

1. **positive-instability admission blind spot** — fourteen definite Stage0 references fall outside
   both P010 channels because the path is neither materially expanding / gaining absolute width nor
   already broad enough;
2. **instability-relocation semantic conflict** — eleven definite Stage0 references have positive
   instability under the representative frontier but are vetoed by the frozen P008 relocation guard.

The first suggests a missing Stage0 abstraction. The second may be a guard problem, a reference
problem, or a projection/taxonomy problem. Combining both immediately into another finite threshold
grid would make attribution worse.

The successor therefore changes the order of work: **atomic semantics first, stage projection second,
threshold search last**.

## 2. Core model

The three primary propositions remain independent:

```text
structural_instability_20
coherent_directional_relocation_20
orderly_consolidation_20
```

They are not themselves the final Stage value.

The existing projection semantics remain the starting hypothesis:

```text
instability TRUE + relocation FALSE + order FALSE -> Stage 0
instability FALSE + relocation FALSE + order TRUE -> Stage 2
instability TRUE + relocation TRUE                -> mixed directional instability
order TRUE + (instability TRUE or relocation TRUE)-> explicit conflict
relocation TRUE + order FALSE                     -> outside Stage 0/2 projection
all FALSE                                          -> NEITHER
```

P012 must not silently change those mappings. It first determines whether the mappings, proposition
semantics or numeric predicates are the actual source of disagreement.

## 3. Workstream A — atomic ontology and resolver audit

### Goal

Make the evaluation target explicit before touching thresholds.

### Questions

- Is Stage0 intentionally a **direction-neutral** instability stage, or should directional structural
  stress own a primary Stage code elsewhere in the twelve-state vocabulary?
- Is `mixed_directional_instability` merely reason metadata, a modifier proposition, or evidence that
  the single-primary-stage projection is premature?
- When reviewer atomic propositions and numeric relocation disagree, which proposition is actually
  under test?

### Required output

A finite truth table that distinguishes:

- atomic proposition disagreement;
- valid coexistence;
- resolver conflict;
- primary-stage projection.

No numeric threshold may be selected by A.

## 4. Workstream B — consumed-evidence relocation adjudication

### Scope

Use only already-consumed P008-P011 evidence diagnostically. The eleven P011 relocation-veto Stage0
references are the primary conflict packet; previously exposed P008 mixed-direction cases may be used
as semantic counterexamples.

### Hypotheses

Each conflict must be explainable as one of:

- **R1 — relocation guard over-fire:** numeric relocation is positive but blinded semantic evidence
  does not support coherent repricing;
- **R2 — reference under-call:** reviewer/reference failed to recognize a coherent relocation that the
  path supports;
- **R3 — genuine coexistence:** both instability and relocation are defensible; projected Stage0 was
  the wrong comparison target;
- **R4 — unresolved:** available T-only evidence cannot distinguish the above.

No vote count is a threshold-learning signal. The output is diagnostic classification and semantic
counterexamples only.

### Exit

P012 may reopen relocation implementation only if the adjudication identifies a reproducible semantic
failure shape. Aggregate P011 recall loss alone is insufficient authority.

## 5. Workstream C — compact / contracting disorder observational sufficiency

### Problem

The fourteen `instability_not_true` misses show that both existing positive-instability channels share
an amplitude admission boundary. They do **not** by themselves define a valid third channel.

A successor must distinguish at least these two cases:

```text
A. narrow / contracting but structurally disordered
B. narrow / contracting and genuinely orderly consolidation
```

P011 already shows that raw churn, counter excursion or turn count cannot solve this by themselves.
Valid Stage2 cases may oscillate heavily inside a bounded contracting envelope.

### Existing Facts get first right of refusal

Before adding a Fact, C must test whether the distinction can be expressed through existing causal
T-only measurements and their allowed deterministic composition:

- range width and width evolution;
- directional efficiency;
- range traversal;
- counter-direction excursion;
- center movement as descriptive context;
- already-governed short/primary scale views where available.

### Candidate missing observable, not yet a Fact

A plausible missing concept is **range-envelope persistence / failed directional resolution** rather
than generic noise intensity. Examples include:

- repeated attempts to escape a local envelope followed by rapid re-entry;
- alternating boundary pressure rather than coherent one-side resolution;
- an envelope that repeatedly re-expands or re-centers while endpoint width happens to be narrow;
- disagreement between endpoint compression and the path of the compression envelope itself.

These are design hypotheses only. C must construct synthetic positive/negative counterexamples before
any formal Fact identity or threshold is authorized.

### Exit states

C must end in exactly one of:

```text
EXISTING_FACTS_SUFFICIENT
NEW_THRESHOLD_FREE_FACT_REQUIRED
ONTOLOGY_RECLASSIFICATION_REQUIRED
INCONCLUSIVE
```

It may not end by silently adding a third candidate channel.

## 6. Workstream D — successor decision fork

Only after A-C are reviewed may an approved successor design choose one or more bounded repair routes.

### D1 — projection / taxonomy repair

Use when relocation conflicts are predominantly genuine coexistence or when a single Stage code is the
wrong representation. Preserve atomic propositions and revise only the downstream projection / modifier
contract under a separate vocabulary decision.

### D2 — relocation proposition repair

Use only when R1 guard over-fire has a reproducible semantic shape. Reopen relocation through a new
fresh calibration/evaluation contract; do not retune P008 thresholds against P011 results.

### D3 — compact-disorder instability repair

Use only when C demonstrates a positive observable distinction from orderly consolidation. The repair
may be a third structural-instability channel if existing Facts are sufficient, or a separately
qualified threshold-free Fact followed by a later proposition projection if they are not.

The route may combine D1-D3, but each must keep separate evidence and claim ceilings.

## 7. What happens to Stage2

P011 gives strong development support to the repaired Stage2 topology:

```text
low directional efficiency
AND materially narrow current range
AND material contraction
AND relocation FALSE
```

P012 should **retain this topology as the leading design assumption** unless new semantic
counterexamples invalidate it.

However:

- the observed `order-balanced` numeric thresholds are not automatically frozen;
- Stage2 is not independently qualified;
- preserved 2021 remains unopened;
- P012 should avoid another 9x3 joint optimization if atomic proposition qualification can isolate the
  actual Stage0 problem.

A later fresh screen may hold the Stage2 semantic form fixed while still treating numeric profile
selection according to a separately preregistered contract.

## 8. Freshness and evidence rules

Consumed evidence may be used only for semantics, counterexamples and topology diagnosis:

- P011 2018 is consumed;
- P010 2019 is consumed;
- P009 2020 is consumed;
- P008 development/evaluation surfaces remain consumed according to their own bindings.

The exact P011-preserved 2021 plan remains unread and must not be converted into development data.

Before any fresh successor experiment, the project must create a **freshness inventory** over time,
economic identities and prior evidence exposure. The next development cohort is selected only after
that inventory and preregistration. This concept deliberately does not nominate a year or identity
pool.

## 9. Bounded delivery sequence and A-C result

A-C were explicitly authorized as consumed-evidence diagnostic work and are now closed:

```text
P3-STAGE-012-A  CLOSED  PASS_ATOMIC_PROPOSITION_FIRST
P3-STAGE-012-B  CLOSED  PASS_CONSUMED_SEMANTIC_ADJUDICATION
P3-STAGE-012-C  CLOSED  INCONCLUSIVE
P3-STAGE-012-D   CLOSED  RECOMMEND_SYNTHETIC_ONLY_ATOMIC_AND_ENVELOPE_ROUTE
P3-STAGE-012-E1  CLOSED  ATOMIC_FIRST_CONTRACT_ACCEPTED
P3-STAGE-012-E2  CLOSED  SYNTHETIC_NON_FACT_ENVELOPE_PROTOTYPE_ACCEPTED
P3-STAGE-012-E3  CLOSED  PASS_SYNTHETIC_CONFORMANCE
-------------------------------------------------------------
next required review:
P3-STAGE-012-E4  post-E Fact/projection route review
-------------------------------------------------------------
only after E4 + later owner approval:
P3-STAGE-012-F   fresh-cohort inventory + preregistration
P3-STAGE-012-G  fresh development / blind atomic review
P3-STAGE-012-H  projection compatibility and candidate freeze
-------------------------------------------------------------
only if H passes:
preserved 2021 evaluation may be considered under separate authority
```

The sequence intentionally postpones fresh data until the semantic and observational questions are
settled.

## 10. Explicit non-goals

P012 concept does not authorize:

- lowering the Stage0 0.70 coverage floor;
- choosing the P011 58/83 frontier;
- rewriting the P011 review ledger;
- adding a 28th P011 pair;
- carrying `order-balanced` numeric thresholds forward as qualified parameters;
- retuning the P008 relocation guard;
- automatically adding a compact-disorder channel;
- creating a new Fact before observational insufficiency is demonstrated;
- opening preserved 2021;
- using 2022-2025;
- daily labels, strategy consumption or trading.

## 11. A-C closed findings and next decision

A closes the ontology question for the next phase: qualify atomic propositions before Stage0/Stage2
projection; `instability+relocation` is valid coexistence and remains outside the Stage0/2 projection
without being discarded.

B classifies the eleven P011 relocation-veto cases as 0 guard-overfire, 2 likely reference under-calls
and 9 genuine coexistence cases. This does **not** authorize P008 guard retuning and instead strengthens
the case for atomic reference review.

C remains `INCONCLUSIVE`: current governed summary/path Facts do not positively express compact
disorder independently of Stage2, but the consumed packet does not prove that a new range-envelope
Fact is the correct abstraction. A sequential envelope-resolution hypothesis needs synthetic-only
adversarial semantics before any Fact decision.

E1/E2/E3 are now complete under the owner-approved synthetic-only authority. E1 reuses the frozen
Primary protocol and makes per-role atomic truths, mixed-state roles and projected denominators
explicit. E2 implements only deterministic close-envelope escape/re-entry evidence and emits no
compact-disorder truth or Fact identity. E3 closes `PASS_SYNTHETIC_CONFORMANCE`: high-churn orderly
compression and stable narrow noise produce no failed-resolution escape trace; a one-leg repricing
persists on one side; an adversarial failed-resolution path produces alternating resolved escapes on
both sides; unavailable input fails closed; and mixed instability+relocation remains explicit while
the downstream Stage0/2 projection stays `NEITHER`.

The required next step is E4 post-E expert review. It must decide whether the envelope semantics are
strong enough to justify a formal threshold-free Fact/projection repair, should remain a research-only
descriptor, or should be parked. P008 relocation remains frozen, no new Fact is registered, fresh
cohort work remains blocked, and preserved 2021 remains unread.
