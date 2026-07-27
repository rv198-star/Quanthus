# S3 Price-Limit Regime Contract

- Status: Implemented and promoted on OCI
- Tracking issue: [#9](https://github.com/rv198-star/Quanthus/issues/9)
- Active policy: `config/price-limit.v2.json`
- Preserved historical policy: `config/price-limit.v1.json`

## Resolver precedence

The production resolver applies, in order: explicit OOS, ST overlay, code-specific IPO window,
then board baseline. Main-board ordinary limits are 10%. Main-board ST-class limits are 5% through
the last open session on 2026-07-03 and 10% from the rule effective date 2026-07-06. ChiNext is
10% before 2020-08-24 and 20% from that session; its ST overlay changes on the same boundary.
STAR remains 20% when ST-class. All boundaries are effective-dated policy rows consumed by both
the single-case resolver and bulk DuckDB build; they are not hard-coded fall-throughs.

The 2026 boundary follows the SSE and SZSE trading-rule revisions published on 2026-04-24:

- [SSE trading rules](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)
- [SZSE trading rules](http://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html)

IPO windows count only actual security sessions where the exchange is open and provider
`tradestatus=1`. A suspension does not consume a window session. Registration-regime IPOs have no
price limit for the first five such sessions. Legacy main-board first-day rules without a complete
computable bound remain fail-closed. The 2014 to registration-era 44% first-day rule references
issue price; until issue-price data is versioned, its first session also remains fail-closed rather
than substituting prior close.

## Price computation

Ordinary limits use the most recent earlier `tradestatus=1` unadjusted close. Suspended placeholder
bars are not price references. Prices are rounded to the 0.01 tick using positive half-up exchange
style. The output separates `entry_policy_allows` from retrospective `is_executable`: the former
describes whether PIT status and rule inputs permit an entry, while the latter also requires the
observed session to be tradable.

Unknown S2 state, unknown trade status, missing prior close, unsupported legacy IPO rules, and
missing issue price all produce explicit fail-closed reason codes. They never fall through to the
ordinary board limit.

A limited security's first `tradestatus=1` session in the supported history may have no earlier
tradable close inside that history. It is classified separately as
`first_supported_trade_no_prior_close_fail_closed`. A missing prior close after the first tradable
session remains `prior_close_missing_fail_closed` and is an upstream coverage defect.

## Publication

`quanthus build-price-limits` verifies candidate checksum and cutoff ordering, builds the
execution-day resolver, checks uniqueness and IPO session bounds, and writes an immutable manifest
with schema/content hashes. Builds without a complete, non-overlapping board/date policy fail
before publication. The previously promoted P1 revision is
`price_limit_execution_v2_oci_v1_3_20260725`. It is consumed with formal S2 and OCI DWD none by
snapshot `p1-s2-s3-dwd-20260725-v1`.
