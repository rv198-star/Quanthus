# P3 Governed Strategy Development Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | accepted; independent full invocation reproduced all retained artifacts byte-identically |
| Evidence date | 2026-07-29 |
| Candidate | `candidate.market-breadth-trend-breakout@1.0.0` |
| Decision scope | 2012-01-04 through 2021-12-30; final execution/mark 2021-12-31 |
| Governing strategy | `docs/design/strategies/market-breadth-trend-breakout-v1.md` |
| Governing config | `config/p3-governed-strategy.research.v1.json` |
| Governing work | `P3-LABEL-002`, `P3-EXP-002`, `P3-AGG-002`, `P3-POLICY-001`, `P3-POS-002/003`, `P3-ACCEPT-001` |

## 1. Decision

The frozen initial development run passed every predeclared candidate performance gate and its
infrastructure evidence reconciles. An independent full invocation reproduced the feature panel,
result, and scoreboard byte-identically. Candidate v1 is accepted for its bounded development
delivery and remains research-only.

This record keeps three conclusions separate:

- infrastructure and conformance cover PIT decisions, accepted P2 execution, lifecycle/event
  reconciliation, deterministic artifacts, and the development-partition boundary;
- candidate performance acceptance covers only the three predeclared selection/timing gates and
  the maximum-drawdown warning;
- full-capital CSI 300 is the opportunity-cost benchmark and materially outperformed the
  candidate. Capital-matched excess does not replace that conclusion.

Nothing here accepts P3 as a whole, opens 2022-2025 data, authorizes notification or live use, or
promotes candidate v1 beyond development research.

## 2. Frozen Inputs And Boundary

| Input | Binding |
| --- | --- |
| Strategy digest | `sha256:2d15e1b0c160efc70bf1d42783a13ea2463e0ed28ae43e920a98c599652abf6b` |
| P1 replay snapshot | `p1-historical-replay-20260729-v1`; `sha256:e72976a2067dcbc7ba6bafdae48a1510b2019f338178eedd80f31da850a51707` |
| Feature-panel report | `sha256:dfec044daf87a4df1d36e989893573da37268545171acdcd340b0806157eedf9` |
| CSI 300 curve | `sh000300`; `sha256:55a04023c356d539febe1ca3b851af6c4e2d1c21ee09e0371ba3225033ecb146` |
| Static industry artifact | `static-level1-concentration-research-v1`; `sha256:8a3ce0af78706039d5423740378d102ef9b17eec05ec5ceada679143104eb45a` |

The industry artifact uses the Sina Finance Shenwan company profile fallback after the primary
Eastmoney endpoint repeatedly returned HTTP 502. It covers all 1,687 required `security_id`
values with zero failures across 28 level-one groups. The snapshot's existing identity/code
relationship was used to obtain source membership; no economic identity was constructed from an
exchange and stock code. The mapping is static, non-PIT, and permitted only for concentration.

The result contains no decision, event, or equity point from 2022 or later. The last decision is
2021-12-30 and its declared T+1 execution/mark is 2021-12-31. Arbitration and permanent-blind
partitions remained unopened.

## 3. Initial Development Result

| Metric | Result |
| --- | ---: |
| Closed trades | 230 |
| Operations | 461 (231 buys / 230 sells) |
| Total return | +31.909470% |
| CSI 300 full-capital return | +114.915498% |
| Full-capital excess | -83.006028% |
| Capital-matched CSI 300 return | +9.420465% |
| Capital-matched excess | +22.489005% |
| Return excluding top 3 closed trades | +19.109432% |
| Maximum drawdown | 4.746455% |
| Win rate | 37.391304% |
| Payoff ratio | 3.73373838 |
| Profit factor | 2.22987153 |
| Average invested capital | 6.875168% |
| Time in market | 31.761317% |
| Fees | CNY 6,260.45 |
| Sharpe ratio | 0.86553862 |

