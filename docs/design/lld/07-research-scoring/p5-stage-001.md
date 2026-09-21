# P5-STAGE-001 — Learned Stage Assessment Dual-Track Research

| Field | Value |
| --- | --- |
| Phase | P5 |
| Parent issue | #400 |
| Active gate | P5-STAGE-001-A / #401 |
| Design status | frozen for A |
| Request | `config/research/p5-stage-001/request.v1.json` |
| Target set | `config/research/p5-stage-001/stage-targets.v1.json` |
| Relationship to P3 Stage phase 1 | research-target reuse only; official Stage qualification remains closed |
| Relationship to P6 | parallel and independent; no mutation or predecessor relationship |

## 1. Research decision

P5-STAGE-001 tests whether a small learned temporal representation can provide useful Stage-proposition
assessment beyond simple learned baselines and the existing rule surface, while keeping the accepted
Continuous Facts substrate authoritative.

This is not a new primary-stage state machine. The historical codes `1..12` are a target vocabulary
with different semantic layers and evidence grades. Multiple learned scores may coexist. Code `0`,
`ABSTAIN`, and `X` remain separate market/context, epistemic, and control meanings.

The concrete downstream consumer is the existing P5 Research Scoring
`proposal-to-rule-shadow` lifecycle review defined by the parent LLD. Its accountable decision owner
is the Quanthus owner acting as human research lifecycle reviewer. That review already consumes
authoritative Continuous Facts, Layered Stage propositions, and governed rule/projection results where
available. The unmet decision is whether a learned per-proposition support distribution adds
reference-supported recognition or calibrated disagreement that justifies further rule engineering,
retaining a learned route, or closing it.

The consumer receives only a read-only R/L/G/T0/T1 scorecard, per-head `k/12` readiness,
calibration status, and route-disagreement report. No model output changes an expert
`PolicyEffect`, veto, constraint, strategy, P6 decision, or trade.

## 2. Why this narrow line may reopen

The accepted Stage phase-1 closeout intentionally parked further label-completion work. It also
allows a bounded successor when a concrete downstream need exposes a gap that cannot be addressed by
the existing Facts plus Layered Proposition surface.

The gap tested here is not "we need all twelve labels." It is narrower:

- existing Continuous Facts remain useful and authoritative;
- existing Stage propositions have heterogeneous maturity and many deterministic rule boundaries
  remain parked or expensive to complete;
- the existing P5 proposal-to-rule-shadow lifecycle has no governed learned, soft, multi-label
  assessment route that can be compared with the rule route under the same semantics and reference
  standard;
- that lifecycle therefore cannot currently decide whether parked/inconclusive recognition boundaries
  warrant more deterministic rule engineering, a learned route, or no further Stage work.

The bounded value question is whether a fixed learned route adds reference-supported assessment value
or calibrated disagreement over simple learned baselines and the existing rule surface **for that
specific lifecycle decision**. A no-increment or data-limited result closes honestly. The gap is not
used to claim that Continuous Facts are insufficient as the factual substrate.

## 3. Authority boundary

Owner direction on 2026-09-22 authorizes execution of A: docs, configuration, contract tests, and
synthetic contract cases. It does **not** authorize a market-row read or model training in A.

The request freezes the candidate successor scope so later issues cannot silently widen it:

- successor B may consider only exact governed 2012-2021 SSE/SZSE development inputs after explicit
  issue activation and after it freezes and verifies an exact manifest;
- B's candidate source families are daily security bars, tradability, trading calendar, accepted
  Continuous Structure Facts, and published Stage reference artifacts;
- 2011 warm-up is not authorized by A and requires a separate pre-result authority update if B proves
  it is necessary;
- 2022-2024, 2025, and 2026 remain closed to this line;
- P3-STRAT-003 data authority is not inherited;
- C and D also require explicit issue activation and may not be inferred from A completion.

A completion therefore means **preregistration accepted**, not "training started" or "Stage reopened
for official qualification."

## 4. Target semantics

The machine target set is the source of truth for per-head semantics. Every head declares:

- historical code and proposition id;
- semantic layer and inherited evidence grade;
- marginal or conditional probability population;
- `anchor_time`, `feature_cutoff`, and `label_observation_end`;
- parent episode identity and parent applicability where needed;
- retrospective-vs-as-known evidence grade;
- reference eligibility and initial trainability disposition;
- positive, negative, ambiguous, and unjudgeable semantic examples;
- T-visible evidence requirements, rule-versus-model target distinction, and explicit relationship
  declarations;
- a predeclared severe-error slice.

All targets are T-only: the observation to be classified is anchored after T close, features stop at
T close, and the label semantics may not use future rows to confirm what was true at T.

Parentless marginal heads share one A-frozen, outcome-blind applicability population:
`BASE_ACTIVE_RESEARCH_ANCHOR_V1`. It is the exact B-bound 2012-2021 SSE/SZSE development manifest
at unique `(security_id, T_market_date)` anchors with ordinary inference control state ACTIVE. Labels,
tested-rule outputs, model results, future outcomes and present-day sector membership cannot determine
membership. B may resolve the exact source manifest and counts but may not redefine this semantic
population. Missing target-specific T-only facts make a marginal head `UNAVAILABLE`, not
`NOT_APPLICABLE`.

Historical evidence may remain retrospective T-only until B proves an as-known historical route. The
system must not silently claim that a retrospective reconstruction was available in real time.

### 4.1 Nested probabilities

Conditional heads are probabilities only inside their frozen applicability population. A parent
failure or unavailable parent does not become a negative child label.

For Stage11 the v1 contract is exactly:

```text
P(Stage11 post-break rebound | Stage10 structural-failure context qualified at T)
```

When Stage10 is known not applicable, Stage11 has `applicability=false`; when the required
Stage10 context is unavailable or unknown, Stage11 has `applicability=null`. In both cases
`calibrated_probability=null`. The decision state distinguishes `NOT_APPLICABLE` from `ABSTAIN`;
the shared encoder may retain a diagnostic raw score, but that value is not a usable Stage11
probability.

The same principle applies to the other conditional/nested cards. Stage4 is conditioned on valid
Stage3 episode provenance. Stage1 and Stage6 require their declared parent contexts. Stage7 remains
**ineligible with null learned outputs** until a separately governed T-only repair-context predicate is
frozen; A intentionally does not invent that context. Stage8 likewise remains ineligible until a
separately governed T-only **high-side applicability predicate** exists: Stage5 advancing context alone
does not define "high-side." Stage9 remains ineligible while the required governed activity relation
is missing.

### 4.2 Operational output and abstention

Each per-head output carries applicability, raw score, calibrated probability, decision state,
reason codes, and exact model/input/calibrator identity. The decision states are
`ASSERT_POSITIVE`, `ASSERT_NEGATIVE`, `ABSTAIN`, `NOT_APPLICABLE`, `UNAVAILABLE`, and `X`.

A calibrated probability at or above 0.70 creates a positive assertion; at or below 0.30 it creates a
negative assertion; the middle interval abstains. An applicable head with a raw score but insufficient
calibration also abstains while retaining the diagnostic raw score. An ineligible/untrained head is
unavailable with null raw/probability outputs. System-control `X` remains separate and dominant.

The 80% primary coverage gate is **raw-score coverage over reference-eligible, target-applicable,
definite cases**. Parent-inapplicable/unknown cases are excluded by the frozen target population, not
by a model choice. An abstained case with a finite raw score remains in the coverage numerator and,
when a calibrated probability exists, remains in AP/Brier evaluation. Assertion coverage is reported
only as a diagnostic.

## 5. Reference truth and readiness

Labels retain five states: `positive`, `negative`, `ambiguous`, `unjudgeable`, and
`unreviewed`. The latter three are not silently turned into negatives.

A confirmatory reference must:

1. be blind to the tested model/rule output;
2. not be derived solely from the tested rule;
3. have a traceable adjudication source and T-only evidence packet;
4. satisfy the target's parent/applicability contract.

Rule-derived silver labels and model suggestions may support training or exploration but cannot be the
sole confirmatory truth.

