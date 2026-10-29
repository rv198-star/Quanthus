# Independent astock source subproject

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Work item | [P10-DATA-SUPPLY-005 / #587](https://github.com/rv198-star/Quanthus/issues/587), parent #581 |
| Parent | [Permanent P10 data line](lld/12-permanent-data-line.md) |
| Owner direction | 2026-10-29: maintain a public astock fork, keep Quanthus private, preserve their relative independence |
| Bounded continuation | 2026-10-30: integrate qualified capabilities into P10, establish units/time/rollback and dated quality candidates with a finite observation period |
| Source repository | [rv198-star/astock](https://github.com/rv198-star/astock), public fork of simonlin1212/a-stock-data |

## Purpose and boundary

Maintain reusable source acquisition in an independently versioned project while
P10 retains data governance and consumer admission. A separate source repository
makes absorbed capabilities, upstream updates, source-specific bugs and releases
traceable. Repository creation is not migration of the deployed Quanthus collector.

```mermaid
flowchart LR
    U["a-stock-data upstream"] --> A["public astock source library"]
    N["qualified new channel/interface"] --> A
    A -->|"exact commit/package digest"| P["private Quanthus P10 adapter"]
    P --> W["governed ODS/DWD and consumer admission"]
```

| Public astock owns | Quanthus P10 owns |
| --- | --- |
| Source requests, transport bounds, source identity, parsing and explicit errors | Jobs, schedules, incremental windows, warehouse construction and releases |
| Raw response envelope, request/body hashes and acquisition clock | Retention, PIT interpretation, frozen consumer gates and governed snapshots |
| Generic source contracts, offline tests and source-library releases | Product/consumer quality decisions, deployment, rollback and operational authority |

astock must not import Quanthus or contain its credentials, node paths, source
archives, policies, warehouse schemas or strategy code. Quanthus may depend on a
fixed astock release; the source library does not depend on its consumers. Public
fork PRs describe generic source behavior without copying private issue bodies.

## Repository and update model

The fork preserves the complete 68-commit upstream history through
`cb57c7aba720380b48a11dd065c6db57a2c39db9`, its Apache-2.0 license, SKILL 3.10.3,
and original tests. `upstream` points to simonlin1212/a-stock-data and `origin` to
rv198-star/astock. Own Python 3.10+ code lives in `src/astock`; the library version
is separate from the retained upstream skill version.

The source-library implementation was merged through
[astock PR #1](https://github.com/rv198-star/astock/pull/1), exact tested commit
`0eeedec0b0f22df78f4e738db2765b5219ca0ab6` on `codex/source-library-bootstrap`.
Candidate prerelease [sources-v0.1.0](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.0)
contains the verified wheel, source archive and checksums. It is a source-library
candidate, not an active Quanthus dependency.

Upstream updates enter a reviewed branch. Preserve their original history and
identify applicable changes; never regenerate executable production modules from
the skill during a running job. Absorbed channels enter as individual source and
capability changes with provenance and regression evidence. Same-backend wrappers
and duplicate institution entries do not count as independent data suppliers.

Quanthus consumes an exact full commit and wheel digest at build time for its
bounded P10 observation consumer. No live job pulls `main`/`latest`, no task switches
`/opt/collector` by virtue of a new fork commit, and no mutable source directory
becomes a hidden deployment dependency. The initial source repository is a logical
subproject with independent Git history. The optional `supplement-sources` dependency
and the source-watch image below activate only the specified observation consumer.

## Source-library candidate

Owner continued source absorption and isolated environment construction on
2026-10-30. Fourteen independently implemented request contracts now reuse the
retained qualification responses; they do not restart the 673-request screen.

| Capability | Increment over the upstream baseline | Engineering evidence |
| --- | --- | --- |
| ChinaMoney LPR direct | Additional actual backend beside upstream Eastmoney LPR | 12 original records parsed from the retained response; Decimal percent units and effective dates |
| CNBC RSS / generic RSS-Atom parser | Reusable publisher feed discovery | 30 original feed items parsed; publication text retained, missing/naive clocks remain unknown |
| Tencent / Sina / Eastmoney single-security quote | Explicit source, venue, native price and clock contracts | Three original snapshots replayed; no adjustment, suspension or fill inference |
| Eastmoney CPI / PPI / PMI / GDP / M2 | Reusable current-vintage numeric rows | Ten original rows replayed; native field names/values and period text retained |
| ChinaBond treasury curve | Identified curve and source-date contract | 505 original points replayed; exact curve ID/name checked |
| CNINFO HTTPS disclosure index | Raw metadata supplement | Two original rows replayed; source milliseconds retained, no PDF acquisition |
| Sina roll channels 2516 / 2510 | Publisher metadata discovery | Four original items replayed; 2510 is archive metadata, not current-policy coverage |

Transport/source failures are `FAILED`; explicit valid empty lists are `EMPTY`;
only nonempty valid records are `SUCCESS`. Raw bodies and request/acquisition
identity remain attached. Neither state nor timestamp grants historical PIT.
Public source-library availability and P10 purpose admission remain separate.

The isolated Python 3.10 environment contains hash-verified PyPI build/runtime
wheels and openpyxl; shared production environments were not modified. The
maintained library's 30 synthetic contracts and Ruff pass. The full retained
upstream-plus-library suite now passes: 221 tests, four opt-in live cases skipped.
Legacy tests reuse the existing read-only pandas dependency closure; a fresh wheel
installation separately passes all 30 library tests without checkout imports.
Two builds with pinned tools and the same source epoch produce the same wheel,
SHA256 `b1896aabced17fcb1761121f9a1fce8a40073495185d2a9ea90a79e3398df646`.

A separate frozen 14-request live canary returned 13 successful samples; Eastmoney
quote had a connection failure and is held for current availability. A previous
valid replay does not erase that failure. Macro/curve/quote values remain explicitly
source-native; consumer unit/basis admission is still required. Full per-purpose
dispositions, artifacts and cleanup evidence are in the
[supply work item](../work-items/p10-source-supply-20261029.md).

The supplied cn-financial-scraper README declares MIT, but the snapshot lacks a
separate license and a verified public upstream. No snapshot code is copied into
the new library. Interface leads and package identity are documented; future code
copying requires an attributable source/license. This does not block independent
implementation of the already observed interfaces or current daily products.

## P10 observation integration and rollback

The consumer is **P10 source qualification and supplementary observation** under
#583–#587. It retains independently addressable capabilities so optional-source
failures are isolated. The fifteen default contracts include the original fourteen
and the subsequently verified Eastmoney NAV request for fund `000001`.

The versioned policy is
[`p10-supplement-sources.v1.json`](../../config/pipelines/p10-supplement-sources.v1.json).
It pins astock `0.1.1`, exact source `8360132de1255a68e42df8a1aa142f1335bd68e7`,
wheel SHA256 `49b04b404016fa5432acb12a1bbfdd5a2a4268adbd3a4e44a1f16adc53b1eebf`,
all source modules, the request/security/fund universe and hash-locked transport
dependencies. The public change is [astock PR #2](https://github.com/rv198-star/astock/pull/2),
with [sources-v0.1.1](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.1).
No source snapshot code was copied.

[`p10_source_contracts.py`](../../src/quanthus/p10_source_contracts.py) owns exact
Decimal mapping: GDP/M2 亿元 × 100,000,000 → CNY; rates/changes % ÷ 100 → ratios;
CPI/PPI/PMI index points remain index points; treasury maturity is years. GDP
periods begin January 1 and end at the stated quarter end. Macro `REPORT_DATE`
remains a vendor period marker. The fixed Shanghai A-share quote scope uses
CNY/share with source snapshot basis; no volume, adjustment or suspension is inferred.
Fund NAV uses CNY/fund unit, retains the vendor accumulated-NAV basis and its
valuation date, and discloses request-bound identity when no response code is echoed.
Primary unit references and retained evidence hashes live in the policy.

Every row starts its knowledge interval at actual complete-response acquisition.
Publisher clocks, quote clocks, CNINFO epoch milliseconds and LPR effective dates
remain separate fields. No scheduling rule or display date supplies a historical
publication clock. Source-clock claims over 300 seconds in the future, future
source periods and stale current-news feeds fail their affected purpose; missing
quote clocks remain a disclosed diagnostic limitation. Channel 2510 serves archive
metadata only. The policy freezes these new observation thresholds.

[`p10_supplement.py`](../../src/quanthus/p10_supplement.py) builds one shared
raw → parsed → normalized intermediate per capability and Job. Quality and ledger
consumers share it. Unpublished staging is `TMP`; published raw/normalized records
and manifests are immutable P10 qualification audit products, retained until an
explicit P10 disposition. They have a stable governance consumer, not an automatic
market-warehouse consumer. Identities include code, policy, source/dependency,
schema, date scope, universe, full request, raw bytes and acquisition time.
Elapsed time, rows, bytes and build/reuse disposition are recorded. A verified
same-day job may be reused without another request; independent online validation
and full reproduction must be named separately.

Requests run serially with a hard 20-second per-source process budget, including
DNS, a 256 KiB response ceiling and no retries or provider substitution. A failing
or disabled source publishes an explicit unavailable result. The prior good
snapshot remains a reference, never a silent fallback. Exact-snapshot reads check
hashes, schema, the caller's policy SHA and actual knowledge cutoff.

The independent watch lasts seven days, at one cycle per day, with its original
end time persisted across restarts. At least three distinct successful days and
the latest three consecutive successful days, with a recent final observation,
permit `REVIEW_DUE`. Failure or insufficient evidence remains a hold. Expiry stops
the process; neither success nor elapsed time promotes a business input. Each
candidate records verification time, latest source date/clock and age, field-unit
contract, quality flags, failed attempts and review deadline.

The isolated runtime and selective-disable/stop rollback are in the
[source-watch runbook](../../ops/source-watch/README.md). Only its own observation
root is writable. Stopping it removes the optional supplement from new work;
selective disable records `SOURCE_DISABLED_ROLLBACK`. Tests cover units, clocks,
optional failure isolation, prior-snapshot preservation, tampering, cutoff reads,
daily reuse, finite probation and non-renewal in
[`test_p10_supplement.py`](../../tests/unit/test_p10_supplement.py).

After reclaiming audited local build images, Quanthus full `make check` passes:
5,699 passed, 20 skipped. This documentation follow-up retains the exact tested
executable/config/test closure and reruns affected document/planning checks. This
source-library engineering pass does not admit historical data or migrate a live
collector.

Source qualification under #583–#585 continues for the remaining function contracts.
Its dated outcomes and current execution evidence are in the linked supply work
item. Consumers needing warehouse substitution or historical PIT retain their own
admission gates.

## Additional access and unit qualification — 2026-10-30

Owner explicitly continued the remaining sources without waiting for the first
seven-day watch. Three corrected contracts use a separate astock `0.1.2` runtime
and [`p10-supplement-sources.v2.json`](../../config/pipelines/p10-supplement-sources.v2.json).
The original fifteen-source policy, installed library and running watch remain
fixed. The new optional `supplement-sources-v2` extra belongs in a separate
environment: the two exact source-library versions must not be installed together.

The new policy explicitly selects three declared capabilities from the verified
library registry. Unknown or empty selections fail closed; a policy without that
explicit subset mode still requires the complete supported capability set. This
allows a new tranche to operate independently without re-requesting the original
fifteen capabilities or inheriting their observation days.

| Corrected contract | Purpose and units | Explicit limit |
| --- | --- | --- |
| Eastmoney `/api/security/ann`, stock600519, two records | Archival announcement-index metadata; notice-date label and native display/production time text | No current-disclosure completeness or historical publication claim; no PDF acquisition |
| Eastmoney single ETF510300 trading-flow snapshot | Signed CNY amounts; vendor-reported percentages mapped once to ratios | Vendor secondary-market order classification, not creations/redemptions or verified investor positions |
| Jisilu current anonymous `/webapi/cb/list/` | Partial bond metadata and C-type convertible reference prices: CNY per100 face; conversion/underlying prices CNY/share | At most30 anonymous rows, no full-universe claim or login; E-type exchangeables retain metadata and unqualified native values only |

The public publisher pages and their declared scripts establish corrected paths,
field-to-label bindings and monetary display scales. SSE/SZSE primary rules and an
issuer disclosure establish convertible and conversion-price denominations. Their
exact URLs, acquisition times and retained body digests are attached to the policy.
No source script code or financial payload is copied into either repository.

Notice dates can label a later business day and never backdate knowledge. Jisilu's
context date is not joined to its optional time-only row clock; such a join can
manufacture a future quote around midnight. Missing listing dates remain unknown.
Reference prices confer no transaction, listing/suspension state or fill. ETF epoch
seconds stay a separate vendor claim; all knowledge intervals start at actual
complete acquisition. Each candidate retains verification time, archive/guest/type
limits, exact units, raw identity and the prior failed request's history.

The three-source watch uses its own empty store and persisted seven-day budget.
Stopping or disabling it is the rollback. Its daily evidence cannot pad the original
watch or automatically promote any business consumer. The same immutable snapshot,
finite request, missing-data isolation and PIT contracts above apply.

## Named futures contract qualification — 2026-10-30

The next tranche uses independently versioned astock `0.1.3` and
[`p10-supplement-sources.v3.json`](../../config/pipelines/p10-supplement-sources.v3.json),
with the separate `supplement-sources-v3` extra and hash-locked
[`requirements-v3.lock`](../../ops/source-watch/requirements-v3.lock). Existing
fifteen- and three-capability policies/watches stay pinned. The new policy selects
only Sina's named `DCE:V2701` PVC futures contract, one request, one row and at most
4096 response bytes within the existing 20-second outer budget.

The publisher's current contract page lists V2701 and its delivery month is still
in the future. Its own JavaScript maps latest/open/high/low to raw fields 8/2/3/4
after accounting for a prepended presentation symbol. A DCE-authored contract
file and the publisher page agree on CNY/tonne and 5 tonnes/lot. The latter is
contract size, never a price multiplier. Official DCE-host reference requests
timed out; independent current exchange listing is unverified. Conflicting tick
sizes, volume/open-interest quantities and bid/offer sizes remain unqualified.
This is a vendor contract-price diagnostic, not a physical spot or executable price.

The source emits a date label and six-digit time. They remain separate because
the date's calendar-versus-trading-day role is unproven, especially around night
sessions. No next-day, weekend or holiday inference is permitted. Source clock,
calendar quote date, trading day and publication time remain null; knowledge starts
at complete acquisition. Real-time freshness and historical PIT are not admitted.

A frozen contract scope binds code, venue, product, delivery month, evidence floor
and conservative stop-before time. It stops before the delivery month starts
(`2027-01-01 00:00 Asia/Shanghai`), without guessing the exchange's final trading
day. There is no automatic roll. Expired, premature or malformed scopes issue no
HTTP request. Scope status joins the immutable job identity so a same-UTC-day
expiry cannot reuse a formerly good job; the current ledger changes to
`HOLD_CONTRACT_SCOPE`. Prior evidence remains readable for audit, never silently
substituted into a failed current observation.

[`test_p10_source_futures.py`](../../tests/unit/test_p10_source_futures.py) covers
exact units, wrong contracts, independent night labels, conservative expiry and
same-day cache/HTTP isolation. The shared rollback/PIT tests continue to apply.
The new finite watch, when launched from published source, has its own empty
store and nonrenewable period; it neither pads existing qualification days nor
blocks continued screening of other sources.
