# P3-STAGE-008 四方方向性评审记录

| Field | Value |
| --- | --- |
| Review date | 2026-09-11 |
| Review scope | Stage 0 directional boundary + primary/recent reference protocol |
| Governing design | `../design/stage-directional-boundary-repair.md` |
| Machine design | `../../config/stage-learning/directional-boundary-repair-design.v1.json` |
| Participants | short-term trader / fund manager / quant researcher / data-warehouse & evidence-governance |
| Provenance | one ChatGPT assistant simulating four responsibilities; not four independent experts or humans |
| Final design verdict | APPROVE DIRECTION / BLOCK IMPLEMENTATION UNTIL CONTRACT TESTS |

## 1. Review method

The review is intentionally adversarial rather than a four-vote approval ritual. Each responsibility
must provide:

1. one reason the design is directionally correct;
2. at least one counterexample that could make it wrong;
3. one condition that would veto implementation;
4. a response to another role's objection.

The review may approve a design direction while keeping runtime delivery blocked. It cannot upgrade
same-origin simulation into independent semantic validation.

## 2. Short-term trader review

### Positive finding

The revised target finally distinguishes “the price is moving strongly” from “the structure is
unstable without directional control.” This is the essential business boundary that old R0 missed.
A short-term trader would not call a clean repricing, trend leg or orderly correction “directionless
disorder” merely because the range center migrated.

The strongest design improvement is the explicit relocation guard. It prevents Stage 0 from
becoming a generic bad-state bucket whenever width or center moves.

### Counterexamples raised

**A. Up-down-up path ending at the high.** Endpoint traversal can be 1 even though the path contains
a full reversal. If range traversal alone became the guard, the design would repeat the same class
of compression error under a new name.

**Resolution:** accepted. `counter-direction-excursion-share` is mandatory before any relocation
guard can be frozen. Range traversal alone is expressly insufficient.

**B. One-day repricing followed by flat trading.** A discontinuous move can produce traversal=1 and
counter-excursion≈0 even without a multi-day trend.

**Resolution:** this is intentionally outside Stage 0. It is a coherent repricing at the available
daily resolution, not direction-neutral oscillatory instability. The design makes no claim about
its cause or future continuation.

### Implementation veto

Reject implementation if any Stage-0 code path can still assert from center shift or width expansion
without first resolving coherent relocation, or if a later developer reintroduces “not Stage2 =>
Stage0”.

## 3. Fund-manager review

### Positive finding

The distinction between **migration caused by repricing** and **migration caused by unstable
oscillation** matches how a longer-horizon investor interprets price structure. A 20-session center
shift aligned with a substantial start-to-end relocation is qualitatively different from a center
shift inside a path that repeatedly gives back its moves.

Keeping Stage 2 unchanged is also correct. The Stage-007 result showed strong precision and no
negative false positives; repairing Stage 0 should not reopen an unrelated successful proposition.

### Counterexamples raised

**A. Persistent decline with rebounds.** A fund view may describe this as a directional correction
while a short-term view may focus on violent swings. The system must not force either view into
Stage 0 just because instability is also present.

**Resolution:** if coherent relocation and instability are both positive, the 0/2 projection returns
outside-scope/NEITHER with a retained mixed-directional-instability reason. It does not force Stage0.

**B. Contracting 20-session structure with late 10-session expansion.** The old review process let
recent deterioration erase the primary consolidation judgement.

**Resolution:** Phase-P primary opinions are committed before explicit 10-session derived facts are
shown. Phase R can contradict the primary as a modifier but has no field able to rewrite it.

### Implementation veto

Reject implementation if “recent contradiction” changes the stored primary proposition, or if the
new design treats any directional relocation as automatically Stage 12. Directional-stage ownership
must remain separate.

## 4. Quant-research review

### Positive finding

The design fixes the abstraction rather than tuning a threshold. Stage-007 diagnostic reuse was
limited to Fact-shape evaluation, and no candidate threshold was searched. The exposed cases show a
clear reason to add path geometry while also showing that daily sign-reversal count is not useful.

The proposed Facts have distinct information roles:

- existing directional efficiency: magnitude-weighted balance of signed path movement;
- range traversal: endpoint displacement relative to the observed close range;
- counter-direction excursion: temporal severity of movement against the endpoint direction.

The three are not identical. The `0 -> 10 -> 0 -> 10` counterexample demonstrates why the latter two
cannot be collapsed into one.

### Counterexamples raised

**A. Overfitting the twelve false positives.** The strong Stage-007 diagnostic separation could tempt
a developer to choose an apparent cutoff directly from the exposed cohort.

**Resolution:** the design freezes no Stage threshold. Stage-007 is permanently design-exposed and
explicitly forbidden for threshold learning or qualification. A later threshold candidate requires
a new disjoint development cohort.

**B. Feature inflation.** Adding reversal rate, session agreement, R², slope, excursion and multiple
variants would create a high-dimensional search justified by only twelve errors.

**Resolution:** v1 is deliberately minimal. Reversal rate and session agreement are not promoted;
linear R² is kept out. Only range traversal and counter-direction excursion are proposed.

**C. Double counting correlated direction evidence in one weighted score.** Window return,
efficiency and traversal are correlated.

**Resolution:** no weighted score is authorized. Instability and relocation remain separate
predicates with explainable reason codes.

### Implementation veto

