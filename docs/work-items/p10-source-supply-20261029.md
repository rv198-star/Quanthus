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

The CN02 runtime lineage is established. A complete new upstream reference checkout
is pinned locally; the production collector is a different, Quanthus-owned code
bundle. The declaration inventory and bounded request manifests are frozen outside
Git. The first screening pass and a separate API tranche retain raw response hashes.
Functional coverage beyond the named samples remains work under #584/#585; an HTML
site-identity result must never be reported as a working financial data feed.

Owner subsequently selected a public, relatively independent astock fork while
Quanthus remains private. [P10-DATA-SUPPLY-005 / #587](https://github.com/rv198-star/Quanthus/issues/587)
tracks the [source-subproject boundary](../design/astock-subproject.md) and fifteen
source-library request contracts in [rv198-star/astock](https://github.com/rv198-star/astock).
The original reference checkout and its hashes below remain historical evidence;
repository creation does not upgrade the deployed collector.

Owner's further 2026-10-30 instruction continues actual P10 integration, unit/time/
rollback contracts, dated quality candidates and finite observation, through the
existing issues. Current source library `0.1.1` adds the verified fund-NAV request;
the original `0.1.0` observations below remain historical evidence. The exact
Quanthus adapter, policy and watch boundary are in the child design. The new
nine-request tranche has one observation-purpose pass, five rejected request/
purpose definitions and three explicit holds. See the current integration section
for the complete dated ledger and test/operational receipts.

The current candidate watch is running locally on CN02 as
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
| [#587](https://github.com/rv198-star/Quanthus/issues/587) | Independent astock fork and bounded absorption | 15 contracts, pinned P10 observation adapter, unit/time/rollback contracts and finite watch |

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
