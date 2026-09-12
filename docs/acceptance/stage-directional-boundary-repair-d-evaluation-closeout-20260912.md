# P3-STAGE-008-D Fresh Evaluation Closeout

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-008-D |
| Final disposition | `REVISE_OR_PARK_EVALUATION_FAIL` |
| D1 Job | `stage_7ffc681cd5254aa0ab3e0d98026c42d7` |
| D3 Job | `stage_7617aafa1f3149c0a65935ef291fc521` |
| Frozen candidate | `sha256:d11c368d4111ebf991315a6c61b800a6737d56e5f81792cf153dabcda7cb52fd` |
| Evaluation result | `config/stage-learning/directional-boundary-evaluation-result.v1.json` |
| Evaluation result digest | `sha256:a23891da8e8f1b65aa12c0eeb90a0c20e4e416ac97542ee90ebfabac4ff71c94` |
| 2022-2024 access | none |
| 2025 access | none |
| Official labels / strategy / trading | not authorized |

## 1. D1 and D2 closure

D1 ran only after the preregistration and source-isolation contract had been durably committed. It
created a fresh 512-identity pool outside the complete Stage-007 pool and earlier excluded economic
identities, selected 64 calibration cases from 2020, and froze a separate 96-identity 2021 evaluation
plan without reading evaluation market data.

The 64 calibration cases were reviewed through a Primary-only transport by the two declared semantic
roles. The same-origin nature of those simulated opinions remains explicit. After sealing the 128 raw
opinions, the complete preregistered 27-point relocation grid was evaluated exactly once. Exactly one
candidate passed calibration gates:

```text
absolute_window_return20_min = 0.10
range_traversal20_min        = 0.50
counter_direction_excursion_share20_max = 0.60
```

On calibration, old R0 Stage-0 precision/FPR/coverage were approximately `0.778 / 0.258 / 1.000`;
the frozen candidate produced approximately `0.903 / 0.0968 / 1.000`. No evaluation data had been
read when the candidate was frozen. No grid expansion or supplementation occurred.

## 2. D3 evaluation preparation

D3 ran only after the candidate freeze was pushed. It read exactly the already frozen evaluation
identity plan and 2021 development data. Date selection was quarter-assigned and hash-only; morphology,
candidate output and reference judgements were not inputs to selection.

Of 96 planned evaluation identities, 95 produced an eligible Primary case and one produced a
shortfall. The frozen contract forbids identity or cross-quarter backfill, so the denominator remained
95. This still exceeded the preregistered minimum complete-case gate of 80.

D3 terminal evidence:

- Job: `stage_7617aafa1f3149c0a65935ef291fc521`;
- manifest: `sha256:a77bc2b439da05da5d10b7291c64caf5d680148c4c69126ba31ea39ac7a99827`;
- evaluation evidence: `sha256:912a11480d6e12a3dcc3862bcdb0900592623a419e5282e5bb2500e2ad5086da`;
- path Facts: `sha256:71294c12fb9bc8f74aa6fcd494e935016d680761e6d21d681d8a7b6776e148f0`;
- parameter search: 0;
- candidate scoring: false;
- opinion count: 0.

## 3. D4 Primary review and frozen scoring

The 95 evaluation cases were reviewed from the Primary-only transport. Explicit recent-derived facts,
candidate outputs and future rows were absent. The sealed evaluation ledger contains 190 same-origin
simulated Primary opinions and preserves 15 cross-role disagreements rather than majority-voting them
away.

Resolved Primary references were:

```text
STAGE_0   15
STAGE_2    8
NEITHER   57
AMBIGUOUS 15
```

The candidate was scored only after that ledger was sealed. There was no evaluation-time parameter
search or candidate replacement.

### Stage-0 result

| Metric | Old R0 | Frozen repair | Gate |
| --- | ---: | ---: | ---: |
| definite positives | 15 | 15 | >=12 |
| definite negatives | 65 | 65 | >=30 |
| Stage-0 predictions | 38 | 26 | >=10 |
| true positives | 15 | 15 | — |
| false positives | 23 | 11 | — |
| precision | 0.3947 | **0.5769** | **>=0.80 FAIL** |
| positive-reference coverage | 1.0000 | **1.0000** | >=0.55 PASS |
| definite-negative FPR | 0.3538 | **0.1692** | **<=0.10 FAIL** |

The repair is therefore materially better than old R0 but does not pass the frozen absolute quality
gates. The final business disposition is `REVISE_OR_PARK_EVALUATION_FAIL`.

## 4. Failure attribution

The remaining eleven false positives are not one homogeneous threshold error.

### 4.1 Eight cases expose an incomplete instability repair

Eight false positives are cases 30, 44, 46, 50, 53, 67, 76 and 93 in the sealed evaluation order.
They enter old R0 through the center-shift arm while the current range is contracting or does not show
positive expansion. This means the implementation still effectively permits **center shift alone** to
assert Stage 0.

That directly conflicts with the approved P3-STAGE-008-A boundary:

> `center_shift_alone_cannot_assert_stage_0`

The relocation guard cannot repair this class because it is not fundamentally a relocation-threshold
problem. The positive structural-instability proposition itself remains under-specified in executable
form.

### 4.2 Three cases expose relocation-boundary generalization

The other three false positives are cases 17, 29 and 68. Their path geometry is directionally coherent,
but their absolute 20-session return is approximately 8.38%-9.78%, just below the calibration-frozen
10% relocation threshold. Lowering that threshold after seeing evaluation would be evaluation-set
fitting and is explicitly forbidden.

Therefore these observations are retained as attribution evidence only. No 8%, 9% or other replacement
threshold is selected in this milestone.

## 5. Decision and claim ceiling

P3-STAGE-008 achieved three useful results:

1. the Path Geometry Facts and Primary-first reference protocol are implementable and deterministic;
2. the relocation guard substantially reduces the old directional false-positive problem while
   preserving all 15 definite Stage-0 positives in this evaluation;
3. fresh evaluation reveals that the repair is **incomplete** because the inherited instability
   predicate still violates the approved center-shift boundary.

Accordingly:

```text
P3-STAGE-008-A: CLOSED
P3-STAGE-008-B: CLOSED / engineering accepted only
P3-STAGE-008-C: CLOSED / diagnostic conformance pass only
P3-STAGE-008-D: CLOSED / REVISE_OR_PARK_EVALUATION_FAIL
P3-STAGE-008:   CLOSED / repair not qualified
```

No additional trial, automatic supplement or hidden rerun is authorized. Directional Path Geometry
remains `implemented_unqualified` and is not added to accepted Structure runtime bindings. Stage 2 is
unchanged. No official Stage label, daily/strategy consumer or trading authority is opened.
