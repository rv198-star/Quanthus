# P4-REPORT-001 Daily Group Comparison Implementation Checkpoint

| Field | Value |
| --- | --- |
| Date | 2026-08-21 |
| Work item | `P4-REPORT-001` / Issue #76 |
| Status | implementation complete; production A/B report acceptance still required |
| Source corpus | accepted P4 development Event + Label corpus |
| Source event count | 36,820 |
| Source settled three-class count | 34,641 |

## 1. Pre-result policy freeze

The comparison policy was frozen before reading the real development report result. User authority
for this phase is to optimize the reproducible engineering chain rather than tune for investment
value. The exact approved policy is:

- id: `p4-event.daily-group-comparison@1.0.0`
- semantic digest:
  `sha256:4b7e6f931b06615940a9a72a5bd05f800be40f825716093e04637ab5fd8134a0`
- decision reference:
  `https://github.com/rv198-star/Quanthus/issues/76#issuecomment-5370987743`

No realized report result was used to choose feature ids, effect-size method, FDR threshold,
perturbation checks, or leading-feature ranking.

## 2. Frozen comparison matrix

The source population is the accepted settled three-class corpus only:

- `positive`: 4,519
- `neutral`: 4,159
- `failure`: 25,963

`not_executable`, `unavailable`, and `censored` remain explicit in report attrition and are not
coerced into a comparison group.

The report evaluates T through T-20. Seven dimensionless features reuse the accepted P3 baseline
feature semantics:

1. `price.price_to_sma20`
2. `price.sma20_to_sma60`
3. `price.momentum20`
4. `price.breakout20`
5. `price.rsi14`
6. `liquidity.volume_ratio20`
7. `risk.realized_volatility20`

The primary contrast is `positive_vs_neutral`; `positive_vs_failure` and
`neutral_vs_failure` remain contextual pairwise outputs.

## 3. Statistical contract

For each feature, lag, and outcome, the report emits count, mean, standard deviation, q25, median,
and q75. Pairwise rows emit:

- Hedges g effect size;
- mean difference;
- normal 95-percent confidence interval for the mean difference;
- two-sided normal-approximation p-value;
- Benjamini-Hochberg q-value, corrected independently across all 147 tests in each contrast.

Leading-feature selection is deterministic and predeclared. A feature must:

- have an FDR-significant `positive_vs_neutral` peak with at least 500 observations per group;
- have two consecutive same-direction FDR-significant lags for first reliable divergence;
- preserve the full-sample effect direction in both deterministic event-id hash halves, with at
  least 200 observations per group/half; and
- have a same-direction adjacent lag with q <= 0.10.

Survivors rank by absolute peak Hedges g, then stable feature id. Exactly the first three are the
leading features. This output is descriptive research evidence, not a strategy rule or promotion
claim.

## 4. Physical implementation

`src/quanthus/event_library/comparison_report.py` owns the report policy contract, statistics,
perturbation checks, leading-feature ranking, immutable package build, and independent verifier.
The package contains:

```text
comparison-report/
├── group-distributions.jsonl
├── pairwise-effects.jsonl
├── feature-ranking.jsonl
├── summary.json
├── manifest.json
└── READY
```

The report rebuilds `BaselineFeaturePanel` from the same admitted Direct-DWD development provider
and aligns every settled event at its signal T, then walks the same security's prior 20 market
sessions. A synthetic DuckDB test verifies the complete 7 × 21 × 3 group matrix and deterministic
hash-half perturbation matrix.

## 5. Worker and Run identity

No third P4 Worker or queue was introduced. The existing static Event Worker remains the only P4
producer. Its governed input adds only:

```text
report_policy = config/p4-event-comparison-report.research.v1.json
```

The worker invokes `run-p4-reported-event-scan`, which builds Event Library, Label Corpus, and
Comparison Report under one lease. Run manifest advances to `quanthus.p4-event-run.v3` and binds:

- Event manifest/content digests;
- Label manifest/content digests;
- Report manifest/content digests;
- all label counts;
- leading feature ids; and
- report acceptance flag.

Recovery succeeds only when all three independent verifiers pass and Event/Label/Report counts
reconcile.

## 6. Verification before production

Focused verification completed during implementation:

- report policy digest/tamper tests;
- Hedges g and BH-FDR tests;
- deterministic leading-feature and perturbation tests;
- synthetic DuckDB T..T-20 matrix alignment test;
- Event Worker v3 recovery/input/boundary tests;
- platform boundary test proving the Worker remains a control-plane supervisor.

Production acceptance remains deliberately separate. #76 may close only after two real governed
runs on the exact merged revision reproduce the same report content digest, report manifest SHA,
three leading feature ids, and `report_acceptance_pass=true`.
