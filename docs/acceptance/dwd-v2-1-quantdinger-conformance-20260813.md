# DWD v2.1 and QuantDinger external strategy conformance (2026-08-13)

| Field | Value |
| --- | --- |
| Status | superseded by the accepted external-findings correction rerun |
| Candidate | `quantdinger.strategy-v2-bullish-three-lines@11.0.0-quanthus.1` |
| Upstream | `OpenByteInc/QuantDinger@e64e1c227bf3174e441a42143620179b286387e1` |
| DWD package | `dwd-base-202607-v2.1` |
| Scope | decisions 2012-01-04 through 2021-12-30; T+1 execution through 2021-12-31 |
| Governance | `pending-review`; execution not authorized |
| Runtime evidence | `/home/ubuntu/data/quanthus/research/dwd-v2.1-quantdinger-conformance-20260813/` |

## Acceptance decision

> This record preserves the pre-correction result. The two open findings were closed by
> [`dwd-v2-1-external-findings-hotfix-20260813.md`](dwd-v2-1-external-findings-hotfix-20260813.md).
> Current consumers must use Base plus correction-001 and must not present the historical 38/38
> execution result as current.

The first external strategy check against the Quanthus DWD passes at the price-bar and signal
boundary. A direct, checksum-bound projection of the installed DWD v2.1 package produced exactly
the same 38 dated entry/exit signals as the native QuantDinger engine. The two engines also agree
on all 38 T+1 execution dates and sides. No QuantDinger market provider, cached result, or older
Quanthus replay ADS supplies the bars for this comparison.

The test does not establish that the two engines have the same portfolio accounting policy, and
it must not be judged by comparing their final return numbers as though they did. QuantDinger
compounds total account equity; Quanthus P2 fixes a CNY 1,000,000 research unit and transfers
profits outside that unit instead of redeploying them. The complete Quanthus result also uses its
frozen execution fees and normalized company-action policy. These policies deliberately produce
different returns from the same signals.

The DWD passes this bounded external signal-conformance gate, but the run identified one market
observation/tradability inconsistency and an unresolved duplicate-company-action defect. Those
findings are recorded below and are not waived by this acceptance.

## Immutable input identity

| Identity | Value |
| --- | --- |
| DWD content digest | `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9` |
| DWD manifest SHA-256 | `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b` |
| Security | `600519` / `CNStock:600519.SH` |
| Price basis | unadjusted |
| Projection rows | 2,431 |
| Projection SHA-256 | `sha256:d4b8c8e0d5cd5a8b57cccf6bdd7780d73664c3674990b4628c0b1d6ad9815495` |
| Projection identity | `projection-identity.json` in the runtime evidence directory |

The historical rows declare `knowledge_from=2026-08-03 23:59:00+00`. The replay therefore uses
the explicit research assumption that a session bar is available at that session's close. This
package proves a reproducible present-day historical view; it does **not** prove that each row was
published with the same content on its original historical date. Accordingly,
`point_in_time_publication_proven=false`, and this result remains development research rather than
blind-set or execution evidence.

## Three-layer reconciliation

### 1. Signal semantics

| Check | QuantDinger native | Quanthus adapter | Result |
| --- | ---: | ---: | --- |
| Entry/exit signals | 38 | 38 | exact match |
| Date, side, reason | 38 | 38 | `38/38` |
| Engine-only signals | 0 | 0 | none |

The adapter expresses QuantDinger's 95% target allocation as `target_percent`. Position quantity
is resolved only at the T+1 open using the execution price and a 100-share lot. It no longer uses
the signal-day close to precompute a quantity or look ahead to the next open.

### 2. Execution semantics

| Check | Result |
| --- | --- |
| T+1 execution date and side | `38/38` exact match |
| Closed trades | 19 in both engines |
| Maximum fill-price difference | approximately CNY 0.005 |
| Difference source | Quanthus cent rounding |

The same-input comparison uses a common simplified cost model to isolate strategy and scheduling
semantics. The complete P2 run is a separate result under `p2-daily-research-v1`.

### 3. Return and accounting semantics

| Result | Capital and cost policy | Total return | Maximum drawdown |
| --- | --- | ---: | ---: |
| QuantDinger native | total-equity compounding; common simplified costs | +70.240329% | 12.346024% |
| Quanthus comparison | fixed research unit; common simplified costs | +59.775856% | 11.092410% |
| Quanthus full P2 | fixed research unit; complete P2 fees and v3 company actions | +60.279056% | 11.209581% |

