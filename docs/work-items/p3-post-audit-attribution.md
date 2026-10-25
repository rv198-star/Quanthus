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
