# P3-STAGE-029 Real-History Stage Proposition Bundle Replay Acceptance

Design status: approved evidence record  
Delivery gate: accepted-conditional

Work item: `P3-STAGE-029`  
Protocol: `../../config/stage-learning/stage-proposition-bundle-real-data-replay.v1.json`  
Machine result: `../../config/stage-learning/stage-proposition-bundle-real-data-replay-result.v1.json`

## Scope

P029 tested one bounded question after the P028 Stage Proposition Bundle MVP: when the accepted
Stage11 rebound condition is true inside the accepted Stage10 structural-failure context, does a
layered development-shadow representation retain both propositions explicitly on real historical
observations?

The replay consumed only the two previously accepted immutable 2020Q1 Support Evolution/F9
artifacts. It did not open a DWD provider, read new market data, read future outcomes, touch the
consumed 2022-2024 arbitration partition, or touch preserved 2025 data.

The source record view is explicitly **retrospective**. This acceptance is therefore about real-history
representation and consumer semantics, not strict PIT/as-known availability, prediction, economics,
out-of-sample performance, strategy or trading.

## Frozen execution

- code revision: `2f3785a241703d8c86f3d7729bb0e6a08ef68fec`
- Job: `job_c7f1203e5d8c4dc6a6e5884858bf8e72`
- Run: `run_c7f1203e5d8c4dc6a6e5884858bf8e72`
- manifest: `sha256:0afdf451286bdd26d70f56ad6ba745eb160c9e9ebafa9779b4a645355112f0c4`
- result: `sha256:d95bc3dd3ea3cd4a0541b5948e3499278d14d5b5ce91d1fd73738bc2212adb77`
- source Support Facts: `sha256:6abbd976591739ede4451fd15ad2a11e8dd98519cc8039c2374f575c0035aff8`
- formal Stage10/11 projection: `sha256:47797766b987d7886de2be86587c7cbd4d7af8d9551d4dc38cd96eb3c6f8a221`

The artifact passed a second deep reconstruction from the accepted immutable sources before the Job
could close.

## Result

The frozen population contains all ten real observations in which the formal Stage10 context is
active: four Stage10-only rows and six rows where Stage10 and Stage11 are both true. There was no
sampling.

Across those ten observation identities there are sixteen explicit target propositions: ten Stage10
contexts plus six nested Stage11 conditions.

| Representation | Explicit target propositions retained | Stage10 parent elided on the six nested rows |
| --- | ---: | ---: |
| Facts-only | 0 Stage propositions by design | not applicable |
| comparison-only single code (`11` else `10`) | 10 / 16 | 6 / 6 |
| P028 layered development-shadow Bundle | 16 / 16 | 0 / 6 |

The layered view had zero development-shadow semantic mismatches and zero non-null `primary_stage`
values. The accepted result is therefore:

`PASS_LAYERED_REPRESENTATION_PRESERVES_EXPLICIT_PARENT_AND_NESTED_SEMANTICS`.

This is an **explicit representation** result. A consumer that already knows and correctly applies
`Stage11 => Stage10 context` can in principle recover the parent relation from the comparison code;
P029 does not claim an information-theoretic gain.

## Concrete real-history example

For the same `000001`, scale-20 support process:

- 2020-02-10: unreclaimed age `5`, signed support distance about `-4.9180%`, post-break rebound about
  `3.6455%`; comparison code `10`; layered view `[10]`.
- 2020-02-11: unreclaimed age `6`, signed support distance about `-3.0164%`, rebound about `5.7184%`;
  comparison code `11`; layered view `[10, 11]`.

The second observation makes the product difference concrete: the current rebound condition becomes
true while the structural-failure parent remains active. The layered view keeps both facts explicit;
the comparison-only single code replaces the visible `10` with `11`.

## Runtime boundaries

- market data reads: `0`
- DWD provider opens: `0`
- future-outcome reads: `0`
- 2022-2024 consumed partition reads: `0`
- preserved 2025 reads: `0`
- production expert authority: `false`
- strategy/trading authority: `false`

The strict PIT gate remains:

`BLOCKED_BY_RETROSPECTIVE_SOURCE_RECORD_VIEW`.

## Acceptance conclusion

P029 closes the gap between a synthetic P028 schema demonstration and a real-history,
deterministically rebuildable development-shadow data product for accepted Stage10/11 semantics.
It does **not** establish that the propositions were available as-known at the historical decision
time, nor that layered representation improves human/model decisions relative to continuous Facts.

Four independent product-effect reviews all selected strict PIT/as-known Stage10/11 replay as the
single next validation priority. That recommendation is recorded for phase review only; P029 does
not automatically open a successor.
