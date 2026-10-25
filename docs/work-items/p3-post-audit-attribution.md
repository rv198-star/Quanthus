# P3 post-audit attribution and next control

This work follows the Owner's October 25 authorization to execute step 1 of the
post-series audit and decide conditionally whether steps 2 and 3 add value.
It uses the already exposed 2012–2021 study; it does not accept original strict
B/D, historical PIT, a strategy, P6, or production.

## Step 1 evidence

The complete B/T/TCH archives have 22,955 identical entry coordinates and two
physically independent roles. The new repository entry points check all file
hashes, manifests, source/request identities, typed business graphs, fees, daily
snapshots, position cycles and rounded daily returns. The original acceptance
time and the Owner's 160 MiB cap amendment are verified as historical records;
this does not restart their expired replay clock.

Raw candidate observations are partitioned exactly into cooldown, missing
industry, concentration exclusion and retained observations. Of 46,238
post-cooldown candidates, 23,283 lack static industry; 22,955 remain. All ten
years have industry exclusions, increasing from 39.52% in 2012 to 63.15% in
2021. This is candidate coverage, not a full-market denominator or reconstructed
historical industry. Listing identities are preserved; listing/delisting dates
are not inferred from a security's last candidate.

The 51 B/T position cycles with the same security and signal date have identical
actual financial outcomes. The full T−B difference is an exact partition:

| Actual path component | PnL difference on 500,000 initial capital |
| --- | ---: |
| 51 shared cycles | 0.00 |
| 287 T-only cycles | +62,608.20 |
| Remove 487 B-only cycles from the comparison | −283,702.76 |
| Total T−B | −221,094.56 / −44.218912 percentage points |

Of the 487 B-only cycles, 388 had a below-threshold T entry decision; their
actual B PnL was +193,919.38. Another 99 had qualified T decisions but no T fill;
their actual B PnL was +89,783.38. These are observations about different
realized paths, not profits T could necessarily have earned with different
capital competition. They motivate investigation of entry selection and
allocation, rather than changing exits or blaming higher fees.

T and TCH have exactly the same 4,370 qualified entry decisions. Their 338/309
actual position cycles differ after allocation, sizing and held management.
TCH's lower drawdown still combines an entry cap, held-risk actions and lower
exposure. No component-level causal claim follows from this attribution.

## Repository entry points

Production logic lives in `quanthus.expert_strategy.p3_history_review` and
`quanthus.expert_strategy.p3_history_attribution`. They consume explicit frozen
path/SHA bindings; source roots and network are not opened. The files are
read-only inputs, and an existing output or output inside either archive is
rejected before any output write.

Run the verifier with:

```bash
PYTHONPATH=src python -m quanthus.expert_strategy.p3_history_review \
  --contract ARCHIVE_CONTRACT.json --primary PRIMARY_WORK --cold COLD_WORK \
  --admission-evidence ADMISSION_BINDINGS.json --output NEW_REVIEW.json
```

Run the combined coverage/attribution entry point with the same four bindings
plus `--input-bindings ATTRIBUTION_BINDINGS.json`. The latter binds the raw
candidate census, preparation and unchanged P4 rule card. This is a revalidation
and diagnostic interface, not a source-admission or strategy-execution interface.

The five-session concentration selector is explicitly offline. Its synthetic
regression demonstrates that future candidate arrivals can change earlier
membership when the cap binds. The existing study has zero such rejections.
An expanded or executable-signal successor needs a versioned causal selector;
the frozen card and old results remain unchanged.

## Conditional continuation

TC means the same Trend entry plus the existing 20% unit entry cap, with base
held management. It is useful only to distinguish cap effects from held-risk
effects on the same source-qualified history. No parameter search, new source
collection, population extension or new rights election is implied.

Step 2 may start after step 1 checks, an explicit decision and an execution
freeze bind the exact existing source/role prerequisites, current code/config,
all costs, resource limits and an independent cold role. Existing T/TCH anchors
can be reused only with complete semantic closure and unchanged science.

TH is conditional, not automatic. It is needed only if TC leaves a meaningful
question about uncapped held-risk value or interaction. A new source blocker
stops the affected new cell and is routed to P10; it does not authorize a new
collection loop or alter any completed study.

Runtime/acceptance budget is 90 minutes per new cell, engineering up to two
hours. A qualified negative or inconclusive outcome closes the cell. Preserve
all earlier source clocks, failed attempts, unavailable visibility, uniform
non-subscription and production boundaries.

## Completed fixed controls, 2026-10-25

All three post-audit steps are complete. Step 1 verified and attributed existing
outputs; step 2 ran TC; the prospective cost gate selected step 3, which ran TH
and closed the component judgment. This is completion of this bounded diagnostic,
not acceptance of original strict B/D or a production strategy.

Execution commit: `d6f8b7524a650cffc2863822ebfbffb2c378f696`.
Frozen control request digest:
`sha256:75c4434ef15bb54cf64721387fcad6db77721ebbcc3fb0decc451883a831f83c`.
The parent source remains
`sha256:bb02e9695716d29683e78c9c497b87286743b35f087d06509eafe43985ee2ff8`,
with actual information cutoff `2026-10-25T08:51:06.934678+00:00`.
This late snapshot does not become information known in 2012–2021.

Each new arm completed 2,430 sessions in two fresh, kernel-isolated processes.
Each role reused its own previously independently built immutable inputs; features
were not independently rebuilt again in this job. Complete source/config/code
bindings, native reads and network denial, exact file sets, typed business graphs,
entry surfaces, financial accounting and unchanged-input identities all passed.
The 126 non-code input files per role have no cross-role inode overlap. All 70
business files across the five arms and two roles are physically distinct.
There were zero new source requests or source modifications.