The low win rate is paired with larger average wins, but the candidate used little portfolio
capital. It passes its declared selection/timing checks while leaving substantial full-capital
opportunity cost. This is not evidence that capital deployment is solved.

## 4. Predeclared Gate Evaluation

| Gate | Actual | Required | Result |
| --- | ---: | ---: | --- |
| Closed trades | 230 | `>=100` | pass |
| Capital-matched excess | +22.489005% | `>=0%` | pass |
| Return excluding top 3 | +19.109432% | `>=0%` | pass |
| Maximum drawdown warning | 4.746455% | `<=25%` | pass |

Full-capital excess is intentionally a warning/interpretation metric rather than the frozen
candidate gate. The -83.006028% result remains visible and cannot be replaced by the
capital-matched comparison.

## 5. Audit Reconciliation

| Evidence | Count or result |
| --- | ---: |
| Candidate/held decisions | 52,423 |
| Accepted / rejected decisions | 461 / 51,962 |
| P2 engine events | 7,344 |
| Lifecycle transitions | 947 |
| Funnel source events | 101,549; fully reconciled |
| Candidate/score inputs | 48,591 |
| Entry-allocation inputs | 535 |
| Held reviews | 3,832 |
| Carried exits | 26 |
| Settlements / cooldown completions | 230 / 230 |
| Final ledger equity | CNY 659,547.35; exact report reconciliation |

Four final lifecycles are Empty and one is a legal Trial. That open position explains 231 buys
versus 230 sells and 230 closed trades; it is not an invented close or a ledger defect. Twenty
held-review funnel outcomes remain explicitly pending for that opportunity and still reconcile.

A maximum-hold trigger is evaluated at the 40th positioned T close and may first fill at the next
open, so those closed trades report 41 holding sessions. This is the declared T+1 clock rather
than an off-by-one correction.

## 6. Reproduction And Artifact Bindings

| Artifact | Initial run SHA-256 | Independent rerun SHA-256 | Status |
| --- | --- | --- | --- |
| Feature panel Parquet | `cb10ee9a98a620c496da6a6683f48f930c7ce6707b9ed5d74736a4a1174c8039` | `cb10ee9a98a620c496da6a6683f48f930c7ce6707b9ed5d74736a4a1174c8039` | byte-identical |
| Result JSON | `d64f34179a275c17e5095b8a33036663cfe61299c211050d863ae1bb6e7f1f23` | `d64f34179a275c17e5095b8a33036663cfe61299c211050d863ae1bb6e7f1f23` | byte-identical |
| Scoreboard Markdown | `53dc8030c0e0dcaaa514edb77ea9fdd59bd59c5de7e8c2eac83bd75451e5c552` | `53dc8030c0e0dcaaa514edb77ea9fdd59bd59c5de7e8c2eac83bd75451e5c552` | byte-identical |

Both result files therefore carry the same canonical digest,
`sha256:c5d718e84588ecfddb20fc2bd7d1d847105289d32f861906d7f674a4b7ca32e9`.
Generated data, mapping, panel, result, scoreboard, and temporary rerun artifacts remain outside
Git.

## 7. Acceptance Boundary

This record accepts the bounded candidate-v1 development delivery and the development-run portion
of `P3-ACCEPT-001`. It does not accept:

- candidate superiority to a fully invested CSI 300 portfolio;
- parameter revision, arbitration/blind access, or promotion to production;
- optional twelve-state labels, adds/reductions, generic formula or training systems;
- intraday matching, email delivery, operator execution reports, or broker integration;
- constrained contribution attribution, dashboard production, or shadow-decision production.

Every retained result declares `research_only=true` and `execution_authorized=false`.

## 8. Verification

Before independent reproduction completed, 34 focused governed-strategy/benchmark tests passed.
After the evidence status closed, all 26 documentation traceability contracts passed and final
`make check PYTHON=.venv/bin/python` passed 362 tests together with environment doctor,
compilation, and planning synchronization preview. Ruff lint/format passed on the affected files,
and strict mypy passed on nine affected production modules.
