# P3-STAGE-008-D2 Calibration Candidate Freeze

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-008-D / D2 |
| D1 Job | `stage_7ffc681cd5254aa0ab3e0d98026c42d7` |
| D1 revision | `071cdbf5cbe8fd1fa71bb042aca48a372193eea6` |
| D1 manifest | `sha256:2ddc4f90376e2061932862c2ef1a8cfd27f8774d1df9bdb59de8bf4e3bf1f9a3` |
| Calibration cases | 64 |
| Primary opinions | 128, same-origin `self_simulated` |
| Parameter trials | 27 exactly |
| Eligible candidates | 1 |
| Evaluation data read before freeze | false |
| Result | `CANDIDATE_FROZEN_FOR_EVALUATION` |

## 1. D1 boundary held

The unique D1 Job succeeded with a 512-identity fresh pool, a 256-identity calibration capacity and a
separate 256-identity evaluation capacity. It selected exactly 64 calibration cases with no quota
shortfall (`coverage=16`, `old_instability_seed=32`, `order_control=16`). It froze exactly 96
evaluation identities and quarter assignments, but `evaluation_data_read=false` and
`candidate_frozen=false` at D1 completion.

D1 Primary-only review transport exposed eight read-only pages. The transport contained the 20-session
primary Facts, the two new Directional Path Geometry Facts and the final 21 raw T-only closes. It
contained no explicit 10-session derived facts, candidate output or future rows.

## 2. Calibration review ledger

The 64 cases were reviewed under the already accepted two-role semantic model: `short_term` and
`fund`. Each role separately judged:

- `structural_instability_20`;
- `coherent_directional_relocation_20`;
- `orderly_consolidation_20`.

The raw ledger is `config/stage-learning/directional-boundary-calibration-review.v1.tsv`, SHA-256
`sha256:f147090c7d6f814e89b652570a89d9aa805b9a0ff34913ab258135ce9356c070`.

Provenance remains one ChatGPT session simulating two business responsibilities. Candidate outputs and
explicit recent derived Facts were not visible during review. Full cognitive blinding is not claimed.
Five cross-role primary disagreements are retained as `AMBIGUOUS`; no majority or adjudication was
used.

Resolved calibration references are:

```text
STAGE_0   28
STAGE_2   16
NEITHER   15
AMBIGUOUS  5
```

## 3. Frozen grid and result

The preregistered relocation guard grid contains exactly 27 candidates:

```text
abs(window_return_20) >= {0.05, 0.10, 0.15}
range_traversal_20    >= {0.40, 0.50, 0.60}
counter_excursion_20  <= {0.40, 0.50, 0.60}
```

No grid expansion, supplementation or Stage-007 threshold learning occurred.

The old R0 Stage-0 behavior on definite calibration references was:

```text
true positive     28
false positive     8
false negative     0
precision        0.7777777777777777777777777778
coverage         1.0
negative FPR     0.2580645161290322580645161290
```

Only one of the 27 relocation guards passed all frozen calibration gates. It is now frozen as:

```text
candidate_id = sha256:d11c368d4111ebf991315a6c61b800a6737d56e5f81792cf153dabcda7cb52fd
abs(window_return_20) >= 0.10
range_traversal_20    >= 0.50
counter_excursion_20  <= 0.60
```

Its calibration result is:

```text
true positive     28
false positive     3
false negative     0
precision        0.9032258064516129032258064516
coverage         1.0
negative FPR     0.09677419354838709677419354839
```

The full 27-trial ledger digest is
`sha256:b47919aec512ce39aa49989188e1d54bd1963182eb72c042670137fb458ca6bf`.
The frozen machine candidate contract is
`config/stage-learning/directional-boundary-candidate.v1.json`.

## 4. Evaluation boundary opened only after candidate freeze

D2 authorizes only the next preregistered step: D3 may read the exact 96 evaluation economic
identities and quarter assignments already sealed by D1. D3 evaluation date selection must be
morphology-blind and hash/quarter based. Evaluation opinions must be sealed before the frozen
candidate is scored.

D2 does not authorize:

- replacement or tuning of the frozen candidate after evaluation data is read;
- any additional candidate grid;
- Stage-2 threshold changes;
- 2022-2024 or 2025 access;
- future outcomes, official labels, strategy/daily use or trading.

Therefore D2 closes as `CANDIDATE_FROZEN_FOR_EVALUATION`, not as validation success.