### Full-period results

These are net **ten-year cumulative** returns on 500,000 initial capital, not
annualized returns or exposure-matched alpha. Average exposure means the mean
daily invested-capital fraction. All monetary accounting residuals are zero.

| Arm | Entry and held treatment | Net return | Maximum drawdown | Average exposure |
| --- | --- | ---: | ---: | ---: |
| B | Simple P4 candidate reference, base held policy | 52.19% | 12.58% | 18.42% |
| T | Trend, base held policy | 7.97% | 18.72% | 21.39% |
| TC | Trend, 20% unit entry cap, base held policy | 9.33% | 13.17% | 14.45% |
| TH | Trend, no entry cap, full held-risk policy | 28.60% | 6.32% | 10.15% |
| TCH | Trend, 20% unit entry cap, full held-risk policy | 9.83% | 11.17% | 8.62% |

TC explained 73.48% of the T→TCH drawdown difference, below the already frozen
80% research-cost gate; this selected TH before its outcomes existed. The gate
controls the cost of this diagnostic, not strategy acceptance or risk limits.

TH−T adds 20.628772 percentage points of cumulative return and reduces maximum
drawdown by 12.397734 points. TCH−TH loses 18.769814 return points and increases
drawdown by 4.843747 points despite lower average exposure. The fixed return
interaction `TCH−TC−TH+T` is −20.137558 points. The cap and held-risk policies
therefore cannot be treated as independent, additive improvements in this
portfolio. Actual sizing, capital queues and changed position paths matter.

The TCH−TH PnL difference is fully reconciled:
−30,614.58 on 220 shared entry cycles, −2,736.85 on 89 TCH-only cycles, and
−60,497.64 from the 82 TH-only cycles, totaling −93,849.07. This is an exact
partition of observed paths, not an assertion that foregone trades could all
have been executed together.

### All predefined two-year slices

Portfolio state carries across boundaries; these are not separately restarted
or selected backtests. All ten annual slices are also retained in the safe
matrix evidence, including periods unfavorable to the proposed direction.

| Slice | B | T | TC | TH | TCH |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2012–2013 | 5.18% | −0.18% | 0.10% | 1.19% | 1.40% |
| 2014–2015 | 24.82% | 18.97% | 12.52% | 16.86% | 13.37% |
| 2016–2017 | −0.14% | −1.61% | −1.71% | −1.04% | −3.46% |
| 2018–2019 | −2.43% | −7.63% | −4.96% | −0.14% | −4.71% |
| 2020–2021 | 18.98% | 0.03% | 3.91% | 10.03% | 3.86% |

TH exceeds T in four of five two-year slices and seven of ten years. It exceeds
TCH in four of five slices and nine years. Against B it wins only one two-year
slice and three years; full-period return is 23.590140 points lower, with lower
exposure and drawdown. This supports retaining held-risk as a research candidate,
not declaring TH superior to B or promoting it to production. Neither independent
process reproduction nor the all-period report is an untouched holdout test.

### Keep, change and stop

- Keep the full held-risk module as a candidate risk-control component, the simple
  B reference, exact source evidence and reusable verification assets.
- Stop micro-tuning this Trend hard-entry version or treating the 20% entry cap
  as an independently proven improvement. Old policies and archived studies stay
  unchanged; this is a research disposition only.
- Test one next hypothesis if research continues: **BH**, the same B entry with
  the exact existing held-risk policy and no added 20% cap. Compare BH−B first and
  BH−TH second to determine whether the useful protection requires Trend entry.
  This is a proposed new frozen request, not an activated experiment or parameter
  search. Use the same admitted window, costs, rights disposition and independent
  own-role prerequisites. Preflight new held-action needs before expensive work;
  a missing hard source qualification stops only the affected cell through P10.

Before any BH run, freeze its complete identity and one-shot limits. A sufficient
initial proposal is up to 60 minutes of narrow wiring/focused checks, 90 minutes
for one two-role replay plus full acceptance, and 30 minutes for the whole-sample
judgment. Report all ten annual/five two-year slices and returns/drawdown/exposure
together; do not choose a winning period or tune the risk/entry thresholds.
Stop at the cap with a qualified result or a precise blocker. No new collection,
protected-window result access, 66-future-session wait or P6 change is implied.

## Checks and retained evidence

159 focused tests passed. Changed-file Ruff, format and mypy checks passed.
`make check` on the execution commit produced **4,158 passed, 128 failed,
20 skipped**; all 128 failure IDs match the fresh prior audit baseline, with zero
new failures. Doctor and compile passed; planning did not run after pytest's
failure and was run separately successfully. Repository Ruff retains the same
217 baseline diagnostics; the changed files have zero diagnostics. The repository
is not globally green, and unrelated baseline failures were not changed.

Private job evidence is under
`/srv/agentdock/.codex/p3-post-audit-step1-20261025T105859Z-948ace8/`.
The `evidence/` directory owns the Step 1 safe summary, TC/TH freezes and full
acceptance receipts, fixed-control safe matrix, physical independence proof,
agent-owned closing judgment, checks and source/code identities. The final
read-only handoff copies safe summaries, plaintext logs, this round's diff
relative to `948ace849b877f4d49c6eaf53b81a64648894e9b`, and SHA256SUMS under the
existing `base22-review-afe2526` root. Raw replay prices and protected results are
not exported. TPlan records this Mission's completion, not the whole strategy's
acceptance.