All three rows start with CNY 1,000,000 and contain 38 executions and 19 closed trades. The first
two rows prove that the residual return difference remains even after costs are aligned: native
QuantDinger reinvests accumulated profit while Quanthus keeps `compound_nav=false` and sends
capital above the fixed research unit to `external_pool`. The third row is the governing Quanthus
candidate result shown in Product Web. It includes minimum CNY 5 commission, slippage, stamp duty,
transfer fees, and normalized company actions.

This is an explained policy difference, not a signal mismatch. Cross-engine equality is asserted
for signal date/side/reason and execution date/side, not for final equity under different capital
and accounting contracts.

## DWD findings

### Zero turnover versus tradability

The `2012-05-29` DWD row for `600519` has valid OHLC values but zero volume and turnover, while its
tradability fields say that the security is tradable and can be bought and sold. QuantDinger uses
OHLC for the exit signal; treating zero volume as invalid market history suppressed that signal in
the initial adapter.

The conformance adapter now separates two questions:

- valid OHLC controls whether a strategy may observe the bar and generate a signal;
- volume, turnover, tradability, and side eligibility control whether P2 may execute an order.

This restores the native exit signal without claiming that the contradictory row is executable.
The DWD inconsistency itself remains a data-quality finding for the market-observation/tradability
domains.

### Duplicate company actions

The DWD v2.1 company-action domain contains 11 same-date, same-semantics duplicate groups arising
from two upstream fact paths. Applying both records would double-book economic events. The direct
DWD company-action input is therefore marked
`fail-closed-pending-semantic-deduplication` and is not consumed by the complete P2 result.

The full P2 run instead uses the frozen
`corporate_action_replay_2012_20260730_v3` normalized revision. Its checksums and ready gate pass,
it supplies 18 normalized actions for `600519`, and its adjustment-factor cross-check has no
exception. In particular, the 2014 and 2015 stock-distribution values are normalized from the raw
unit `1` to the economic ratio `0.1`. This is an explicit, versioned correction source, not silent
deduplication inside the replay.

## Superseded result

The earlier `strategy-replay-development-1f1ea4d-v1` compatibility result of +69.630054%, 30
fills, and 15 closed trades is retained as historical adapter evidence only. That bundle was
created from `p1-historical-replay-20260729-v1`; merely checking the currently installed DWD
authority did not make the older snapshot a child of DWD v2.1. It is therefore not a direct DWD
v2.1 result and must not be used as the candidate's current headline metric.

## Reproduction and evidence map

Run the bounded comparison from the repository root:

```bash
ops/bin/run-dwd-quantdinger-conformance
```

The wrapper mounts DWD v2.1 read-only, rebuilds the 600519 projection, runs native QuantDinger and
Quanthus against the same bars, then runs complete P2 with the frozen v3 company-action revision.
It writes:

| Evidence | Purpose |
| --- | --- |
| `projection-identity.json` | DWD, projection, PIT limitation, and company-action identity |
| `comparison.json` | signal equality and the three accounting results |
| `quantdinger-native-common-cost.json` | native-engine ledger and audit |
| `quanthus-common-cost.json` | fixed-unit comparison replay |
| `quanthus-full-p2.json` | governing complete P2 replay |

## Engineering and deployed acceptance

- direct DWD wrapper rerun: passed; signal and execution comparisons are both machine-recorded as
  exact `38/38` matches;
- focused Python regression: 61 passed, with one PostgreSQL test skipped because the optional host
  test DSN was not configured;
- Product Web source contracts: 5 passed;
- Product Query contract: 6 passed;
- changed Python Ruff checks and Bash wrapper syntax checks: passed;
- Product Web production build: passed with the existing large-chunk warning;
- repository `make check`: environment doctor passed, then `compileall` was blocked by pre-existing
  read permissions on `src/quanthus/event_library/` and three `tests/unit/test_event_*` files; no
  DWD-conformance compile or test failure was reported;
- deployed API image:
  `sha256:45cd7745b86691b302fb7e2c82f598adf8ab7f54e958432c54d80edf36f9d121`;
- deployed Web image:
  `sha256:21b185f55c1e1154f3964f69015330c1568da848bf9a5c888c57ebbcdec3bc8e`;
- public API returned catalogue `2026-08-13.2`, DWD v2.1, five candidates, zero governance
  admissions, zero execution authorizations, and the QuantDinger `60.279056 / 38 / 19 / 38/38`
  result;
- public Playwright: 4 passed across desktop Chromium and Pixel 7, including the exact headline
  result and horizontal-overflow checks;
- public root returned HTTPS 200 with a valid certificate, external `/metrics` returned 404, and
  PostgreSQL, API, and Web remained healthy; the existing PostgreSQL volume was preserved.

The accepted user-facing result is available at `https://quanthus.oci.061718.xyz/` under
"候选验证". Login credentials are managed separately from this repository and are not included in
the acceptance record.
