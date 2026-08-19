# DWD v2.1 external-findings Hotfix acceptance (2026-08-13)

| Field | Value |
| --- | --- |
| Status | accepted as a bounded manual DWD correction |
| Work item | `META-DATA-DWD-002` / TPlan `G-DWD-HOTFIX-001` |
| Parent | `dwd-base-202607-v2.1`, unchanged |
| Correction | `dwd-base-202607-v2.1-correction-20260813-001` |
| Scope | zero-activity tradability and company-action semantic duplication |
| Recurring weekly correction | still open; not accepted by this Hotfix |

## Decision

The two defects found by the first QuantDinger external comparison are corrected through one
immutable package bound to the exact DWD v2.1 Base. The Base was not rewritten. Consumers apply
business-key replacements and stable-id tombstones, or use the correction's checksum-bound replay
projection. Deep verification rebuilds the effective view and proves that the targeted defects no
longer remain.

## Identity

| Identity | Value |
| --- | --- |
| Parent content digest | `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9` |
| Parent manifest SHA-256 | `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b` |
| Correction content digest | `sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da` |
| Correction manifest SHA-256 | `sha256:4c8eeb5c0d74bbf2953f003364fd138832f15a5163254b710f9db8430767ffde` |
| Installed package | `/srv/quanthus/dwd-dws-v2.1/corrections/dwd-base-202607-v2.1-correction-20260813-001` |

`READY` binds the correction manifest. The manifest binds the Base, every payload checksum, the
normalization revision, application keys, reasons, and row counts.

## Zero activity versus tradability

The v2.1 builder classified a daily row as tradable whenever a close existed and neither explicit
suspension nor provider `trade_status=0` was present. It did not carry volume or turnover into the
state query. This misclassified static price observations as executable sessions.

The full-Base audit found 445,691 daily rows with non-positive volume and turnover. Of these,
398,108 were marked `TRADABLE_OBSERVED` across 3,469 listings from 1990-12-19 through 2026-07-31.
The correction replaces all 398,108 current state keys with:

- `is_tradable=false`, `can_buy=false`, `can_sell=false`;
- `reason_code=ZERO_ACTIVITY_OBSERVATION`;
- null upper and lower executable prices; and
- a new input digest containing the parent state digest and activity values.

The future builder carries volume/turnover into state construction. Explicit suspension and
provider-not-trading evidence keep higher reason priority. A missing close remains distinct from a
static observed price.

For `600519`, the correction covers 2011-05-25, 2012-05-29, 2012-09-03, and 2012-12-10. Eastmoney,
Tencent, and Tonghuashun history all omit 2012-05-29 between 2012-05-28 and 2012-05-30, confirming
that it was not a normal positive-activity session.

## Company actions

The v2.1 disclosure builder independently materialized `corporate_actions` and
`dividend_history`, even though its source audit already described the former as normalized
authority. It also materialized numeric zero as a term and interpreted share quantities supplied
per ten shares as per-share ratios.

The full audit classified 45,929 exact term-overlap groups, all between the two source paths, and
found no same-coordinate same-term value conflict. It also found 116,461 zero-value supplemental
terms. The correction applies:

| Operation | Rows |
| --- | ---: |
| Duplicate/zero term tombstones | 162,390 |
| Fully empty supplemental action tombstones | 54,069 |
| Matching disclosure/subject tombstones | 54,069 each |
| Per-ten to per-share term replacements | 7,352 |
| Replay-normalized actions | 38,531 |
| Retained fail-closed normalization exceptions | 328 |

Future construction processes canonical actions first, builds a term authority index, drops zero
terms, suppresses exact supplemental overlap, keeps genuinely missing supplemental terms, and
fails closed on a value conflict. The governed vocabulary now states
`share_quantity_unit=per_ten_shares`; `600519` 2014/2015 bonus ratios are therefore `0.1`, not `1`.

## External replay regression

The projection and full P2 replay now consume Base plus correction. They no longer read the v3
revision directory as an external side path. The effective 600519 company-action domain has zero
duplicate semantic groups.

| Result | QuantDinger native | Quanthus common costs | Quanthus full P2 |
| --- | ---: | ---: | ---: |
| Executions | 38 | 36 | 36 |
| Closed trades | 19 | 18 | 18 |
| Total return | +75.739509% | +59.076934% | +59.628843% |
| Maximum drawdown | 12.407455% | 11.160902% | 11.274136% |

Thirty-seven entry/exit intents are shared. QuantDinger carries the 2012-05-28 buy intent across
the corrected non-trading 2012-05-29 observation and fills on 2012-05-30. Quanthus P2 follows its
accepted cancel-buy rule: it emits `security_not_trading_observed`, cancels the order, and requires
a new close decision. Consequently that entry and its later 2012-06-28 exit do not become Quanthus
fills. This is an explained execution-policy divergence exposed by the data repair, not a remaining
DWD defect. The policy is retained rather than changed to preserve the old 38/38 headline.

The correction-bound projection SHA-256 is
`sha256:336a417b5609432c2442923e7c9837389621a92cdd1e05f7e14fe30d4240b1cd`.
Historical `knowledge_from` remains a present-day reconstruction; `point_in_time_publication_proven`
remains false, so arbitration, blind-set, governance admission, and execution authorization remain
closed.

## Verification

- correction build plus an independent Docker-mounted deep overlay verification: passed, with
  correction content digest
  `sha256:1c1d1f4ea906797255fbc71cc3915bb905e51e7aea202d27042e33692b72e9da`;
- focused correction, candidate, P2, and product-catalog regression: 71 passed;
- Platform product/API contract regression: 24 passed;
- product Web source contracts and build: 13 passed;
- Ruff and Python/shell entry-point syntax checks: passed;
- correction-bound QuantDinger/Quanthus rerun: accepted with the governed divergence above;
- public authenticated API returned catalog `2026-08-13.3`, correction-001, 36 executions,
  18 closed trades, `+59.628843%`, `pending-review`, and no execution authorization;
- public Product Web browser acceptance: 4 passed across desktop Chromium and Pixel 7 at
  `https://quanthus.oci.061718.xyz/`;
- deployed API image:
  `sha256:6b3ebdeb68f96006b2f7f31ea9de63ab33411b8bff573d191e4eab1c5350049a`;
- PostgreSQL container and named volume were preserved during the API-only rollout; and
- DWD Base content and manifest digests remained unchanged.

Repository-wide `make check` still stops at the pre-existing unreadable P4 `event_library` source
and three corresponding unit-test files. Doctor passes, and this does not affect the bounded
Hotfix suites above; those file permissions and contents were not changed by this work item.

The correction package is a runtime data artifact and is not committed to Git. Source changes,
tests, manifests, and this evidence record are the repository delivery.
