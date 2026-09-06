# P3-STRUCT-002 Structure Intelligence economic-value final acceptance (2026-09-06)

| Field | Value |
| --- | --- |
| Status | accepted / closed |
| Work item | `P3-STRUCT-002` |
| Frozen request | `config/structure-economic-run.development.v1.json` |
| Request digest | `sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de` |
| Final same-revision code | `f77a69e9c3307c0af9d9bd74b6f9bdf7b79fd0a5` |
| Final Run A | `job_79164324230389efd5dbcf1be5a63471` |
| Final Run B | `job_278e1b72234c6397b803bc364d0d5eb2` |
| Result digest A/B | `sha256:460d38cf0d183c15513d2aa9fc74a768ec1dc0c2fa0efb4705fd9871a7e15855` |
| Manifest SHA A/B | `sha256:39e4f403ddd9ae514473759a491dda8e1665a7bc1f1d0b34f172602a34b2d836` |
| Structure ADM manifest A/B | `sha256:308f1706338e25d45b8497e316dab844cd44f75b35f0a0433b497644452559c0` |
| Lifecycle disposition | `prefer_continuous_structure_facts` |
| Sealed partition access | false |
| Execution authority | false |

## 1. Frozen question and population

The accepted experiment is exactly the preregistered development-only comparison from
`structure-intelligence-economic-preregistration-20260906.md`. It reuses
`candidate.p4-trend-risk@1.0.1`, the accepted 2020-2021 formal candidate population, Direct-DWD
Base V2.1 plus correction-001, five fixed capital units, the existing position-risk/held-review
path, concentration/allocation, corporate actions, costs, settlement, cooldown, and the accepted P2
matcher.

Only entry-scoring representation changes:

- C0 `variant.native-baseline`: accepted native formal strategy;
- C1 `variant.structure-continuous`: native strategy plus frozen continuous structure scoring;
- C2 `variant.structure-coarse`: native strategy plus the coarse-state compression of the same
  structural information and episode memory.

Primary comparison is C2-C1. Secondary comparisons are C1-C0 and C2-C0. No date, candidate,
threshold, score mapping, materiality rule, bootstrap method, or sealed-partition rule was changed
in response to economic results.

## 2. Baseline reproduction

Both final A/B Runs reproduce the already accepted native complete-P2 baseline exactly:

| Variant | Net total return | Maximum drawdown |
| --- | ---: | ---: |
| C0 native | `0.06704618` | `0.07102827` |
| C1 continuous | `0.06433900` | `0.06168277` |
| C2 coarse | `0.05718376` | `0.07360706` |

The C0 replay, decision, daily-return, reconciliation, equity, engine-event, and final-ledger
artifacts are byte-identical across A/B and retain the accepted native identities.

## 3. Preregistered comparisons

### C1-C0

- return delta: `-0.00270718`;
- maximum-drawdown delta: `-0.00934550`;
- tail-loss delta: `+0.00033030`;
- stable entry-surface final-action change count: `2`;
- action-change rate: `0.0002165908598657136668832575265`;
- preregistered materiality: **false**.

C1 therefore shows a lower-drawdown direction in this frozen development simulator, but the
improvement is below the preregistered absolute one-percentage-point drawdown threshold. The paired
22-session bootstrap interval crosses zero, so this acceptance does not claim statistically proven
forward improvement.

### C2-C0

- return delta: `-0.00986242`;
- maximum-drawdown delta: `+0.00257879`;
- tail-loss delta: `+0.00046140`;
- stable entry-surface final-action change count: `2`;
- action-change rate: `0.0002165908598657136668832575265`;
- preregistered materiality: **false**.

### C2-C1 — primary

- return delta: `-0.00715524`;
- maximum-drawdown delta: `+0.01192429`;
- tail-loss delta: `+0.00013110`;
- stable entry-surface final-action change count: `0`;
- action-change rate: `0`;
- preregistered materiality: **true**, because the maximum-drawdown difference exceeds `0.01`.

C2 has lower return and higher maximum drawdown than C1 under the frozen comparison. The primary
comparison therefore maps to the preregistered disposition
`prefer_continuous_structure_facts`.

The zero C2-C1 entry final-action change count does not imply equal portfolio paths. C1 and C2 emit
different aggregate scores/rankings, so finite capital allocation can choose different simultaneous
opportunities; later holdings, exits, cooldown, and capital availability then diverge. Complete-P2
portfolio metrics remain the authority for those downstream differences.

## 4. Independent reproduction

The final owner-authorized A/B pair ran on exactly the same code revision and request.

A and B reproduce exactly:

- semantic result digest;
- package manifest SHA;
- Structure ADM manifest/content identity;
- all three variant metrics;
- all three comparison results and bootstrap evidence;
- each variant replay digest;
- decision, opportunity, daily-return, reconciliation, engine-event, equity, and final-ledger file
  identities; and
- lifecycle disposition `prefer_continuous_structure_facts`.

This satisfies the independent same-revision reproduction gate for `P3-STRUCT-002`.

## 5. Defect and repair history

Two implementation defects were found during qualification and are retained as explicit evidence,
not hidden from the final result.

1. **Pre-result comparison pairing defect.** The first attempt incorrectly required the entire
   downstream portfolio decision-key set to remain identical across variants. Because an entry
   intervention legitimately changes later held/exit paths, the run failed before publishing any
   economic result. The repair constrained action-change pairing to the stable shared entry/event
   surface while leaving complete-P2 economics responsible for downstream path effects. Evidence:
   `structure-intelligence-economic-pre-result-repair-20260906.md`.
2. **Post-result derived lifecycle defect.** A later complete Run published correct economic
   comparisons but evaluated secondary materiality before the preregistered primary C2-C1 question,
   producing an inconsistent derived lifecycle label. The sealed economics were retained unchanged;
   only lifecycle precedence was repaired to match the frozen primary-first rule. Evidence:
   `structure-intelligence-economic-derived-lifecycle-repair-20260906.md`.

The final A/B pair independently reproduces the repaired economic calculation and lifecycle.

## 6. Accepted decision and claim ceiling

The accepted decision is:

```text
P3-STRUCT-002: CLOSED
Economic development comparison: PASS
Independent same-revision reproduction: PASS
Disposition: prefer_continuous_structure_facts
Continuous representation proven economically superior in forward/live markets: NO
C1 proven materially better than C0: NO
Bootstrap directional significance: NO
Thirteen-state expansion: NOT AUTHORIZED
Model training: NOT AUTHORIZED
Shadow / paper / live: NOT AUTHORIZED
```

This evidence justifies investing the next engineering milestone in an auditable continuous
structure-fact layer rather than making coarse states the primary computational representation. It
does not prove that the current C1 mapping is optimal, profitable in forward markets, or ready for
production trading.

The owner subsequently approved `P3-STRUCT-003 / Continuous Structure Facts v1` with price-only
breakout episode creation, independent volume facts, and coarse states retained only as an
explanation projection.