Reject implementation if the Fact family contains Stage thresholds, if Stage-007 is used to choose
cutoffs, or if the implementation merges correlated Facts into an opaque scalar before semantic
validation.

## 5. Data-warehouse / evidence-governance review

### Positive finding

The design respects ownership. Window return and directional efficiency remain in Trend Evolution;
the new family adds only the two missing path-geometry observations. The proposed calculations need
only the same contiguous admitted close closure and therefore can share one deterministic
materialization rather than rereading or rebuilding source data.

The two-phase reviewer protocol is materially stronger than prompt-only discipline because it makes
the primary commit an immutable event before explicit recent facts are exposed.

### Counterexamples raised

**A. False blinding claim.** A 20-session raw path necessarily includes its latest ten sessions, so a
reviewer can still visually notice late expansion before Phase R.

**Resolution:** accepted and documented. The protocol does not claim cognitive blinding from recent
history. It claims the narrower enforceable property that explicit derived 10-session facts cannot
post-hoc mutate the primary ledger.

**B. Corporate-action/listing-coordinate contamination.** Path geometry is meaningless across a
coordinate reset.

**Resolution:** same-listing/same-coordinate/contiguous-admission reset semantics are inherited
exactly. Reset or missing primary input yields unavailable, never Stage 0.

**C. Fact duplication.** A new family must not reimplement window return or efficiency under new
names.

**Resolution:** the machine design explicitly lists them as reused Facts and forbids duplication.

### Implementation veto

Reject implementation if it opens a second provider/source read for the same Job, creates an ADM
solely to cache diagnostics, loses coordinate/reset lineage, or allows recent fields to appear in the
primary opinion schema.

## 6. Cross-review: adversarial scenario table

| Scenario | Trader | Fund | Quant | Governance | Final design judgement |
| --- | --- | --- | --- | --- | --- |
| monotonic up/down | directional | directional | traversal high, counter excursion low | causal | relocation guard; no Stage0 |
| stair-step trend | directional unless pullbacks dominate | repricing | multi-Fact guard, no single cutoff yet | causal | relocation or ambiguous; no residual Stage0 |
| `0→10→0→10` | unstable swings | mixed | traversal alone fails; counter excursion catches | reproducible | not coherent by traversal alone |
| broad oscillation ending near start | disorder | disorder | traversal low, counter excursion high | reproducible | Stage0 may be supported |
| one jump then flat | repricing | repricing/event-like | traversal high, counter excursion low | no causal attribution | no Stage0 |
| large aligned center migration | repricing | repricing | center shift must be conditioned by direction | traceable | center shift alone cannot assert Stage0 |
| contracting box | consolidation | consolidation | existing Stage2 remains | unchanged owner | Stage2 may assert |
| contracting box + recent expansion | primary box, recent risk | same | recent is separate variable | primary commit blocks mutation | Stage2 primary can remain; recent contradicts |
| missing/reset data | no call | no call | unavailable | fail closed | INPUT_UNAVAILABLE |

No role found a scenario requiring the design to restore residual Stage0 classification.

## 7. Directional-error findings and resolutions

### Finding DIR-001 — old target mixed relocation with disorder

**Severity:** blocker in predecessor.  
**Resolution:** atomic relocation proposition + Stage-0 guard.  
**Status:** CLOSED IN DESIGN.

### Finding DIR-002 — range traversal can be fooled by full reversal paths

**Severity:** blocker if traversal used alone.  
**Resolution:** counter-direction excursion is a co-required proposed Fact; traversal-alone guard is
forbidden.  
**Status:** CLOSED IN DESIGN.

### Finding DIR-003 — recent facts contaminated primary semantics

**Severity:** blocker in reference process.  
**Resolution:** primary commit before recent packet; recent schema cannot modify primary; resolver
ignores recent for primary status.  
**Status:** CLOSED IN DESIGN.

### Finding DIR-004 — exposed-cohort overfitting risk

**Severity:** high.  
**Resolution:** Stage-007 cohort permanently design-exposed; no thresholds or qualification from it.  
**Status:** CLOSED IN DESIGN.

### Finding DIR-005 — Stage2 collateral-change risk

**Severity:** high.  
**Resolution:** Stage2 rule changes are explicitly unauthorized in this repair.  
**Status:** CLOSED IN DESIGN.

### Finding DIR-006 — false independent-review/blinding claim

**Severity:** governance blocker.  
**Resolution:** review provenance remains same-origin simulation and full cognitive blinding remains
false.  
**Status:** CLOSED IN DESIGN.

## 8. Final four-party verdict

All four responsibilities approve the **direction of the design** after the above changes. None
approves immediate market execution.

The agreed next move is narrowly bounded:

1. implement only the two threshold-free path Facts and the primary-first reference contract;
2. prove the adversarial synthetic cases and exact reset/PIT semantics;
3. use Stage-007 only as an exposed regression/diagnostic set, never to select thresholds;
4. only then request authority for one new disjoint development screen.

Therefore:

```text
DESIGN: APPROVED
DIRECTIONAL BLOCKERS IN DESIGN: 0 OPEN
IMPLEMENTATION: BLOCKED
NEW MARKET READ / JOB / PARAMETER SEARCH: NOT AUTHORIZED
STAGE 2 RESEARCH: NOT REOPENED
2022-2024 / 2025: NOT OPENED
```

This verdict means the proposed repair is internally coherent across the four simulated
responsibilities. It is not independent expert validation and must not be reported as such.
