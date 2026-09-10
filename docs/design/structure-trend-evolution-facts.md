# 趋势演化事实族 / Trend Evolution Fact Family

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Governing work item | `P3-STRUCT-006` |
| Trigger | accepted `P3-STAGE-003` third-group morphology review |
| Parent baseline | `structure-facts-v2.md`; `structure-support-facts.md` |
| Fact contract | `../../config/structure-trend-evolution.v1.json` |
| Qualification | `../../config/structure-trend-evolution-qualification.v1.json` |
| Code | `../../src/quanthus/structure_trend_evolution.py` |
| Claim ceiling | threshold-free continuous Structure facts only; no Stage 1/8/9/12 projection or trading authority |

## 1. Decision

`P3-STAGE-003` showed that the third group should not proceed as six peer label searches. The first
reusable prerequisite is a narrow **趋势演化事实族 / Trend Evolution Fact Family** serving the
`1/12` downside lifecycle and the directional substrate later needed by `8/9` high-side modifiers.

This milestone formalizes only continuous quantities that were already preregistered and observed in
the third-group morphology profile. It does not formalize Structure Order facts, activity relations,
Stage thresholds or primary-stage arbitration.

## 2. Fact-family boundary

The family owns seven measurement concepts with fixed first-version windows:

| Concept | v1 windows | Meaning |
| --- | --- | --- |
| `structure.geometry.window-return` | 5 / 20 / 60 | simple return from the exact prior admitted session at the declared lag |
| `structure.geometry.directional-efficiency` | 5 / 20 | signed net close displacement divided by total absolute close path |
| `structure.geometry.return-velocity` | 5 / 20 | simple return divided by session count |
| `structure.geometry.velocity-delta` | 5 vs 20 | short-window return velocity minus medium-window return velocity |
| `structure.geometry.range-position` | 60 | close position inside the causal rolling high/low range |
| `structure.geometry.drawdown-from-window-high` | 60 | close / causal rolling high - 1 |
| `structure.geometry.rebound-from-window-low` | 60 | close / causal rolling low - 1 |

The formal family deliberately excludes current/prior range-width comparison and center shift; those
belong to the later **结构秩序事实族 / Structure Order Fact Family**. It also excludes volume/activity;
Stage-9 price-volume divergence may request a separate narrow activity relation only after both
price-structure fact families exist.

## 3. Direction and velocity semantics

`velocity_delta_5_vs_20 = return_velocity_5 - return_velocity_20` is a mathematical fact, **not an
acceleration label**. Its interpretation depends on the directional context.

Examples:

- medium-term decline `-1%/session`, recent decline `-0.3%/session` -> positive velocity delta;
  this means the decline has become less negative, not that downside is accelerating;
- medium-term rise `+0.5%/session`, recent rise `+1%/session` -> positive velocity delta;
  here it is consistent with upside acceleration.

Therefore no Fact ID, reason code or schema field may call positive delta `faster` or `accelerating`.
Stage/proposition layers may interpret the same fact only after binding an explicit direction context.

## 4. PIT, identity and failure semantics

- Current windows include T; a return at lag N uses the exact admitted close N sessions earlier.
- The first version requires 61 uninterrupted admitted daily sessions for a complete observation.
- Nonadmitted rows, hidden calendar gaps, listing changes and coordinate changes reset and rewarm the
  sequence. They do not create bearish facts or carry old history across the discontinuity.
- Source closure binds the exact input member revisions used by each measurement.
- Wall-clock generation, publication and consumption time do not enter Fact semantic identity.
- A zero-width 60-session range suppresses only `range-position`; returns/efficiency/velocity remain
  independently available when their inputs are valid.
- Rolling high/low are causal window extrema. They are not confirmed swing tops/bottoms and do not
  imply future reversal.

## 5. Relationship to Stage 1 / 12 / 8 / 9

This milestone emits **no Stage proposition**.

After this fact family is independently accepted, later stage research may test:

- Stage 12 as a downside-context proposition using direction, efficiency and persistence;
- Stage 1 as a nested deceleration/stabilization condition using the same context plus velocity delta,
  range position and rebound evidence;
- Stage 8 as an upside acceleration/extension modifier over an advancing context;
- Stage 9 only after an additional price-volume divergence relation is justified.

The profile evidence does not preselect those thresholds. The phrases `止跌企稳`, `赶顶`, and `派发`
remain interpretations, not Structure Facts.

## 6. Qualification route

To avoid rebuilding market data already qualified twice, the first bounded qualification consumes
only the two accepted immutable `P3-STRUCT-005` F9/Support-Evolution cold-run artifacts. Each source
artifact must first pass its original deep verifier. Their exact `source-inputs.jsonl` payloads must
be byte-identical.

For each source independently:

```text
accepted immutable F9 artifact
        -> original F9 deep verifier
        -> exact published DailyInput rows
        -> normal B2 admission rules
        -> Trend Evolution Facts
```

The two fact payloads and semantic digests must be identical. This qualification records:

```text
market_data_reads = 0
dwd_provider_opens = 0
```

It is a bounded engineering/semantic reproduction on the existing three-security 2020Q1 slice, not
external validation or proof that any Stage threshold is correct.

## 7. Exit criteria

`P3-STRUCT-006` may close only if:

1. the seven concepts are registered without changing existing Structure concept identities;
2. formula-compatibility tests reproduce the frozen third-group metric definitions exactly;
3. reset, zero-range, prefix-replay and no-stage-semantics counterexamples pass;
4. the fixed Event Worker route accepts only the governed qualification config;
5. the two immutable source artifacts pass original deep verification and reconstruct byte-identical
   Trend Evolution Facts;
6. a deterministic T-only review sample shows no obvious semantic contradiction;
7. no Stage projection, parameter search, LabelCard, DWD/provider, holdout, daily, strategy or trading
   authority is opened.

A PASS permits the fact family to become an accepted bounded Structure capability. It does not open
Stage 1/12 or Stage 8/9 search automatically. The next accepted successor remains the Structure Order
Fact Family unless the owner changes the route.