Before formal labeling, B must publish a per-head readiness matrix. The 1500 unique-episode cap is a
budget ceiling, not a promise that all twelve heads qualify. Heads that lack capacity remain null
without lowering gates or opening protected years.

## 6. Fixed model ladder and attribution

The bounded ladder is:

| ID | Role |
| --- | --- |
| R | Existing governed rule/projection where one exists; missing rules are not manufactured |
| L | Fixed regularized logistic baseline |
| G | Fixed small GBDT baseline |
| T0 | Fixed small sequence encoder trained directly on supervised targets |
| T1 | The same encoder after masked self-supervised pretraining, then supervised training |

This is a narrow exception to the general P5 "GBDT plateau before sequence model" ladder. The
exception exists only to answer this preregistered comparison with one small architecture. It does
not authorize generic sequence-model search, a model platform, or automatic promotion.

The fixed contrasts are:

- `T0 - B*`: preregistered **component diagnostic** for sequence-architecture increment;
- `T1 - T0`: preregistered **component diagnostic** for self-supervised-pretraining increment;
- `T1 - B*`: the **sole line-level confirmatory contrast** for complete learned-route increment.

The two component diagnostics report the same frozen metrics/intervals but cannot independently create
the line-level `SUPPORTED_INCREMENT` disposition. This removes cross-contrast multiplicity from the
line-level claim. If T1 is not admitted under the frozen compute budget, the line is
`COMPUTE_LIMITED`; the T0 component result may still be reported descriptively.

`B*` is selected once between L and G on the B-frozen common head set using
development-validation **macro average precision, maximizing the metric**. A tie within
`1e-12` selects L for simplicity. If exactly one baseline execution is valid it becomes B*; if
neither is valid the confirmatory comparison is inconclusive. The selection is then locked.
Rule/model agreement is diagnostic only and never becomes an automatic confidence multiplier.

## 7. Seed, calibration, and statistics

T0 and T1 use downstream seeds 17, 29, and 43. The confirmatory raw prediction is the arithmetic mean
of the three raw sigmoid scores for the same sample and head. Single-seed results are stability
diagnostics only. No best-seed selection is allowed.

If a required seed is unavailable after the preregistered technical-recovery allowance is consumed,
the confirmatory model result is inconclusive; a replacement seed is not introduced.

The first calibration method is per-head Platt scaling fitted only on the calibration partition.
L and G calibrate their own raw scores; T0 and T1 calibrate the three-seed mean raw score. B* uses the
calibrator of whichever underlying baseline was selected. The hard rule route R is not silently
upgraded to a probability. A calibrated probability is published only when the head has sufficient
calibration capacity and effective time blocks.

The confirmatory evaluation uses one exact B-frozen head set for AP, Brier and severe-error
statistics. No head may be dropped after results. AP is computed per head and equal-weight macro
averaged. Brier and severe-error are paired within the same reference case, averaged within each head,
then equal-weight macro averaged across the same complete head set. A missing required metric for any
frozen head makes the line-level statistic `INCONCLUSIVE`.

Severe-error uses one machine-defined slice for every head:
`ALL_REFERENCE_ELIGIBLE_DEFINITE_APPLICABLE_CASES`. Historical descriptive slice names remain
diagnostic hints only; they cannot change the confirmatory denominator.

The confirmatory evaluation further freezes:

- AP increment requires the paired-difference lower bound to be above zero;
- Brier non-inferiority uses signed difference `candidate - comparator`, tolerance 0.01, and
  requires the paired-difference upper bound to be at or below 0.01;
- severe-error non-inferiority uses signed difference
  `candidate severe-error rate - comparator severe-error rate`, tolerance two percentage points,
  and requires the paired-difference upper bound to be at or below 0.02;
- per-head results are descriptive diagnostics only: no per-head confirmatory p-values or Holm family
  exists; the sole confirmatory claim remains `T1-B*`.

The line-level uncertainty protocol is `paired-calendar-block-bootstrap-v1`. B freezes ascending
evaluation dates before results, partitioned from the earliest date into consecutive non-overlapping
22-session blocks; a trailing 1-21 session remainder is appended to the preceding block. There must be
at least eight constructed blocks and **every** constructed block must contain a paired metric-eligible
case for every frozen confirmatory head; no failing block is dropped. Otherwise interval evidence is
`INCONCLUSIVE`.

