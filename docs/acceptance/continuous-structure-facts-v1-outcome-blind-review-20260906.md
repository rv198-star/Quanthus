# Continuous Structure Facts v1 outcome-blind review (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-003` |
| Review result | PASS, 12 / 12 |
| Preregistered plan | `continuous-structure-facts-v1-outcome-blind-review-plan-20260906.md` |
| Plan commit | `6a4e0e1` |
| Result-bearing revision | `fd69b35b725310133a06ec19d27955bb15e5ba6f` |
| Reviewed Run | A / `job_8ce5f20bdbafa9f0040348e950797108` |
| Independent reproduction | B / `job_27181468d6da96eb0110bac567e1cd6a`, exact semantic reproduction |
| Scope | development-only T-close structural facts |
| Future/outcome evidence | not read and not present in review projection |

## 1. Selection conformance

The sample was selected by the rule frozen before Run A result access:

1. for each configured security in `000001`, `300750`, `600519` order, take the earliest active
   episode row with age 0, earliest active row with age >= 1, and earliest ended row when present;
2. remove duplicate security-date rows; and
3. fill to 12 by security round-robin over earliest remaining `available=true` rows.

The production API projected only the preregistered T-close fields plus a canonical row digest. It
excluded coarse-state, future-return, outcome, label, and portfolio fields.

## 2. Reviewed rows

| # | Security | Market date | Episode status | Age / holds | Row digest | Result | Key check |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 000001 | 2020-01-03 | active | 0 / 0 | `bf7f5a28...` | PASS | attempt row; close 17.18 > frozen breakout 16.95; anchor and current resistance agree |
| 2 | 000001 | 2020-01-06 | active | 1 / 1 | `5ca9b352...` | PASS | same episode/anchors as attempt; current rolling resistance may move independently |
| 3 | 000001 | 2020-01-08 | ended | 3 / 2 | `7e5e2701...` | PASS | same frozen breakout 16.95; close 16.66 is below it; explicit termination reason |
| 4 | 300750 | 2020-01-02 | active | 41 / 41 | `111cdff0...` | PASS | attempt predates report window, but row carries attempt date/snapshot and frozen anchors locally |
| 5 | 600519 | 2020-03-04 | active | 0 / 0 | `3fef7f90...` | PASS | attempt row; close 1128.92 > frozen breakout 1124.0 |
| 6 | 600519 | 2020-03-05 | active | 1 / 1 | `42ce047b...` | PASS | same episode/anchors; one completed post-attempt hold |
| 7 | 600519 | 2020-01-02 | ended | 2 / 1 | `45bd0a5c...` | PASS | close 1130 < frozen breakout 1176; frozen support differs legitimately from current rolling support |
| 8 | 000001 | 2020-01-02 | none | - | `e64c39f3...` | PASS | available rolling facts with all episode-bound fields null |
| 9 | 300750 | 2020-01-03 | active | 42 / 42 | `5d99002e...` | PASS | same long-lived frozen episode while current volume ratio is independently descriptive |
| 10 | 600519 | 2020-01-03 | none | - | `0cdf36c3...` | PASS | negative support margin is published as a fact; no episode or forced-action semantics are injected |
| 11 | 000001 | 2020-01-07 | active | 2 / 2 | `ad283805...` | PASS | same 000001 episode; age and hold progression is monotone and row-local |
| 12 | 300750 | 2020-01-06 | active | 43 / 43 | `f21b6cf8...` | PASS | same long-lived episode and frozen anchors; current rolling anchors evolve separately |

## 3. Independent numeric/lifecycle check

A second read-only check re-parsed the 12 projected rows with `Decimal` and asserted all of the
following:

- `source_close / rolling_resistance20 - 1 == resistance_margin` exactly;
- `source_close / rolling_support20 - 1 == support_margin` exactly;
- for episode rows, the corresponding breakout and frozen-support margins also recompute exactly;
- active rows have non-negative breakout margin, no termination reason, and `hold_count == age`;
- ended rows have negative breakout margin, `close_below_breakout_level`, and `hold_count < age`;
- no-episode rows have every episode-bound field null;
- knowledge cutoff remains on the same market date; and
- no `state`, `coarse_state`, future-return, outcome, or label field is present.

Observed result: `BLIND_REVIEW_NUMERIC_LIFECYCLE_PASS 12 / 12`.

## 4. Review findings

No sampled row required hidden prior-state replay to identify its active/ended episode. Frozen
breakout/support anchors and attempt identity stayed stable while current rolling anchors continued
to evolve. This is the intended separation between causal episode memory and current rolling facts.

Volume remained descriptive. In particular, an already-active 300750 episode persisted across
rows with different current volume ratios without changing its episode identity or frozen anchors.
The stronger contract that **episode creation is price-only** is additionally covered by the
pre-result synthetic adversarial test where an episode is created with volume ratio `0.1`.

The real-DWD report slice contained `history_reset_rows=0`. Therefore this review does **not** claim
real-sample reset coverage. Gap/reset fail-closed behavior is accepted from the deterministic
synthetic contract tests, which clear history/episode state and publish unavailable facts without
bearish or forced-sale semantics.

## 5. Conclusion and claim ceiling

```text
Outcome-blind sample: 12 / 12 PASS
Local T-close explainability: PASS
Frozen episode identity / anchors: PASS
Signed-margin arithmetic: PASS
Coarse-state authority leakage: NOT OBSERVED
Future/outcome leakage: NOT OBSERVED
Real-DWD history-reset example: NOT PRESENT
```

This review supports only the engineering claim that Continuous Structure Facts v1 is locally
auditable and causally coherent on the frozen development slice. It does not prove economic value,
production expert quality, thirteen-state validity, model value, or Shadow/paper/live readiness.
