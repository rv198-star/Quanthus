# 结构秩序事实族 / Structure Order Fact Family

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Governing work item | `P3-STRUCT-007` |
| Trigger | accepted `P3-STAGE-003` third-group morphology review and accepted `P3-STRUCT-006` Trend Evolution Facts |
| Parent baseline | `structure-facts-v2.md`; `structure-trend-evolution-facts.md` |
| Fact contract | `../../config/structure-order-facts.v1.json` |
| Qualification | `../../config/structure-order-qualification.v1.json` |
| Code | `../../src/quanthus/structure_order_facts.py` |
| Claim ceiling | threshold-free rolling range geometry only; no Stage 0/2 classification or trading authority |

## 1. Decision

The third-group profile showed that low directional efficiency is common and therefore cannot by
itself distinguish Stage 0 disorder from Stage 2 orderly consolidation. The missing reusable
substrate is **structure organization**, not another Stage threshold.

This milestone formalizes a narrow **结构秩序事实族 / Structure Order Fact Family** containing range
width, width evolution and range-center movement. It deliberately reuses the already accepted
`structure.geometry.directional-efficiency` from the Trend Evolution Fact Family instead of
redefining or duplicating it.

No Fact in this family says `orderly`, `disorder`, `accumulation`, `distribution`, `breakout ready` or
`Stage 0/2`. Those remain later proposition-layer interpretations.

## 2. Fact-family boundary

The first version owns three measurement concepts:

| Concept | v1 specs | Meaning |
| --- | --- | --- |
| `structure.geometry.range-width` | 10 / 20 / 60 | normalized high-low width using the window midpoint |
| `structure.geometry.width-change` | current 10 vs prior 10; current 20 vs prior 20 | current width relative to the immediately prior equal-length non-overlapping window |
| `structure.geometry.range-center-shift` | current 10 vs prior 10; current 20 vs prior 20 | current range midpoint relative to the immediately prior equal-length non-overlapping midpoint |

The family does **not** own directional efficiency; that fact is reused from `P3-STRUCT-006` when a
later Stage-0/2 proposition needs multiple dimensions. It also excludes volume/activity, confirmed
pivots, trend labels and Stage compression.

## 3. Exact geometry

For one admitted window with high `H` and low `L`:

```text
midpoint = (H + L) / 2
range_width = (H - L) / midpoint
```

For an immediately prior, equal-length, non-overlapping window:

```text
width_change = current_width / prior_width - 1
center_shift = current_midpoint / prior_midpoint - 1
```

A negative `width_change` means the current normalized range is narrower than the prior window. It
is **not** automatically called consolidation or accumulation. A near-zero `center_shift` means the
range midpoint changed little relative to the prior midpoint; it is not proof of a stable market
regime by itself.

If the prior window has zero range width, `width-change` is unavailable because its denominator is
zero. `range-center-shift` remains independently computable because its denominator is the positive
prior midpoint, not the range width. Other facts must not be suppressed merely because one
measurement is unavailable.

## 4. PIT, identity and failure semantics

- Current rolling windows include T.
- Comparison windows are immediately prior, equal-length and non-overlapping.
- First-version runtime requires 60 uninterrupted admitted final-daily observations.
- Listing/coordinate changes, nonadmitted rows and hidden session gaps reset and rewarm history.
- Source closure binds the exact admitted member revisions used by each fact.
- Generation/publication/use clocks do not enter semantic Fact identity.
- The algorithm never uses future bars to decide whether a range is ordered or important.
- Range contraction/center stability are geometry observations only; no actor intent or future
  breakout/reversal meaning is implied.

## 5. Relationship to Stage 0 / 2

The accepted third-group review freezes only the later modeling hypothesis:

```text
Trend Evolution directional efficiency
        +
Structure Order range-width evolution
        +
Structure Order center stability
        ↓
future Stage-0 / Stage-2 proposition research
```

Stage 0 must be positive evidence of disorder, not a residual `else`. Stage 2 must be positive
evidence of organized consolidation and cannot be inferred from low return or range contraction
alone. This milestone therefore performs **no Stage threshold search**.

## 6. Qualification route

As with Trend Evolution, the first bounded qualification avoids rebuilding market data. It consumes
exactly the same two independently accepted immutable `P3-STRUCT-005` F9/Support-Evolution source
artifacts. Each source first passes the original F9 deep verifier; its exact published daily input
rows are then re-admitted and used to reconstruct Structure Order Facts.

```text
accepted immutable F9 source A/B
       -> original deep verification
       -> exact source-input rows
       -> normal admission
       -> Structure Order Facts A/B
       -> byte-identical comparison
```

The qualification must report:

```text
market_data_reads = 0
dwd_provider_opens = 0
```

A deterministic T-only review sample is used only for semantic sanity. It is not a Stage-label test
and does not inspect future outcomes.

## 7. Exit criteria

`P3-STRUCT-007` may close only if:

1. the two existing concepts are reused and only missing `range-center-shift` is newly registered;
2. formal formulas exactly reproduce the frozen third-group profile geometry;
3. zero-prior-width, reset, prefix-replay and no-Stage counterexamples pass;
4. the fixed Event Worker route admits only the governed Structure Order qualification config;
5. both immutable source artifacts pass original deep verification and reconstruct byte-identical
   Structure Order fact payloads/digests;
6. deterministic review finds no obvious semantic contradiction;
7. no Stage 0/2 projection/search, DWD/provider read, LabelCard, holdout, daily, strategy or trading
   authority opens.

A PASS makes this a bounded accepted Structure capability. It does not automatically authorize
Stage 0/2 parameter selection. After acceptance, the third-group facts substrate is complete enough
to separately design the minimal Stage 0/2 proposition experiment; high-side Stage 9 activity
relation remains a different, later question.
