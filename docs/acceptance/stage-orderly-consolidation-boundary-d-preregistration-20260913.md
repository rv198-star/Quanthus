# P3-STAGE-011-D — Fresh Joint Stage0/Stage2 Development Preregistration

## Disposition

```text
PREREGISTRATION: ACCEPTED / IMMUTABLE BEFORE FRESH DATA
DEVELOPMENT YEAR: 2018
PLANNED REVIEW CASES: 192 / 48 PER QUARTER
STAGE0 PROFILES: 9 EXACT P010 TOPOLOGY
STAGE2 PROFILES: 3 EXACT P011 TOPOLOGY
JOINT PAIRS: 27 EXACT
PRESERVED 2021 EVALUATION: UNREAD
MARKET DATA / RUNTIME: NOT YET AUTHORIZED
```

Machine contract:
`config/stage-learning/orderly-consolidation-joint-development-screen.v1.json`

Digest:
`sha256:c9b5eff892f3d731170afac052e3810543f7444f5b6025bdef24c1ebfa4c213e`

## 1. Why 2018 is the development period

P011 must not reuse P010's consumed 2019 references or P009's consumed 2020 references for numeric
selection. The exact 2021 plan remains preserved for later evaluation. The preregistration therefore
moves one full calendar year earlier to 2018 and freezes that choice before any P011 result-bearing
read.

The D1 source scope is:

```text
history / warm-up start: 2017-09-01
development start:       2018-01-01
development end:         2018-12-31
candidate cohort date:   2017-12-29
```

`2017-12-29` is already present as a governed historical market date in repository evidence. The
preregistration deliberately uses that observed trading-day endpoint rather than natural-year end,
preventing the calendar-endpoint defect previously exposed by P010.

This choice does not assert that RX6800 source readiness has already been proven. D1 engineering must
fail before submission if its exact required source partitions or bindings cannot be verified.

## 2. Fifth fresh economic-identity pool

D1 must construct a fifth fresh pool of 512 economic identities after excluding the complete exposed
identity surfaces from:

- Stage-007;
- the entire Stage-008 fresh pool;
- the entire Stage-009 512-identity fresh pool, including the still-unread 2021 evaluation identities;
- the entire Stage-010 512-identity fresh pool.

The first 256 eligible fresh identities by the frozen hash ordering form development capacity. The
preserved 2021 identities must have zero overlap with that capacity.

No P010 2019 or P009 2020 market data may be read during D1. Their prior results are consumed design
and diagnostic evidence only.

## 3. Why 192 review cases

The joint proposition now requires adequate semantic support for two positive classes rather than only
the P010 Stage0 target. D therefore freezes 192 planned cases as `48 × 4 quarters` before any 2018
result is visible.

Selection remains deliberately morphology-blind:

- identities are ranked by the frozen hash seed only;
- each selected identity is assigned one quarter;
- the date is selected by hash only inside that quarter;
- one case per economic identity;
- no cross-quarter backfill;
- no identity backfill;
- no result-dependent supplement.

The 192 target is a review-capacity decision, not a claim about the expected 2018 Stage2 prevalence.
If the fixed sample cannot satisfy the preregistered semantic support gates, D closes rather than
backfilling toward the missing class.

## 4. Reference-sufficiency gates are not lowered after P010

P010's Stage0 development gates remain exact:

- minimum definite Stage0 positives: 18;
- minimum Stage0 predictions: 15;
- precision >= 0.78;
- positive-reference coverage >= 0.70;
- definite-negative FPR <= 0.10;
- no regression versus the frozen P008 Stage0 baseline under its existing precision/FPR/coverage
  rules.

For Stage2, the development denominator must contain at least **20 definite Stage2 positive
references**. This deliberately inherits the earlier P3-STAGE-004 Stage2 reference-sufficiency floor.
It is not lowered to 15 or 16 after observing P010's 2019 prevalence.

Stage2 quality gates are frozen before fresh data:

- minimum predictions: 12;
- precision >= 0.80;
- positive-reference coverage >= 0.65;
- definite-negative FPR <= 0.10.

The joint pair must assert Stage0 and Stage2 simultaneously on exactly zero development cases.
Overlap is a semantic failure, not something resolved by class precedence.

## 5. Exact 9 × 3 pair surface

P011 does not add a tenth Stage0 candidate. D rebuilds numeric values on the fresh unlabeled 2018
threshold basis while retaining the exact approved P010 bundled topology:

```text
3 expansion profiles × 3 broad-reversal profiles = 9 Stage0 profiles
```

Stage2 has exactly the three P011 profiles frozen before fresh data:

```text
order-lenient:  efficiency max Q70 / width max Q40 / contraction min Q30
order-balanced: efficiency max Q60 / width max Q30 / contraction min Q50
order-strict:   efficiency max Q50 / width max Q20 / contraction min Q70
```

The resulting surface is exactly 27 Cartesian pairs. No pair, profile or independent threshold knob
may be added after results.

If multiple pairs pass all hard gates, the frozen order is:

1. maximize the weaker of Stage0/Stage2 precision;
2. minimize the worse of Stage0/Stage2 FPR;
3. maximize the weaker of Stage0/Stage2 coverage;
4. Stage0 candidate id ascending;
5. Stage2 candidate id ascending.

## 6. Review blindness and threshold construction

Threshold basis uses all available unlabeled 2018 primary-scale observations from the 256 development
capacity identities. Labels and review opinions are invisible during basis construction.

The 192 review cases expose only the primary T-time packet permitted by Reference Protocol v2. Before
any pair scoring:

- both `short_term` and `fund` opinions must be durably sealed;
- candidate output is absent;
- threshold basis is absent;
- explicit recent derived Facts are absent;
- future rows are absent.

Cross-role disagreement remains `AMBIGUOUS`; there is no majority or post-score adjudication.

## 7. Preserved 2021 evaluation boundary

D retains the exact Stage-009 plan:

- identity count: 160;
- identity digest: `sha256:8bbbd7ca7110d06bfe07db6c2f555cc093b0c1a259aa666e359a637f9157b1e8`;
- plan digest: `sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7`.

D1 may verify and bind the plan but may not read 2021 market rows. D3 may open only that exact plan and
only after D2 has durably frozen one pair. There is no reselection or backfill.

Evaluation retains the same zero-overlap requirement. It uses preregistered class-quality floors and
can claim at most `PROMISING_EXPLORATORY_JOINT_BOUNDARY_REPAIR`.

## 8. Budgets and stop rules

The full D sequence is:

```text
D1 fresh 2018 prepare + exact preserved-2021 binding
D2 seal references -> score exactly 27 -> freeze at most one pair
D3 open exact preserved 2021 only after pair freeze
D4 seal evaluation references -> score only frozen pair
```

Budgets are frozen as:

- maximum result-bearing market-data Jobs: 2 (`D1`, later `D3` only if D2 freezes a pair);
- maximum development candidate pairs: 27;
- semantic retries: 0;
- automatic supplements: 0;
- automatic technical retries: 0.

If D1 lacks the fixed semantic denominator, or D2 has zero eligible pairs, D closes
`REVISE_OR_PARK_DEVELOPMENT_FAIL` and 2021 remains unread.

## 9. Current authority ceiling

This acceptance freezes the preregistration only. It does **not** yet authorize a market-data read or
runtime submission. D1 source isolation, artifact publication, candidate-blind review transport,
one-shot budgets and exact-pair scoring must first pass engineering acceptance on a durable commit.

2019 P010, 2020 P009, preserved 2021 market rows, 2022-2024, 2025, future outcomes, official labels,
daily activation, strategy and trading remain closed.
