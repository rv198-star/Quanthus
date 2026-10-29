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

| Work item | Scope | Evidence/remaining gate |
| --- | --- | --- |
| [#582](https://github.com/rv198-star/Quanthus/issues/582) | Runtime lineage and upstream baseline | Daily chain established; upstream reference updated; no production upgrade claimed |
| [#583](https://github.com/rv198-star/Quanthus/issues/583) | Census, contracts and disposition ledger | Frozen declaration census; incomplete API definitions still need functional contracts |
| [#584](https://github.com/rv198-star/Quanthus/issues/584) | Structured data, announcements and regulatory APIs | Bounded samples; capability-specific results and limitations retained |
| [#585](https://github.com/rv198-star/Quanthus/issues/585) | Media, RSS and institution sites | Site identity and RSS tested separately; unavailable/network-limited entries stay explicit |

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

No image, deployed code pointer, timer, production environment, existing Base or
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

## Verification and next gate

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
