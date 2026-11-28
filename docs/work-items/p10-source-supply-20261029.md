# P10 supplementary-source qualification — 2026-10-29

Owner requested an update of `simonlin1212/a-stock-data`, followed by individual
online qualification of the delivered `cn-financial-scraper` sources. Passing
capabilities enter a purpose-limited candidate area; unusable declarations are
excluded with evidence. The finite work package is
[P10-DATA-SUPPLY-000 / #581](https://github.com/rv198-star/Quanthus/issues/581),
under the standing P10 epic #478 and [LLD 12](../design/lld/12-permanent-data-line.md).

This is supplier qualification for P10 source selection. It does not evaluate a
strategy/model, change a learning module, grant historical PIT, or publish ODS/DWD.
ADR 0016's purpose-relative quality and finite investigation rules apply.

## Current recovery checkpoint

### 2026-11-28 vendor-lane and governance update

Three material changes since the tranches below; the older sections remain
accurate operational history for the v1-v4 watches:

1. **astock fork governance.** `rv198-star/astock` is now private (upstream
   fork relation retained; unauthenticated access 404; release assets intact).
   Vendor credentials are centralized in the private Gitea repository
   `rv192/quanthus-secrets` (vendor-keys.json) and mirrored read-only on CN02
   at `/srv/agentdock/quanthus-secrets/vendor-keys.json`; rotation updates
   both atomically. No keys exist in the astock repository. Future source-watch
   rebuilds can no longer pull GitHub release assets anonymously; existing
   containers embed their wheels and are unaffected.
2. **astock 0.2.x.** Vendor MCP adapters `vendor_wind`/`vendor_choice` merged
   (PR #6), upstream v3.10.4 synced (PR #7), 0.2.1 released with wheel/sdist/
   SHA256SUMS, and `docs/capability-inventory.md` now indexes all three
   capability planes. CN02 checkout and library verified at 0.2.1. Existing
   v1-v4 watches need no re-pinning: the 0.1.4-to-0.2.1 wheel diff only adds
   new modules, and their hash-verified modules are byte-identical.
3. **Vendor lanes validated as reference capabilities**
   ([P10-DATA-SUPPLY-006 / #777](https://github.com/rv198-star/Quanthus/issues/777)).
   Live evidence: Wind Alice (0.0001 points per get_stock_kline call),
   Eastmoney MX free tier, iFinD trial tier restricted (core tool unauthorized),
   Tushare community transit (low-trust, http-only, excluded from astock
   adapters; consumer-side reference lane only). Cross-source check on
   600519.SH 2026-11-24 shows price agreement across three lanes and a 5-share
   volume divergence from MX display scaling — per-field provenance remains
   mandatory when comparing lanes; MX volume is never share-exact truth.
   Automated vendor watch (v5) is deliberately deferred: the supplement
   runtime is a single-request no-key model, vendor MCP sessions need a new
   transport path plus container credential injection, and no consumer has
   named a recurring cross-verification need. Revisit when a consumer names
   one; manual bounded probes cost fractions of a point meanwhile.



The CN02 runtime lineage is established. A complete new upstream reference checkout
is pinned locally; the production collector is a different, Quanthus-owned code
bundle. The declaration inventory and bounded request manifests are frozen outside
Git. The first screening pass and a separate API tranche retain raw response hashes.
Functional coverage beyond the named samples remains work under #584/#585; an HTML
site-identity result must never be reported as a working financial data feed.

Owner subsequently selected a public, relatively independent astock fork while
Quanthus remains private. [P10-DATA-SUPPLY-005 / #587](https://github.com/rv198-star/Quanthus/issues/587)
tracks the [source-subproject boundary](../design/astock-subproject.md) and twenty-one
source-library request contracts in [rv198-star/astock](https://github.com/rv198-star/astock).
The original reference checkout and its hashes below remain historical evidence;
repository creation does not upgrade the deployed collector.

Owner's further 2026-10-30 instruction continues actual P10 integration, unit/time/
rollback contracts, dated quality candidates and finite observation, through the
existing issues. The original fifteen-capability runtime uses source library `0.1.1`
with the verified fund-NAV request;
the original `0.1.0` observations below remain historical evidence. The exact
Quanthus adapter, policy and watch boundary are in the child design. The earlier
nine-request tranche recorded one observation-purpose pass, five rejected request/
purpose definitions and three holds; the later three-hold continuation is below. See the current integration section
for the complete dated ledger and test/operational receipts.

The latest tranche adds only Sina's named PVC futures contract `DCE:V2701` through
astock `0.1.3` and a separate v3 policy/watch. Price units pass for vendor
contract-price diagnostics; the date/time labels remain independent and trading-day,
real-time, tick-size and quantity admission remain open. See the final futures
section below for the actual first-cycle receipt and remaining-route dispositions.

The original fifteen-capability candidate watch is running locally on CN02 as
`quanthus-p10-supplement-watch`, pinned Quanthus source
`d335c1a3d3ad2197340f0ef4c66f0bced657070c`, with its only writable bind at
`/srv/quanthus-source-watch/qualification-d335c1a3d3ad`. Its first fifteen requests
all pass their observation purposes, with explicit archive/identity/clock limits.
The seven-day period ends **2026-11-06 01:39:23 Asia/Shanghai**; next scheduled
cycle is **2026-10-31 01:39:23 Asia/Shanghai**. Do not relaunch an identical daily
canary or extend the persisted period. Inspect `watch.json`, `candidates.json` and
immutable `runs/*/manifest.json`, then continue source-specific issues. Stopping
and restarting this new watch was verified without a second fetch or changed
manifest/deadline. Business and historical PIT admission remain false.

| Work item | Scope | Evidence/remaining gate |
| --- | --- | --- |
| [#582](https://github.com/rv198-star/Quanthus/issues/582) | Runtime lineage and upstream baseline | Daily chain established; upstream reference updated; no production upgrade claimed |
| [#583](https://github.com/rv198-star/Quanthus/issues/583) | Census, contracts and disposition ledger | Frozen declaration census; incomplete API definitions still need functional contracts |
| [#584](https://github.com/rv198-star/Quanthus/issues/584) | Structured data, announcements and regulatory APIs | Bounded samples; capability-specific results and limitations retained |
| [#585](https://github.com/rv198-star/Quanthus/issues/585) | Media, RSS and institution sites | Site identity and RSS tested separately; unavailable/network-limited entries stay explicit |
| [#587](https://github.com/rv198-star/Quanthus/issues/587) | Independent astock fork and bounded absorption | 21 contracts; v1/v2/v3 watches preserved and a separate bounded A-share v4 policy |

The Owner then continued the three access/unit holds independently of that watch.
[astock PR#3](https://github.com/rv198-star/astock/pull/3) adds three corrected
contracts in source-library 0.1.2, tested source
`3814b20ccb121f44e8bcdc73ba4c1ec4981176e2`. A separate explicit three-key policy
in `config/pipelines/p10-supplement-sources.v2.json` qualifies archival stock
announcements, one ETF's vendor trading flows and the anonymous guest-bond subset.
Their 02:01 observations and unsupported purposes are in the three-hold section.
The original fifteen-source running watch above is not replaced or restarted.
The three-capability container `quanthus-p10-holds-watch` now runs source
`f2247db5d35e60ced2e849cadb38d68cbf6298da` in the separate root
`/srv/quanthus-source-watch/holds-f2247db5d35e`. Its first three observations at
02:34:36–02:34:39 pass their limited purposes. Its next cycle is
**2026-10-31 02:34:34 Asia/Shanghai**, and its nonrenewable end is
**2026-11-06 02:34:34 Asia/Shanghai**. The original watch still ends at01:39:23.
Both periods are observation requirements; other source qualification proceeds.

Owner's latest continuation makes **A shares the highest priority**. Hong Kong/US
work is low priority and requires new access or request-contract evidence before
another bounded probe; unchanged failures are not replayed. The next two A-share
functions use astock `0.1.4` and the separate v4 policy: one security's financing/
stock-lending window and a two-row block-trade disclosure sample. Dates are frozen
qualification samples, not a latest-session/full-market feed. See the final section
for source-native units, partial coverage and the retained amount discrepancy.
The existing fifteen/three/futures watches and their deadlines remain unchanged.
Seven-day observation is not a predecessor for another bounded source tranche.

## Why daily collection works without the upstream checkout

On `quanthus-cn02`, the actual daily path is:

```text
cn02-p10-container@daily.service
  /opt/quanthus/p10-current/ops/bin/run-p10-container-job daily
  /etc/quanthus/p10-container-deployment.json → fixed Quanthus image
  daily_base24_cycle → daily_base24_provider → pinned Quanthus collector
  lane_runner BaoStock daily + events → core → BaoStock native 5m → final receipt
```

The image is `sha256:9b1e4a0674d7ceae5f282dcfac0bdc9c24f4c58c6f9c0ebc1c1995a0eee63171`;
pipeline revision is `1a6cc86722df78537dcecdab6ba634740b462854`; collector revision is
`f839af0033ab4aa8cbe6d9fb933f29db88792e91`. Both image source label and collector Git
remote identify `rv198-star/Quanthus`. The collector is exported into `/opt/collector`
inside the image, with source-file identity checks. Host collector checkout:
`/opt/quanthus/cn02-base24-collectors/f839af0033ab4aa8cbe6d9fb933f29db88792e91`.

The 2026-10-29 service ran 17:30–18:46:30 Asia/Shanghai and exited zero. Its final
receipt reports `QUALIFIED_1D_NATIVE5M`, with the same two code revisions,
`consumer_admission=NOT_GRANTED` and `execution_authorized=false`. Only metadata
was inspected; the earlier core-only receipt is not the final result.

Git history provides a concrete integration boundary:
[`0e4186a51d09b92edcb9b6bb1bf62f4be3de0f52`](https://github.com/rv198-star/Quanthus/commit/0e4186a51d09b92edcb9b6bb1bf62f4be3de0f52)
versioned ODS providers on 2026-07-26 and replaced the external
`minute/bin/minute_backfill.py` default with `quanthus.providers.minute_tdx`.
The current daily adapter uses its own BaoStock implementation. Historical
`ASTOCK_ROOT`, `.venv-a-stock-data` and `/root/work/astock` names are therefore not
proof of an installed upstream repository or an upstream commit dependency.

Known project/skill roots and a directory-name search under `/root` and
`/home/ctyun` to depth five found no pre-existing independent upstream checkout.
This is a bounded search, not proof of full-machine absence or of all historical
code ancestry. The existing system has no recorded old simonlin commit that can
honestly be presented as an old-to-new package upgrade.

## Updated upstream reference and applicable changes

The complete new reference checkout is `/srv/agentdock/projects/a-stock-data`,
from [the upstream repository](https://github.com/simonlin1212/a-stock-data), pinned
to `cb57c7aba720380b48a11dd065c6db57a2c39db9` (main, verified 2026-10-29).
Its embedded SKILL version is **3.10.3**; the latest published GitHub release is
**v3.10.2**. These are different version signals. Upstream delivers executable
examples inside `SKILL.md`, not a package dependency that this daily job imports.

| Reference | SHA256 |
| --- | --- |
| `SKILL.md` | `a774151626a5389b1eaf45d5d38f83314e96826bdf20fd25057fe4ca5bb6ccb8` |
| `CHANGELOG.md` | `2adf3fb219e65d97d33b9534d647db6f87b6bbad9236d4fe104c9912c7a81abf` |
| `LICENSE` | `199bab40163ad21135c2c4d5db43bffdadb14ed80762b048f04931cc48700bbf` |

The comparison baseline is now explicit. Shared vendor names below establish
capability overlap, not byte-identical ancestry or independent corroboration.

| Upstream capability/change | Existing Quanthus surface | Update disposition |
| --- | --- | --- |
| BaoStock daily/basic/valuation | `lane_runner`, `providers/daily_history.py`, `providers/valuation_history.py` | Separate governed implementations; retain existing source/PIT contracts |
| Tencent K-line units and session boundaries, v3.10.1 | `providers/tencent_m1_normalize.py`, `p10_native_m1_shard_capture.py`, native 5m code | Applicable reference for future adapters; do not replace source-native units/windows with tutorial output |
| SSE e-interaction relative times, v3.10.3 | No `sse_e_interaction` / `_sse_time` implementation found in current `src`/`tests` | Latest reference retained; no corresponding production patch to apply |
| `full_valuation()` / `digest_years`, v3.10.2 | No matching valuation-digestion function found in current `src`/`tests` | Latest reference retained; no corresponding production patch to apply |
| Financial statements / F10 | `providers/fundamentals.py` uses Sina statements and TDX F10 | Compare real API/backend and fields per capability; no automatic tutorial replacement |
| CNINFO disclosures | Existing `lane_runner` announcement collector | Supplementary HTTPS index sample tested separately; no silent transport or time-schema change |
| News, macro and other upstream layers | Not established as current daily inputs | New candidate purposes require their own source and consumer contracts |

No active image, deployed code pointer, timer, production environment, existing Base or
warehouse data was changed. Recovery shares the container release; the runbook
describes initial Weekly financial wiring and explicit held domains, not full
Weekly acceptance. A complete latest reference checkout alone cannot upgrade any
of these runtime roles.

## Qualification contract and evidence location

Delivered archive SHA256:
`e2da28fdc62e745370a6bd33a070778d9681be791351997de9b66b8cacec6e60`.
All 148 delivery files and 88 Python source files were checked. The offline
assessment reproduced empty-success, lost timestamps, implicit suspension,
transport downgrade and integration defects; the supplied skill is not admitted
as a whole. Individual raw capabilities can still be useful.

The job-local shared inventory has **3,363 declaration occurrences**, including
2,334 compressed institution rows, JSON URLs, static code URL definitions and
explicit functional probes. These are not 3,363 independent data vendors.
The first pass has **660 unique requests**; identical request identities share
one immutable response. No website, proxy, example URL or API base becomes a
financial data candidate merely because it appears in that denominator.

Before network access, freeze method, URL, parameters, expected response schema,
sampling purpose and evaluator hash. Use at most four workers, one active request
per host, no retries, TLS verification, 3-second connection/5-second read timeout,
256 KiB response cap, at most three redirects, and a 1,800-second new-request
scheduling limit. Each redirect is separately recorded. No ambient proxies,
credentials, login sessions or fallback vendors are used. The second API tranche
adds 13 separately frozen, sequential requests; explicit HTTPS adaptations are
labelled separately from original HTTP declarations.

| Disposition | Meaning |
| --- | --- |
| `CANDIDATE` | Observed sample meets a named, restricted purpose; not production admission |
| `REJECTED_THIS_ROUND` | Missing endpoint, identity conflict, missing expected payload, or other explicit exclusion; not proof of permanent vendor death |
| `UNDETERMINED` | DNS/connection/timeout/access limits, truncated response or incomplete functional contract; no candidate admission |

Institution website title matching only supports site discovery. Quote candidates
are diagnostic samples, not universe/history/volume qualification. Macro values
are current-vintage observations, not historical release vintages. Disclosure
indices require attachment/body/time work before historical admission. News is
discovery from acquisition time; media reports do not establish official policy
publication. Known 招商银行→民生银行 domain conflicts are rejected explicitly.

The completed screening made **673 distinct request specifications** across the
two tranches. Across **3,376 declaration/capability occurrences**, the ledger has
126 candidate occurrences, 1,930 exclusions this round and 1,320 unresolved
occurrences. Exclusions include **1,913 institution rows without any endpoint**;
they are not 1,913 observed dead websites. Unresolved entries include **661 static
code URL occurrences without a complete functional contract**. Thus the census
is dispositioned, but full online functional acceptance is not complete.

After deduplication, there are **14 functional sample candidates** and **109
website-discovery candidates**. The 14 functional capabilities are individually
retained as follows:

| Candidate capability | Sample evidence | Allowed candidate purpose |
| --- | --- | --- |
| Tencent quote | One security, matching identity/price/source time | Quote diagnostic |
| Sina quote | One security, matching identity/price/source time | Quote diagnostic |
| Eastmoney quote | One security, matching identity/OHLC | Acquisition-time quote diagnostic |
| Eastmoney CPI | Two dated numeric records | Current-vintage macro inspection |
| Eastmoney PPI | Two dated numeric records | Current-vintage macro inspection |
| Eastmoney PMI | Two dated numeric records | Current-vintage macro inspection |
| Eastmoney GDP | Two dated numeric records | Current-vintage macro inspection |
| Eastmoney M2 | Two dated numeric records | Current-vintage macro inspection |
| CNINFO HTTPS announcement index | Two IDs/timestamps/attachment paths | Disclosure-index inspection |
| CNBC declared RSS | Thirty titled linked records | News discovery |
| ChinaMoney LPR | Dated positive 1Y/5Y observations | Current-vintage rate inspection |
| ChinaBond treasury yield curve | Dated named curve and numeric points | Current-vintage curve inspection |
| Sina rolling news, lid 2516 | Titled linked timestamped records | Macro/news discovery |
| Sina rolling news, lid 2510 | Titled linked timestamped records | General news discovery |

CLS `/api/sw` returned 404 in this attempt. Two Eastmoney policy-news requests
and the tested SSE/BSE schemas did not return the declared functional structure.
Two built-in RSS requests returned non-RSS content. These exact declarations are
excluded this round. WSCN DNS, SZSE HTTP status, oversized report/RSS responses
and other access/network limits remain unresolved. None authorizes fallback
content to be labelled as the original vendor's successful response.

External evidence root on CN02:
`/srv/agentdock/incoming/cn-financial-scraper-1.0.51-20261029T223405/evaluation/`.
`P10-EVALUATION.md` and `offline-results.json` retain the offline findings.
`online-20261029/frozen-inventory.json` SHA256 is
`74841b71717577eefa2a2ab238f27185300e968fdc968b35a5ad2468d99ac3a9`;
its `observations.jsonl`, `raw/`, `dispositions.json`, `candidates.json` and
`summary.json` retain first-pass evidence. `api-tranche-2/` retains the second
manifest, observations and offline-adjudicated dispositions. A parser exception
handling correction was replayed against the same 13 responses without another
network request; no observed disposition changed, and a malformed-rate synthetic
case now explicitly rejects. Both evaluator versions remain available.
`purpose-limited-candidates.json` combines the candidates with individual request
IDs, body hashes, acquisition clocks, purposes and explicit no-admission flags.
`combined-summary.json` and `evidence-index.json` bind both tranches. These files
are the local candidate area; the summary table is its repository review index.
Candidate-ledger SHA256:
`f131c0d0b4c63ea9cdb1290848537991cc839a98d0ce5223ca5eb1f8c54d9377`.

The runtime lineage receipt is
`/srv/agentdock/projects/Quanthus/.tplan/p10-source-qualification-20261029/reports/runtime-lineage.json`,
SHA256 `656756cc71f396e3e389bc554e650c4618be82fb5f897ee9e9c45dec03301d13`.
These are retained local evidence, not raw data committed to Git or an ADM product.

## Current source-library verification — 2026-10-30

The public library independently implements 14 request contracts in `src/astock`;
no delivered scraper source code is copied. [astock PR #1](https://github.com/rv198-star/astock/pull/1)
merged as `8ce0c5c844d88b56ff7092e2575757c463e698d3`; candidate prerelease
[sources-v0.1.0](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.0)
pins the exact tested source commit and distributes the wheel/source/checksums.
All original candidate bodies replay
with exact body hashes and equivalent method/URL/form-or-JSON fields. A separate
canary froze 14 requests, no retries, verified TLS, no proxy inheritance and a
256 KiB response cap. Its current dispositions are:

| Capability | Original replay | New canary | Candidate use ceiling |
| --- | --- | --- | --- |
| ChinaMoney LPR | 12 rows | 12 rows, pass | Effective-date rate observation; no inferred publication clock |
| CNBC RSS | 30 items | 30 items, pass | Publisher feed discovery |
| ChinaBond treasury curve | 505 points | 505 points, pass | Exact curve identity/source date; native units pending consumer mapping |
| CNINFO index | 2 rows | 2 rows, pass | Raw index, exact source milliseconds; no document bodies or historical admission |
| Eastmoney CPI/PPI/PMI/GDP/M2 | 2 rows each | 2 rows each, pass | Current-vintage named native numeric fields; no inferred release clocks |
| Sina 2516 | 2 items | 2 items, pass | Publisher news metadata; no official-policy authority |
| Sina 2510 | 2 items | 2 items, pass for archive metadata | Latest observed creation time is 2025-05-26 UTC; exclude current-policy coverage |
| Tencent quote | 1 snapshot | 1 snapshot, pass | Single-security native quote diagnostic |
| Sina quote | 1 snapshot | 1 snapshot, pass | Single-security native quote diagnostic |
| Eastmoney quote | 1 snapshot | `REQUEST_FAILED`, hold | Prior schema evidence retained; current availability not granted |

These are 13 current purpose-limited sample candidates and one transport hold,
not whole-vendor, numerical-consumer, historical-PIT or production admission.
The earlier 673-request census/dispositions remain immutable; this canary is an
additional dated observation rather than a new independent-source denominator.

Environment/build evidence is outside Git at
`/srv/agentdock/evaluations/astock-20261030/`:

- `build-dependency-manifest.json`: 13 PyPI wheel hashes/versions; bootstrap pip
  23.0.1 comes from the existing pinned Python image
  `sha256:db6ee5170ebce0f4d165af242e8e77addc08bd9ffc37e8b1815752cf027aa4f8`.
  A partial official pip download was not executed. No shared runtime was modified.
- `library-replay.json`, `live/plan.json`, `live/observations.jsonl`, retained bodies
  and `candidate-ledger.v1.json`: per-contract status and evidence. The canary ledger
  SHA256 is `7a1f10541ea6b9fd6630ace338e0e20fd4b0b70994a12b616be8dc1188ce20c7`.
- `wheel-verification.json`: two fixed-epoch builds produce wheel SHA256
  `b1896aabced17fcb1761121f9a1fce8a40073495185d2a9ea90a79e3398df646`.
  A fresh install outside the checkout runs all **30 maintained tests** successfully.
  Full original-plus-library suite: **221 tests, four opt-in live skips, no errors**;
  legacy pandas is reused read-only, openpyxl is installed in the isolated overlay.
- Mission logs retain commands/results. Ruff passes for all maintained source/tests.
  Quanthus full `make check`: **5,699 passed, 20 skipped**, 605.11 seconds; doctor,
  compile and planning/blueprint/exploration checks pass. The previous free-space
  failure is resolved without changing its 8 GiB floor. The documentation-only
  follow-up retains all 1,711 executable/config/test closure files byte-identically;
  79 affected documentation/P10 contract tests and planning checks also pass.

## CN02 disk and cleaner audit — 2026-10-30

The running `quanthus-data-cleanup-1` uses the active pinned pipeline image, has
exactly one bind at `/srv/quanthus-base22-data/tmp/p10` and no network. Five retained
sweep logs cover Shanghai 03:30 on October 25–29; October 26–29 removed **nine**
eligible directories. `.p10-cleaner.json` records the October 29 slot. Four failed
jobs are correctly retained. The entire TMP root is only **104 KiB**. Historical
logs have directory counts but no byte counters, so historical reclaimed bytes
cannot be reconstructed from them. The scheduler is effective within its declared
72-hour successful-TMP scope; it does not manage images, test scratch or evidence.

The filesystem had 8,351,113,216 free bytes before this cleanup. Six old P10 local
build images were unreferenced by all containers and current deployment configs;
normal non-force image removal reclaimed **927,772,672 bytes**. Current and recent
rollback images, all containers/volumes, P6, source trees and formal data were kept.
After full tests, this task's own completed `/tmp/pytest-of-agentdock/pytest-519`
synthetic scratch was removed with its test log retained. The receipts are
`disk-cleanup-plan.json`, `disk-cleanup-receipt.json` and
`test-scratch-cleanup-receipt.json` in the evaluation directory. Read-only synthetic
publication fixtures required restoring owner-write on their own test directories
before final removal; production paths were not involved. Final observed free
space is 9,201,885,184 bytes (about 8.6 GiB); the test environment and audit evidence
remain retained.

Other retained `.codex` work/evidence consumes about **23 GiB**, including prepared
research inputs. It was not age-deleted or relabelled disposable. Further recovery
there requires an exact lifecycle/dependency disposition through P10, not widening
the current cleaner mounts or deleting old research evidence by name. Findings are
routed to the existing container lifecycle issue #537, without opening a duplicate
cleaner-defect issue or changing the running schedule.

## P10 integration and next source tranche — 2026-10-30

The current consumer is P10's own supplementary observation and supplier
qualification, governed by the [source-subproject design](../design/astock-subproject.md).
`p10_source_contracts.py` and `p10_supplement.py` integrate fifteen separately
identified capabilities through the fixed optional `supplement-sources` dependency.
The versioned policy and per-field primary evidence are in
[`p10-supplement-sources.v1.json`](../../config/pipelines/p10-supplement-sources.v1.json).
The separate finite-watch image/runbook is in [`ops/source-watch`](../../ops/source-watch/README.md).

The added NAV adapter was independently implemented and merged in
[astock PR #2](https://github.com/rv198-star/astock/pull/2).
Exact tested source `8360132de1255a68e42df8a1aa142f1335bd68e7` and merge
`34f1c8492705c7a22b4bee8a05b73b33c505e3fd` have the same tree.
[sources-v0.1.1](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.1)
distributes the reproducible wheel, source archive and checksums. Wheel SHA256:
`49b04b404016fa5432acb12a1bbfdd5a2a4268adbd3a4e44a1f16adc53b1eebf`.
The maintained synthetic suite passes 37 tests; the full retained upstream plus
library runs 228 tests with four opt-in live skips. An initial legacy-environment
attempt lacked pandas; the successful run uses the isolated environment plus the
existing read-only test dependency closure. Quanthus's 28 targeted contracts cover
units, periods, clocks, optional failure/disable, prior snapshot preservation,
cutoff/hash checks, daily reuse and finite watch non-renewal.

Quanthus full `make check` passes: **5,750 passed, 20 skipped**, 618.66 seconds;
doctor, compilation and planning/blueprint/exploration checks pass. The first
attempt had two subprocess import failures because the fresh test environment
had not installed Quanthus. After an isolated editable install, those two cases
pass independently and the complete check passes. No production logic or test
gate was changed to repair that environment. Both full-check logs are retained.
Ruff and strict mypy pass for the new production modules; 79 affected document/P10
contracts pass separately.

The fixed source request was replayed from the retained NAV response without
another financial request. The source's actual `SGZT`/`SHZT` fields are validated;
the delivered wrapper uses different names and would lose these statuses. Fund
manager metadata confirms RMB for this exact fund. Unit NAV and accumulated NAV
retain different bases; a valuation date supplies no release time. The source
response does not normally echo the fund code, so identity is explicitly
request-bound rather than claimed independently proven.

The next manifest froze nine functional requests, each bounded to two rows/one
security where applicable, with TLS verification, 256 KiB ceiling, 20-second
outer deadline, no retry or automatic fallback. Original manifest and responses
remain immutable. All times below are **2026-10-30, Asia/Shanghai (UTC+08:00)**;
the machine ledger retains exact UTC and Shanghai timestamps and body SHA256.

| Request / package function | Verified at | Disposition | Quality and reopening condition |
| --- | --- | --- | --- |
| Eastmoney fund NAV / `get_fund_nav` | 00:54:31 | PASS, P10 observation | Two valuation rows; NAV currency/units and status fields checked; absorbed into astock/P10 and finite watch |
| Eastmoney margin / `get_margin_trading` | 00:54:32 | REJECT this request | HTTP 200 with source business error 9501, null result; corrected vendor-supported request needed |
| Eastmoney block trades / `get_block_trades` | 00:54:32 | REJECT this request | Same explicit business error; no rows or unit claim; corrected contract needed |
| Eastmoney billboard seats / `get_dragon_tiger_seats` | 00:54:32 | REJECT this request | Same business error; a quote fallback cannot establish seats |
| Eastmoney executive holdings / `get_shareholder_changes` | 00:54:33 | REJECT this request | Same business error; corrected request and actual field definitions needed |
| Eastmoney announcement / `EastMoneyReportAPI.search_reports` | 00:54:34 | HOLD access | HTTP 200, zero bytes; not an explicit empty list; changed/documented public access needed |
| Eastmoney ETF flow / `get_etf_fund_flow` | 00:54:34 | HOLD units/basis | Two structured rows and source epoch seconds; per-field monetary/flow basis unproven, declared evidence page `/etf/` is 404 |
| Eastmoney northbound / `get_northbound_flow` | 00:54:35 | REJECT this purpose contract | Four source fields versus eight assumed by wrapper, ambiguous market aliases; do not admit zeros as current flow |
| Jisilu convertible list / `get_convertible_bonds` | 00:54:36 | HOLD access | HTTP 200, zero bytes; controlled public-session diagnostic at 01:12 found bootstrap `/cbnew/` HTTP 404 and stopped |

These rejects apply to the observed request/purpose, not to every service of a
vendor. Access holds remain unknown rather than permanently dead. The Jisilu
diagnostic adds one homepage request, performs no second POST, login, TLS downgrade
or challenge bypass. ETF schema success is retained, with numerical admission held
until primary unit and definition evidence exists. No identical failed request
is repeatedly retried. The source declaration census is not rebased by this tranche.
The four shorthand function labels in the frozen manifest have explicit origin
errata in the disposition ledger; request/body evidence is unchanged.

An additional **static contract screen**, separate from financial online results,
checked all thirteen `structured_market_data.py` AKShare routes against primary
upstream source `akfamily/akshare@a14a7830c5b9a763230fcfb8ab0b3c9107716dc7`.
Three named functions are not exported; three no-argument macro functions conflict
with the package's `start_year` defaults. The package's fallback drops those
arguments or switches backend, so it cannot prove the requested window. Two routes
reuse already covered Eastmoney macro backends. Five remaining signatures require
an explicit bounded source request/universe before runtime qualification; no
whole-market or unrestricted history call was made. The reference modules and
per-route dispositions are in `akshare-reference/contract-triage.json`. These
thirteen rows count as no financial online passes and introduce no new dependency
to the observation runtime.

Current artifacts are outside Git at
`/srv/agentdock/evaluations/p10-admission-20261030/`:

- `normalization-replay.v2.json`: all fifteen retained observations through the
  current pinned adapter, standardized units/time and quality; fourteen usable
  for their limited purpose and the retained Eastmoney quote transport hold.
- `remaining/plan.json`, individual response metadata/bodies and
  `remaining/candidate-ledger.json`: all nine dated dispositions and primary-unit
  evidence, plus `jisilu-session.json` for the sole controlled diagnostic.
- `astock-dist-a/` and `astock-dist-b/`: identical wheel builds;
  `astock-check-v2.log`, `astock-check-upstream-v3.log` and Git publication receipt.
- `journal-before.json` and `journal-cleanup-receipt.json`: removal of ten archived
  2024 OS journal files under a 365-day retention bound, 234,881,024 bytes. All
  2026 Quanthus/container logs, formal data, volumes and rollback images remain.

The finite watch persists a seven-day budget and daily checkpoints. Candidates
remain observations while it runs; earliest review requires enough distinct-day
and recent consecutive evidence. Stopping or selectively disabling the supplement
is the rollback. Frozen research/business consumers keep their existing gates.
The P10 epic and this source-supply issue group remain open for actual outstanding
qualification and consumer-specific decisions.

## First integrated observation and operational rollback — 2026-10-30

[Quanthus PR #590](https://github.com/rv198-star/Quanthus/pull/590) carries the
integration. The source-watch image consumes the already published exact source
`d335c1a3d3ad2197340f0ef4c66f0bced657070c`; GitHub and local Git tree identities
were checked before exporting tracked source/config only. The offline build uses
Python 3.10.22 and the hash-locked runtime wheels. Its image identity is
`sha256:ca5f2f0c4e4c5a8bc1c31712375b1d36847d2e03b19d3f010b59ad825a9a9c96`.
All bundle files and installed astock modules are verified during the build.
Local full checks use Python 3.10.12. Python 3.12 is unavailable locally; hosted
jobs did not start because of the account billing/spend limit, as confirmed by
actual check annotations. This is a hosted-CI availability limit, not an executed
hosted test. Local acceptance follows the root AGENTS Owner rule of 2026-10-29.

The network-disabled container check marks all fifteen capabilities explicitly
unavailable with `SOURCE_DISABLED_ROLLBACK`; both initial build and same-job reuse
make **zero network requests**, retaining an identical immutable manifest.
The live watch then makes exactly fifteen requests in **20.779104 seconds**, with
**46,693 raw-response bytes and 568 normalized rows/curve points**. All fifteen
pass their named observation purpose. These counts describe one bounded sample.
All times below are **2026-10-30, Asia/Shanghai**.

| Capability | Verified at | Rows | Quality/use ceiling |
| --- | --- | --- | --- |
| `chinabond:treasury.curve` | 01:39:25 | 505 | Observed-purpose pass |
| `chinamoney:lpr.history` | 01:39:27 | 12 | Observed-purpose pass |
| `cnbc:news.rss` | 01:39:28 | 30 | Observed-purpose pass |
| `cninfo:disclosure.index` | 01:39:30 | 2 | Observed-purpose pass |
| `eastmoney:fund.nav` | 01:39:31 | 2 | Pass; fund identity is request-bound, no response echo |
| `eastmoney:macro.cpi` | 01:39:32 | 2 | Observed-purpose pass |
| `eastmoney:macro.gdp` | 01:39:33 | 2 | Observed-purpose pass |
| `eastmoney:macro.m2` | 01:39:35 | 2 | Observed-purpose pass |
| `eastmoney:macro.pmi` | 01:39:36 | 2 | Observed-purpose pass |
| `eastmoney:macro.ppi` | 01:39:38 | 2 | Observed-purpose pass |
| `eastmoney:quote.snapshot` | 01:39:39 | 1 | Pass for quote diagnostic; source clock unavailable |
| `sina:news.roll.2510` | 01:39:40 | 2 | Pass for archive metadata; excluded from current-policy coverage |
| `sina:news.roll.2516` | 01:39:42 | 2 | Observed-purpose pass |
| `sina:quote.snapshot` | 01:39:43 | 1 | Observed-purpose pass |
| `tencent:quote.snapshot` | 01:39:44 | 1 | Observed-purpose pass |

Eastmoney quote has recovered for this new dated observation; its earlier
transport failure remains in the original ledger. All three quote adapters report
the same normalized price unit and security identity; this sample's last prices
agree. Their clocks differ and Eastmoney's remains unknown, so this comparison
selects no provider priority or automatic business fallback. Sina 2510's retained
old item is still archive-only. First-observed acquisition time remains the
knowledge floor for every source.

After publication, stopping the new watch container leaves its manifest intact.
Restart resumes the original next-at/end-at times, produces no second Job or
source request, and keeps its sole writable observation bind. The finite end is
**2026-11-06 01:39:23 Asia/Shanghai**. Three distinct successful dates and recent
three consecutive successful dates permit review then; a first sample is not
completion of that period or business acceptance.

Operational receipts outside Git are `watch-image-receipt.json`,
`watch-rollback-receipt.json`, `watch-launch-receipt.json` and
`watch-first-cycle-receipt.json` under the current evaluation root. The live
`candidates.json` SHA256 at this checkpoint is
`6a3935f75224f8725b7c02c9d71dbde814e988c4bb44f5bdde7b2ec2e2888a29`; its first immutable manifest SHA256 is
`1eb98a14ec6bba9e5f2c0c8a13bd64c910a84c900166653d7b0f8b70865817f5`.
Run-stage metrics and all raw/normalized response hashes are retained there.

## Three access/unit holds — continued 2026-10-30

Owner explicitly continued qualification without waiting for the original watch.
Current publisher pages and their declared scripts establish corrected public paths;
all initial failures remain immutable. Five discovery pages, six declared scripts,
four primary unit/rule documents and three corrected functional calls were bounded
and frozen before access. Each call has a 20-second outer budget and byte ceiling,
with no auth, retry, provider substitution or automatic redirect following.

The source library 0.1.2 independently implements these three contracts. The new
policy `config/pipelines/p10-supplement-sources.v2.json` explicitly selects them
without requesting the original fifteen capabilities. See the governing child
[additional qualification design](../design/astock-subproject.md#additional-access-and-unit-qualification--2026-10-30).
The `supplement-sources-v2` extra and `requirements-v2.lock` use a separate runtime;
the existing 0.1.1 image/root/policy/deadline stay fixed.

All times below are **2026-10-30, Asia/Shanghai**.

| Corrected capability | Verified at | Result | Remaining purpose limit |
| --- | --- | --- | --- |
| Eastmoney announcement index, `/api/security/ann`, stock 600519/page1/size2 | 02:01:29| Two correct-security records; archive metadata admitted | Latest notice label 2026-08-15 (76 days old); current-disclosure completeness and historical release clocks unproven |
| Eastmoney ETF 510300, one exact `secids` via `ulist.np/get` | 02:01:30| One identified ETF; signed CNY trading-flow buckets and vendor percentages admitted for diagnostics | Secondary-market order classification, not creations/redemptions or verified investor positions; original unqualified f20 omitted |
| Jisilu current anonymous `/webapi/cb/list/` | 02:01:30| 30 anonymous records;29 C convertibles with qualified reference units,1 E exchangeable metadata-only | Login required for full list;4 missing row time texts and3 missing listing dates; no full-universe or actual-trade claim |

The original announcement path `/security/announcement` and old Jisilu POST/
bootstrap definitions remain failed historical contracts; a working new route
never rewrites them as passes. Unsupported current-disclosure/full-anonymous-list/
ETF-subscription-flow interpretations stay explicitly unavailable. Reopening needs
new purpose-specific recency, access/coverage or creation/redemption evidence,
not a repeat of an unchanged failed request.

Primary pages and field bindings resolve ETF money as CNY and percent values as
native percentages; normalization maps percentages to ratios once. Official SSE/
SZSE article 6 gives convertible quotation per 100 face; issuer disclosure confirms
conversion-price denomination CNY/share. This verifies denomination, not each
current conversion price. All unit sources have exact URLs/times/body hashes in
the policy. Exchangeable numerics are kept outside that convertible unit admission.

Notice-date labels and native `display_time`/`eiTime` text never backdate knowledge.
Jisilu's `info.date` is not joined to its row's time-only `last_time`: the context
can already show the next calendar day at midnight. Missing listing dates remain
unknown, and reference prices do not establish a trade or fill. ETF epoch seconds
stay a separate vendor claim. Knowledge begins at complete-response acquisition.

The new adapters pass 51 maintained synthetic tests. The retained upstream plus
library runs 242 cases: 238 pass and 4 opt-in live cases skip. An initial full-upstream
attempt lacked openpyxl; the successful run reuses the existing isolated read-only
spreadsheet dependency closure. Shared environments were not changed. Two pinned
wheel builds are byte-identical, SHA256
`5311abc0c2ecfd89be787c4fda4ba7edd03f9079ab9954ce026cf9b80a86f64d`;
installation outside the checkout passes all 51 maintained tests. The new P10
unit/time/subset contracts plus existing supplement tests pass 37 cases; local
Ruff and strict mypy pass for the changed production modules.

### Installed-container integration and recovery

[Quanthus PR#591](https://github.com/rv198-star/Quanthus/pull/591) publishes the exact
new adapter source `f2247db5d35e60ced2e849cadb38d68cbf6298da`. GitHub's Git tree was
verified before making the tracked-only source bundle. Offline construction uses
the pinned base image, fixed 0.1.2 wheel and five hash-locked transport wheels;
the build verifies the bundle and every library module before completing. Image
`sha256:f1b0b77f5966b7c77e244e774fb716b8ed35d3611a8bcf62f27772cd7f5eeba5`
reports53,765,863 bytes in image-inspect Size (not unique disk usage). The new watch runs as 998:998 with a read-only rootfs, dropped
capabilities, bounded resources/logs and one writable qualification root. Its
source, policy and package differ from the original watch, which stays fixed.

A separate three-call cold-integration plan was frozen before launch (SHA256
`529772226ae33d5ee963664cceca15859859ea5af47882a43b3cc68632424077`).
The actual first cycle has 3 requests, 33 normalized rows and 40,473 raw bytes in
5.077963 seconds. Raw/parsed/normalized prerequisites are built once per source
and shared by the quality and ledger consumers; timings/rows/bytes/BUILD are in
the immutable manifest. Latest actual acquisition times are all 2026-10-30 Shanghai:

| Capability | Installed-container verified at | Rows/quality |
| --- | --- | --- |
| Eastmoney archive announcement index | 02:34:36.897653| 2; correct 600519, notice-age 76 days, archive-only and release-clock flags retained |
| Eastmoney ETF trading-flow diagnostic | 02:34:38.261492| 1; correct sh510300, CNY/ratio mapping, vendor epoch clock separate from knowledge |
| Jisilu anonymous partial reference | 02:34:39.960405| 30; 29 C-qualified reference units and 1 E-metadata-only, unknown actual quote clock |

All rows retain complete-response acquisition as `knowledge_from`; reading even
one microsecond before that acquisition fails `SOURCE_NOT_KNOWN_AT_CUTOFF`.
Stopping and restarting the new container keeps the manifest, next-at, end-at and
one Job unchanged; no second request occurs. The original fifteen-capability
container/image/root, manifest, candidate ledger and schedule were compared before
and after and remain identical. The new first manifest SHA256 is
`e758c716b08ee7f34a5043e14b1f8a5fbf4aeb6d3f3a47314f3e9b950cfbca2d`;
the candidate-ledger SHA256 is
`8a900374dd259b7fb99eb25cd112d8ecb22ca876cdbf866ad515ac467b79403d`.

With network disabled, the actual image's all-disabled first Job and same-Job
reuse both make zero requests and explicitly report `SOURCE_DISABLED_ROLLBACK`.
A second offline test copies the published first live snapshot into an isolated
scratch root, then disables all three. Past-good references remain auditable,
but current reads fail `SOURCE_CURRENT_OBSERVATION_UNAVAILABLE`, every candidate
isHOLD, and no old good value is silently reused. The real live root is unchanged.
Restart/reuse never renews either watch period or pads distinct successful dates.

Complete local `make check` on the exact source passes **5759 tests / 20 skips**,
including planning/blueprint/exploration checks, in 634.60 pytest seconds. The host
uses Python 3.10.12; optional research/runtime tests remain skipped as logged. Hosted
jobs did not start because of account billing/spending limits; actual annotations
are retained. Local acceptance uses the Owner 2026-10-29 AGENTS instruction without
workflow/protection/account changes or forged hosted status. Later documentation
reuses the byte-identical executable/config/test closure and reruns its affected
checks. Only completed synthetic pytest 531 TMP was removed after process exit,
recovering 603,193,344 filesystem bytes; full logs and receipts remain outsideTMP.

Main advanced during this work, so the branch also integrates exact main
`d6c01cde9941ec3f30a3806a198e18f39090758e`. Complete local checks on integrated
source `cfadba190d0c0ed3b96826e2f657f9717202ad11` pass **5771 tests / 20 skips**
in 615.19 pytest seconds, with doctor/compile/planning checks also passing. The
1,800 non-documentation source/config/test files are bound in
`integrated-tested-closure.json`. P10 runtime files, selected policy, library and
request identities remain identical to the independently running f2247 image.

The first integrated attempt had 5770 passes and one environmental failure,
`FREE_SPACE_FLOOR_REACHED`: default pytest retention accumulated 577 MiB of successful
synthetic TMP before the existing 8 GiB gate. Its log is retained. Only that exited
synthetic scratch was removed; the corrected full run uses pytest 8.4.2 options
`-o tmp_path_retention_policy=failed -o tmp_path_retention_count=1`, reclaiming
successful fixture TMP after teardown and retaining failures. Test selection,
source/config/test bytes and the 8 GiB threshold are unchanged. Observed late-run
scratch was about 16 MiB with about 8.38 GiB free; after completion, free space is
9,016,180,736 bytes. The explicit command and full log/closure hashes are retained
in `integrated-retry-command.json` and `integrated-verification-receipt.json`;
the operator command is also in the source-watch README. This check generated
zero additional financial source requests. Further documentation changes reuse
this exact 1,800-file closure and rerun documentation/planning checks.

The remaining five pinned AKShare calls now have an explicit budget disposition.
`futures_zh_daily_sina` has no date/count request bound; the FX-reserve helper loops
back through history; HK/US spot wrappers paginate the whole market. These four
original call contracts are rejected for this latest-only sample budget, with no
claim that their vendors are unavailable. `futures_zh_spot` remains held until one
current listed contract, exchange, units and useful P10 purpose are fixed. This is
static source evidence with zero financial requests, not five additional online
passes. The pinned upstream SHA and exact code lines are in the separate receipt.

Evidence outside Git: `/srv/agentdock/evaluations/p10-holds-20261030/` retains
`discovery-plan.json`, `script-plan.json`, `functional-plan.json`, the two unit
plans, each raw response/metadata, `candidate-ledger.json`, independent build and
test logs. The added `watch-*-receipt.json` files record the image, zero-network
rollback, launch, first live cycle and disable-after-good test; `quanthus-verification-receipt.json`
binds complete local checks to source/log hashes. The original 673-request census
and all prior ledgers are unchanged.
#583/#584/#585/#587 continue to own remaining functional coverage and later review.

## Earlier verification checkpoint — retained history

The upstream full offline suite ran 191 tests with four live tests skipped; it
reported 14 errors from missing `openpyxl` in the inspected runtime. It must not be
called a full pass. The runtime has no pip; the failed dependency-install attempt
did not modify it. Targeted time/valuation regressions and local repository checks
are recorded separately in the mission logs: **14 targeted upstream tests passed**;
**74 documentation/P10 contract tests passed**; planning/blueprint/exploration
checks passed. `make check` ran with the existing Python 3.10 environment:
**5,698 passed, 20 skipped, one failed**. The failure was
`test_verification_cli_accepts_successor_and_keeps_legacy_options_explicit`:
the unchanged verification CLI requires 8 GiB free and the host filesystem had
about 7.3 GiB available (`FREE_SPACE_FLOOR_REACHED`). No free-space gate was
weakened and no unrelated files were removed. This is a failed full check, not a
full local acceptance. The executable/config/test closure is unchanged from
base `cc273564`; this change only adds work-item evidence and routing.
Synthetic qualification cases cover
valid and malformed RSS, wrong institution identity, truncation, rate limiting,
and malformed numeric API fields.

Remaining admission work must name the consumer benefit and bounded request before
expanding samples. Do not endlessly retry identical unavailable endpoints. A
failed optional source does not block independently qualified daily products.
The current source inventory, candidate ledger and issue group provide recovery;
they do not close the full functional-qualification work package.

## Named futures and remaining bounded replacements — 2026-10-30

Owner continued #581/#583/#584/#587 with valid futures contracts, unit/time proof
and bounded online qualification. No seven-day wait is a predecessor. Evidence
root: `/srv/agentdock/evaluations/p10-futures-20261030/`; original failures,
frozen plans and raw hashes remain outside Git.

| Capability | Actual verification (Asia/Shanghai) | Disposition and quality |
| --- | --- | --- |
| Sina DCE:V2701 price snapshot | 2026-10-30 05:08:26 | PASS for vendor contract-price diagnostics: one row/241 bytes, native CNY/tonne, 5 tonnes/lot separately; native date/time labels uncombined; no real-time or trade-day admission |
| Eastmoney HK00700 exact-security replacement | 2026-10-30 05:11:15 | HOLD_LOCAL_TRANSPORT_FAILURE; public page and currency/clock field definitions found, but actual snapshot request failed locally; no vendor-outage claim |
| Eastmoney US AAPL exact-security replacement | 2026-10-30 05:11:16 | HOLD_LOCAL_TRANSPORT_FAILURE; same bounded treatment, no login/retry/fallback |
| Jin10 attr76 latest first page | 2026-10-30 05:11:16 | REJECT_CURRENT_PURPOSE_STALE_VINTAGE: 20 rows/830 bytes, explicit USD100 million units; latest label 2025-09-07 has null actual and latest non-null actual is 2025-08-07 |

The four original unbounded wrappers retain their previous exclusion reasons;
these new single-security/one-page requests do not erase that history. Sina's
unbounded daily-history wrapper remains excluded without a repeated request.
DCE-host spec/guide references timed out; a DCE-authored PDF on a member's site
provides units but does not independently confirm today's listing. The passed
sample is explicitly vendor-listed and unexpired, with a conservative stop before
its delivery month, under the [v3 contract](../design/astock-subproject.md).

This tranche made four functional requests: one diagnostic pass, two local
transport holds and one current-purpose rejection. Website/script discovery is
counted separately and is not functional qualification. Full census functionality
remains open; an accessible stale series does not become a useful archive product
without a named consumer.

### Futures installed-runtime receipt

The standalone `quanthus-p10-futures-watch` runs locally on CN02 with published
source `3ed3fe4ba40549244e634c428caaecc5fc52e97b`, image
`sha256:b08a193c9a2b743731b65428a6c32f40c27c0a82c337d0e0b284d9ba8d5d607b`,
and its sole writable bind `/srv/quanthus-source-watch/futures-3ed3fe4ba405`. First cold integration completed at
**2026-10-30 05:20:26 Asia/Shanghai**, with one request, one row and 241 bytes
in 1.9112 seconds. The manifest SHA256 is
`bd6f57d62f3aaa81e52e490d012cea9a85ead30f0a16ad2682352ac285c12df1`; the candidate ledger SHA256 is
`fd9f3c08f0195d8526b5c9dbef4f3e3b3d57560b95ff36a07f1f1b563089cc89`.

The persisted next cycle is **2026-10-31 05:20:25 Asia/Shanghai**; the observation
period ends **2026-11-06 05:20:25 Asia/Shanghai**. Only one distinct successful
date exists. First functional and installed-container samples do not pad days.
Actual stop/restart preserved the manifest and deadline without another HTTP
request. Offline disable-after-success kept the prior-good reference but made
current data unavailable; exact-cutoff reading one microsecond before acquisition
was rejected. Both earlier watches retain the same images, start times, manifest
hashes, ledgers and persisted deadlines.

The existing production TMP cleaner's **2026-10-30 03:30 Asia/Shanghai** slot ran:
two eligible successful directories removed, four failed jobs retained. It
records no deleted-byte count; no byte saving is inferred. The completed local
image-build context was classified TMP and removed after its Git/image identity
and bundle manifest were retained. No shared prune or production mutation was
performed. See existing operations issue #537.

Local full validation on exact executable/config/test source
`3ed3fe4ba40549244e634c428caaecc5fc52e97b`: **5781 passed, 20 skipped**, 8 warnings,
641.66 seconds; doctor/compile/planning checks passed. Focused unit/time/expiry/
rollback tests 47 passed; installed-library futures tests 10 passed. Source-library
checks 58 passed, retained suite 245 passed plus 4 opt-in live skips; installed wheel
checks 58 passed. The two wheel builds and downloaded release agree on SHA256
`b6d6cd358164d0ea3bde7d98020c54b8001800ca4aea7c9538fa539f6925d1b4`.
Source archives have identical file contents but different metadata timestamps;
only wheel byte reproducibility is claimed.

The final documentation-only handoff preserves all 1803 tested non-documentation
files byte-for-byte and reruns documentation/planning checks. Local pytest uses
successful-TMP teardown cleanup; the test selection and 8 GiB floor are unchanged.
Hosted CI did not start because of account billing (annotations retained), so no
hosted pass is claimed. Full log SHA256:
`d9f86fab2ff531ead767f9674e801142b3019b976cbae361a6fec6406806f85b`.

Published library: [astock PR #4](https://github.com/rv198-star/astock/pull/4),
[sources-v0.1.3](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.3),
source `e626c85ee7bdd43f9b66468d2b3349a95d37477e`.
Consumer change: [Quanthus PR #592](https://github.com/rv198-star/Quanthus/pull/592).
Existing issues and the standing epic remain open; candidate observation never
automatically grants business or historical PIT admission.

## 2026-10-30 continuation: A shares first, futures evidence bounded

This continuation uses existing #581/#583/#584/#587. A shares are the highest
source priority. HK/US remains low priority with **zero** new requests: no new
access or request-contract evidence supports retrying the prior transport holds.
Evidence is retained under
`/srv/agentdock/evaluations/p10-cn-priority-20261030/`; prior roots and raw responses
remain immutable.

### Futures evidence review

Two new official DCE routes were attempted once each: the HTTPS contract-info
POST restricted to product `v`, and its declared contract-info page. Both timed
out on CN02 at **05:47:53 / 05:48:05 Asia/Shanghai**. Official V2701 listing/start/
last-trade metadata therefore remains unverified. This is a local access result,
not a claim that the exchange or the named contract is unavailable.

The [CFA explanation](https://www.cfachina.org/inv/wqbh/rxwd/202302/t20230208_36029.html),
fully acquired at **05:48:05**, supplies general exchange single-sided counting
from 2020 and lots in its example. A DCE-authored
[night/day auction Q&A reproduced by a member](https://www.bhfcc.com/customer-center-ques-details.html?id=8944)
links the 2023-05-25 night to the 2023-05-26 trading day. That text is browser
discovery evidence; CN02's full-body request timed out. A separate 2025 rules
reproduction hit the response cap and is explicitly incomplete. Neither partial
reference establishes Sina's native date role, vendor quantity scale/counting
or the 2026 holiday/session mapping.

Sina V2701 keeps its existing price-only contract. Official current listing,
night-session date binding and vendor volume/open-interest units/counting remain
open; no clocks are combined, quantities normalized or expiry extended. Reference
discovery stops at the finite budget; the work moves to A shares without another
identical futures quote or failed DCE request.

### New A-share functional samples

| Capability | Actual validation, Asia/Shanghai | Counts and disposition | Qualified units/time limits |
| --- | --- | --- | --- |
| Eastmoney SH600519 financing/stock lending | 2026-10-30 05:50:51.793109 | 2 rows, vendor total 3/pages 2 within 2026-10-26..29; bounded-reference candidate | Source sessions 10-28/27; CNY and shares, exact decimals/signed net flows; no claim of 10-29 coverage or historical publication |
| Eastmoney A-share block trades | 2026-10-30 05:50:52.067566 | 2 rows from vendor total 68/pages 34 for 2026-10-29; partial bounded-reference candidate | CNY/share, shares, CNY and already-fractional premium; one CNY 50 price-times-shares discrepancy retained; no inferred execution/complete census |

Both actual HTTP 200 JSON envelopes pass scope/security/venue/schema/count checks.
The independent parsers require the exact request and headers, first-page bounds,
finite source-native numbers and declared session scope. Source-reported errors,
empty/invalid/partial transport and missing fields never become successful zero
or a second provider. `RZRQYECZ` is excluded from numeric admission: the delivered
skill's `balance_change` label is unsupported. Unknown extra fields, including
future-horizon returns, are not admitted into metrics.

The policy fixes these dates and purposes; another session/universe is a new
reviewed scope. All publication/source-clock values are null and knowledge begins
at actual complete acquisition. The source's latest native session and the actual
validation clock are separate ledger fields. A longer access watch does not
qualify more sessions or repair discrepancies. astock `0.1.4` and P10 policy v4
absorb both functions independently of the three existing watches.

### A-share installed-runtime and local acceptance receipt

Published [astock PR #5](https://github.com/rv198-star/astock/pull/5) and
[sources-v0.1.4](https://github.com/rv198-star/astock/releases/tag/sources-v0.1.4)
use source `c33a7b7b0e110735283331e0356318679750afb5`. Two wheel builds and the
downloaded release agree on SHA256
`2880d1e261bcbc50a35296aefab7cd66ce2a4f3a7a85de2ed53835c542583ffe`.
This adds two bounded contracts, not two independent vendors or a completed
functional census. Consumer changes are in
[Quanthus PR #594](https://github.com/rv198-star/Quanthus/pull/594).

The separate local CN02 container `quanthus-p10-a-share-watch` consumes published
Quanthus source `2a630b0ed3df81ee80089d7767c0100b037a1d1c`, image
`sha256:9ee9b8f11823ae241521f7eb5ce2fc00260bd4d189074a7788675bf42170711a`,
and only writable bind `/srv/quanthus-source-watch/a-share-2a630b0ed3df`.
Its first cold cycle completed at **2026-10-30 06:16:00 Asia/Shanghai**:
two requests, four rows, 3953 raw bytes and 3.171887 seconds. Actual complete
acquisition was **06:15:59.283353** for margin and **06:16:00.521474** for
block trades. The same source-session limits, partial-page counts and reported
amount discrepancy remain visible. Manifest SHA256:
`158bc616af1ae804092c1c379a15ce97dec23eea346c700390a80807245792b0`;
candidate-ledger SHA256:
`1f90918964ac610966c56ced3c4f59d6ffba1d8b9ad8718effd503fd8be6e5e6`.

The next cycle is **2026-10-31 06:15:57 Asia/Shanghai**; the nonrenewable
period ends **2026-11-06 06:15:57 Asia/Shanghai**. There is one distinct
successful access-observation day. Fixed-date reaccess does not qualify later
native sessions or latest-session coverage. Actual stop/restart preserved the
deadline and manifest with zero extra requests. Offline disable-after-good kept
old evidence for audit and rejected current reads; a cutoff one microsecond
before actual acquisition was rejected. All three prior watches retained their
images, start times, manifests, ledgers and deadlines. This is qualification
infrastructure only; business, historical PIT and production admission remain
unchanged. Remaining sources continue without waiting for this period to end.

Local `make check` on runtime source `2a630b0ed3df81ee80089d7767c0100b037a1d1c`
passed **5791 tests, with 20 skipped and 8 warnings**, in 621.31 pytest seconds;
doctor, compile and planning checks passed. Focused P10 tests passed 57; the new
v4 installed-library contract tests passed 10. astock library tests passed 70,
retained compatibility tests passed 257 with 4 opt-in live skips, and installed
wheel checks passed 70. Ruff and changed-module strict mypy passed. Host Python
is 3.10.12 and the pinned container is 3.10.22; Python 3.12 was unavailable locally.
The full-check skips retain optional model/database/artifact prerequisites and
include no new A-share tests. Hosted CI did not start because of account billing;
its annotations are evidence of unavailability, not executed tests. Full log SHA256:
`d5dac7a82547b455938199f983adcd21431937ee5ae09d19f48bdff99e8b69ce`.

This final documentation follow-up reuses that full check only with byte-identical
executable/config/test closure, plus affected documentation/planning checks.
The closure includes 1806 tracked non-documentation files; original manifest SHA256:
`404f5eaa53d6ad1e1989a886080077d041c5ef70029edb1161f526490a20488d`.
Complete checks, integrated candidate dispositions, raw responses and rollback
receipts stay outside Git under the evidence root above.

Scoped disk cleanup removed verified completed software-image TMP copies only
(154043055 logical bytes, 154107904 measured free-byte increase), plus this
completed image-build context after its source/image/bundle identity was retained
(15575423 logical bytes, 17702912 measured free-byte increase). No shared prune,
market-data deletion or production change was made. The existing cleaner's
03:30 run described above has a different owned root; it does not own these
software TMP copies. Operations tracking remains in #537.
