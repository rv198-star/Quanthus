# P2 Corporate-Action and Fixed-Unit Metrics Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Cases | `P2-CORPORATE-ACTION-001`, `P2-FIXED-UNIT-REPORT-001` |
| Scope | PIT corporate-action accounting and reconciled fixed-unit performance |
| Governing design | `docs/design/lld/02-backtest-engine.md` |
| Governing work | `P2-LEDGER-001`, `P2-METRIC-001`, `P2-AUDIT-001`, `P2-ACCEPT-001` |

## 1. Corporate-action decision

The engine accepts a corporate action only on its effective market date and only after its
timezone-aware `known_at`. Cash dividends increase unit cash and dividend income through a balanced
ledger. Bonus shares change settled quantity but retain total position cost. The post-action mark
is an explicit price and snapshot input; P2 does not calculate an ex-right price from future bars.

The hand-calculated cash-dividend case moves CNY 20.00 into cash while a CNY 10.20 prior mark becomes
an explicitly sourced CNY 10.00 ex-right mark. Unit equity remains exactly CNY 10,014.00. A separate
2% bonus-share case moves 100 shares to 102, retains CNY 1,001.00 total cost, and also preserves
equity under its explicit CNY 10.00 reference mark.

## 2. Fixed-unit report decision

The report publishes exactly the fixed-unit interpretations required by LLD 02:

| Metric | Expected |
| --- | ---: |
| Trade return | `0.00743539` |
| Unit-capital-day return | `0.00024933` |
| Total PnL | CNY `7.48` |
| External cash flow | CNY `0.00` |
| Cash-flow-adjusted drawdown | `0.01186474` |
| Compound NAV | `false` |

Trade cash-flow PnL must equal opening-to-ending system wealth after external flows. A one-cent
disagreement fails report construction instead of publishing two inconsistent return stories.

## 3. Evidence map

| Concern | Evidence |
| --- | --- |
| PIT corporate-action and accounting contracts | `src/quanthus/backtest/contracts.py` |
| Corporate-action application | `src/quanthus/backtest/engine.py` |
| Fixed-unit report | `src/quanthus/backtest/metrics.py` |
| Hand-calculated oracles | `tests/fixtures/golden/p2_corporate_action.v1.json`, `tests/fixtures/golden/p2_fixed_unit_report.v1.json` |
| Behavioral verification | `tests/unit/test_backtest_engine.py`, `tests/unit/test_backtest_metrics.py` |

## 4. Residual boundary

This slice consumes an already PIT-resolved action; automatic P1 dataset loading and insertion into
a multi-session event loop remain open. The report covers one closed fixed-unit round trip. Batch
portfolio reports, audit persistence, notifications, hypothetical paths, and two-window execution
remain outside this evidence.

## 5. Verification

- Focused policy, engine, and metric suites: passed 22 tests.
- Ruff, formatting, and mypy on the P2 package and focused tests: passed.
- Full repository check: passed 140 tests.