Bootstrap block draws are deterministic: candidate replicate `r` / draw `j` uses SHA256 over the
frozen request/contrast/protocol seed material plus `r:j`, interprets the first eight digest bytes as
an unsigned big-endian integer, and reduces modulo block count. Candidate/comparator and all heads use
the same sampled block indices. Candidate replicate ids 0..3999 are scanned in order and the first 2000
shared-valid replicates are retained; fewer than 2000 is `INCONCLUSIVE`. An AP replicate is invalid
when any frozen head lacks either reference class. The 95% interval is the empirical nearest-rank
interval over exactly 2000 sorted valid replicates: ranks 50 and 1950 (zero-based 49 and 1949), with no
interpolation.

The sole line-level confirmatory contrast `T1-B*` is mapped through an exhaustive ordered
decision table. First matching rule wins:

1. protocol leakage/binding/protected-boundary invalidity -> `INVALID_EVIDENCE`;
2. T1 not admitted under the frozen compute budget -> `COMPUTE_LIMITED`;
3. missing confirmatory reference/head, unavailable/invalid B*, missing required seed/effective
   blocks, or unresolved preregistered precision -> `INCONCLUSIVE`;
4. either candidate or comparator raw-score coverage below 80% -> `NO_INCREMENT / LOW_COVERAGE`;
5. resolved Brier upper bound above 0.01 or severe-error upper bound above 0.02 ->
   `NO_INCREMENT / RESOLVED_NONINFERIORITY_FAILURE`;
6. AP lower bound at or below zero -> `NO_INCREMENT / AP_INCREMENT_NOT_SUPPORTED`;
7. otherwise, valid protocol/reference evidence plus all coverage/block/AP/Brier/severe gates ->
   `SUPPORTED_INCREMENT`.

The order makes resolved harm/low coverage deterministic rather than leaving them between
`NO_INCREMENT` and `INCONCLUSIVE`. `T0-B*` and `T1-T0` receive only component-diagnostic
labels and cannot override the sole confirmatory disposition. Per-head results are descriptive only,
so no per-head confirmatory multiplicity procedure applies. No line-level multiplicity correction is
needed because there is exactly one confirmatory contrast.

## 8. Compute boundary

A freezes only the budget and decision rules. It does not select a live backend.

- total budget: at most 24 node-hours;
- one worker at a time;
- at most 20 GiB new artifacts/cache;
- at most 12 GiB GPU working set;
- at most 24 GiB host RAM and never above 50% of currently available RAM.

C must validate a full forward/backward/optimizer/checkpoint path. GPU use requires correctness,
recovery, no undeclared CPU fallback, resource-budget compliance, and positive end-to-end value.
A median speedup of at least 2x is a preference, not the only hard gate. C must freeze any rule that
allows a sub-2x GPU choice before it sees benchmark results.

L/G/T0 remain useful even if T1 cannot fit the frozen compute budget. T1 then closes as
`COMPUTE_LIMITED`, not as empirical `NO_INCREMENT`.

## 9. Sequence and stop rules

The issue sequence remains:

```text
A -> (B, C, D) -> E -> F -> G -> H
```

A does not auto-start B/C/D.

Stop instead of repairing in place if an issue would require protected 2022-2026 access, future rows
to confirm T state, current sector membership as historical context, unknown-as-negative relabeling,
result-driven head selection, gate lowering, replacement seeds after results, broad hyperparameter
search, P3-STRAT/P6 mutation, or production/trading authority.

If an upstream issue ends with a legal stop, downstream issues that can no longer execute are closed
in dependency order with an explicit `NOT_RUN_<reason>` or equivalent evidence disposition before H
performs closeout.

## 10. Claim ceiling

P5-STAGE-001-A freezes a research-only comparison protocol. It does not prove a learned model works,
does not qualify any Stage code, does not publish an official LabelCard, and does not activate
training, daily inference, expert policy, strategy, P6, broker, live capital, or trading.
