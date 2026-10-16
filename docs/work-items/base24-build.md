# P10-DATA-BASE24-000: ODS/DWD Base 2.4 and October continuation

| Field | Value |
| --- | --- |
| Parent | [Permanent P10](../design/lld/12-permanent-data-line.md); [work-item index](index.md) |
| Design status | draft |
| Delivery gate | blocked |
| Requirement input | [Owner-supplied REQ-P10-BASE24-001 v1.3](base24-requirements.v1.3.md), 2026-10-16 |
| Predecessor | [2.3.1 priority batches](base231-priority-batches.md) |
| Authority | Current build line targets 2.4 directly; CN02 is the future Daily writer; T-day after-close is the main target, T+1 is recovery/revision only |

This node is an implementation/takeover plan for received requirements, not a deployed
policy or new acceptance. The Owner's product direction is recorded below; frozen old
contracts stay effective until an explicit opt-in successor, tests and governing LLD
updates agree. No production switch, credentials, service/timer action, cloud upload,
old data deletion, research activation or paid acquisition follows from this plan.

The v1.3 input supersedes v1.0's fixed T+1 main-flow design. Unpublished target
version/configuration/output declarations may move directly to2.4. Sealed2.3.1
images/manifests/receipts retain their original identities. Continue the existing
isolated branch/worktree; a version change alone does not require another copy.

## Current recovery checkpoint

Continue CN02 `/root/Quanthus/.worktrees/base22-standalone`, branch
`codex/base22-standalone`. The inherited, clean engineering checkpoint is
`13eb02d54312e4dcb1131e56db4bc9b1960cdf30`. This v1.3 issue-planning update changes documentation
only; accepted capsules retain their actual producer/module hashes. Do not rebind
them to a new HEAD or rename a predecessor image to 2.4.

The sole live gap ledger remains `current-gaps.json` under
`/srv/agentdock/AgentDock/base22-review-afe2526/base231-budget-exhaustion-continuation-20261015T153555Z-c887578`.
It contains the retained G01-G13 entries and the new BASE24 entries. The takeover
snapshot is `base24-takeover-20261015T204438Z` within that job. Review exports and
repository prose are snapshots/routes, not competing live ledgers. The snapshot
preserves the exact received REQ, its digest, pre-transfer controls, source-selection
identity census, capacity receipt and takeover record. No Base build was in flight;
the node's unrelated scheduler and existing services were left running.

The inherited `make check PYTHON=.venv/bin/python` passed at the engineering SHA:
6,526 passed, 21 skipped, 66 warnings. Scoped holder/action lint and type checks
passed. These are code checks, not real 2.4 source or image qualification. Earlier
repository-wide Ruff results contained 216 failures; no new whole-repository Ruff
pass is claimed. A prior interrupted check and two corrected cold-controller
attempts remain evidence, not successful complete runs. Final native domain
receipts explicitly retain their successful replay/negative checks.

### 2026-10-16 bounded execution checkpoint

This checkpoint supersedes the earlier implementation-state statements below; the
product boundaries and acceptance gates remain unchanged. Continue from local
commit `ca6930d8fddfe817fb78bf5d9cc545cc526891f1`. The worktree was clean after that
commit. `make check PYTHON=.venv/bin/python` passed with 6,645 tests, 21 skips and
66 warnings. The Base 2.4 focused suite passed 73 tests. These remain engineering
checks, not real source or candidate acceptance.

The default frequency contract is now versioned as
`config/pipelines/base24-default-frequency.v1.json`: `1d` plus supplier-native
`5m`; independent `1m` is `NOT_INCLUDED` and its retained sources are not deleted.
The source feasibility census froze 21,606 missing windows over 5,051,993 required
security-days after preserving 2,752,760 existing good security-days. The selected
single-concurrency acquisition is resumable and fail-closed. Repeatedly invalid
windows receive a hash-bound `HELD_AFTER_BOUNDED_FAILURES` receipt after three
attempts, remain in the denominator, and never become acquired data. At the
2026-10-16T08:20Z snapshot, 334 partitions were sealed and 6 were held; the
supervisor and acquisition runner were live. Counts are execution snapshots, not
coverage conclusions.

The downstream chain is registered and live:

1. acquisition root `base24-native5m-acquire-a60465f`;
2. exact coverage ledger `base24-native5m-ledger-733a7cd`;
3. source-grounded ODS projection `base24-native5m-projection-5338759`;
4. whole-domain `dwd_security_bar` replacement `base24-native5m-dwd-bar-80366bb`;
5. complete 33-table image `base24-complete-dwd-ca6930d`.

The ledger distinguishes acquired R2, justified no-data and unresolved R3. The
projection watcher refuses to run while R3 remains. The DWD adapter preserves
actual acquisition time, listing/venue/calendar closure and exact source lineage,
keeps `1d`, replaces supplemented `5m` security-days as a whole, and emits zero
`1m` rows. The complete assembler binds all 32 unchanged tables to the exact
Base 2.3.1 predecessor and replaces only `dwd_security_bar`; it uses the shared
full-table/global-key/reference/lineage engine under a distinct Base 2.4 contract.
Its watcher freezes a same-snapshot capacity receipt, performs a source-denied
read-only full scan, and writes `READY` last. Two relocated cold scans and damage
negatives remain a later acceptance stage and are not implied by the first seal.

No Base 2.4 ODS or DWD candidate exists at this checkpoint. No production pointer,
consumer admission, service, CURRENT, cloud archive or source file was changed.
The data device had 66,120,671,232 free bytes at the snapshot after retiring only
registered, recoverable Base 2.3.1 intermediate copies. The acquisition, coverage,
projection, DWD and final-image outputs remain capacity-gated with an 8 GiB free
floor.

The ODS pair is now an explicit successor rather than a renamed predecessor.
`config/pipelines/base24-ods.v1.json` and
`securities_warehouse/base24_ods_image.py` define ODS Base 2.4 as the exact ODS
2.3 non-1m selection plus the terminal supplier-native 5m acquisition closure:
original wire bytes, exact Decimal projection, receipts, bounded failures/holds,
the frozen denominator and a PASS/R3=0 coverage ledger. ODS 2.3 remains an input;
it is not the ODS corresponding to Base 2.4. The new offline auditor never opens
archival source paths, rejects missing/corrupt/extra bytes and writes READY last.
This is implemented engineering state; the real acquisition has not yet reached
its terminal gate, so no real ODS Base 2.4 candidate or admission is claimed.

## Product and immutable input boundary

- Base business upper bound: 2026-09-30 inclusive, Asia/Shanghai. October events
  and observations belong to Daily. Acquired/publication/effective/knowledge,
  build and node-visible clocks stay separate; no historical time backfill.
- Default product: `1d` and supplier-native `5m`. Native 5m target starts at the
  first applicable authoritative session on/after 2020-01-01. Earlier retained
  non-minute history is preserved. Independent 1m is optional `NOT_INCLUDED`,
  neither repaired nor deleted, and cannot block the new default profile.
- Freeze historical SSE/SZSE A-share listings, retired names, dated code changes,
  venue/session rules and the explicit inherited index list. Enumerate previously
  included other objects; no silent scope drop or implicit new-market expansion.
- Bind new ODS2.4 plus immutable qualified supplements as the factual source of
  DWD2.4. Old DWD/ADS/CURRENT are not reconstruction truth and cannot create ODS.
  Old metadata can locate scope; exact source-grounded parsing outputs may be
  reused only with complete matching semantic identities and their real clocks.
- Enumerate the exact model table set and approved extensions from
  `config/pipelines/dwd-domain-model.v1.json` and
  `dwd-domain-model.v1.1-extension.json`. Every table needs source, business cutoff,
  keys/versions, partitions, row census, clocks and explicit purpose status.
  Empty data is not evidence of no applicable changes.
- Proposed opt-in profile `p10-base24-1d5m-v1` is not implemented/active yet.
  Keep current `1d/1m/5m` contracts intact; do not edit an old required-frequency
  list in place or impersonate a legacy image kind to make a reader accept it.

## Retained work and exact reuse decisions

All counts below are takeover snapshots; use the live ledger for later state.

| Scope | Saved result | 2.4 action / unresolved boundary |
| --- | --- | --- |
| G01 financial origin | 560 new native facts in 16 source scopes independently accepted; 200-request ceiling reached | Preserve qualified originals/producer pins. 4,320 original cells remain held; 39 later originals are evidence only, not native-admitted output. No further use of the exhausted budget. |
| G02 ratios/currency | Dimensionless ratios retained; no authority for old TDX monetary currency | Keep unknown currency unknown; other providers' CNY cannot qualify it retroactively. |
| G03 holder matrices | 1,093/1,147 distinct coordinates; newest 12 frames/120 rows have two cold replays and three negative checks | 54 remain: 15 layout/field, 28 dated issuer, 11 exact-provider-frame. Keep conflicting observations separate; full source-grounded reuse still needs 2.4 closure. |
| G04 holder conflicts | 30 complete official comparisons against 42 original conflict coordinates; zero priority arbitrations | Keep all 42 disputes; later official knowledge does not prove old provider priority or inferred exits. |
| G05 shareholder transactions | Exact original plan and result independently retained | A plan ending September29 is not a September29 transaction, especially when acquired/published earlier. No date replacement/netting. |
| G06 implemented distributions | 277 distinct issuer/document events from 278 selected materializations; original baseline218, unique additions59 | 000703 current wrapper repeats one baseline event. Corrected implementations count once. 200-request ceiling reached. The360 title-candidate set is a different denominator, not360 required dividends. |
| G07 listings/names | 23 IPOs,546 daily bars/states,33 alias targets/34 intervals | Preserve dated source scope; delisting-period entry is not actual removal. Full historical listing denominator still separate. |
| G08 minutes | Existing originals/evidence retained; legacy source/time/basis closure incomplete | Optional1m limitation moves out of default dependency. 5m limitations remain genuine and need source-specific qualification. |
| G09 factors | Six selected current event-reference inputs qualified | No full historical cumulative QFQ/HFQ or tax-dependent economic-effect qualification. |
| G10-G13 | 4,173 visibility-unavailable entries, missing original normalization closure, unrecoverable old precision/time and three preserved turnover conflicts | Preserve exact restrictions and old values. Version changes do not restore unavailable historical evidence. |

G01 and G06 stopped at actual request ceilings. Other unfinished scopes were
redirected by the Owner, not exhausted or declared solved. Unused balances neither
transfer to 2.4 nor reset automatically. Qualified domain output is not evidence that
the latest continuation was integrated into a new complete image: it was not.

The newest domain identities include:

| Capsule | Semantic input identity |
| --- | --- |
| G03 nature-first twelve | `fc5be92cd69331f710b39671aaeddcdccdf397abafe4625455ba6e4229ffc878` |
| G06 report-currency ten | `9f0ca8518ee42123638d6772d74c538888d6f0837171cdc8bf32a347413a7adf` |
| G06 report-currency seven | `9f2b480139e4c0718cac4fba289bd637b0067b14b6edd2917b0bdaf695bbf1e6` |
| G06 report-currency eight | `2e109a2c2ab5e6550e83dc6a84aa1c94f4da25a7dc10df35979250d30497dda9` |
| G06 complete-page currency boundary | `d042e3536dbb187206c8096fd046bef0343541a88096fce652b9872793d5f882` |

The takeover inventory pins INPUTS bytes and original code-module digests. Its
metadata census is not a rerun of all underlying market payload or cold acceptance.
Reusing an adapter does not reuse source admission when purpose/denominator changes.

## Stage DAG and first execution milestone

`A takeover -> B source feasibility -> C ODS2.4 -> D DWD2.4 -> E isolated Daily -> F cold recovery/read -> G switch preparation`.
Only dependent failures stop downstream work; local noncritical holder/financial
limitations do not block unrelated qualified products. Whole required 5m coverage
failure does block the claimed 2020-start default joint product.

1. **A: freeze received intent and current identities.** Reconcile requirement
   reference commit `93f928592bcf1dc99fdd2c3db681107b719a5983` with actual build
   HEAD; do not check out/reset to it. Several referenced paths are absent here:
   ADR0016, `p10-complete-daily.v1.json`, `p10-notification-trial.v1.json` and the
   sender's `outputs/.../CURRENT-GAPS.md`. Reconcile exact normal Git versions
   before implementation depends on their contents. No deployment state inferred.
2. **B: historical native5m feasibility and cost report, before bulk requests.**
   First inventory known original releases and current source contracts. Existing
   `providers/minute_tdx.py::_baostock_native_5m_bars` requests an exact day with
   `frequency="5"`, `adjustflag="3"` and explicit date/code checks, but converts
   source price/quantity strings to float. It neither preserves full native wire
   responses nor proves early/retired coverage. Add a separate raw-preserving,
   Decimal-safe bounded path before acquisition; keep existing callers compatible.
   TDX fixed pages/empty results cannot prove the 2020 boundary.
   Freeze exact historical listing/session samples spanning early2020, a middle
   year, August/September2026, SSE/SZSE boards, a code change, retired and newly
   listed securities. Source version, timestamps, units, price basis, pagination
   end, access restrictions, requests/retries/bytes/time and output/scratch budgets
   must be registered before requests. This document itself registers no new
   network budget and claims no historical availability. Forbidden/blacklisted
   access trips the source circuit; no identity/exit rotation to bypass it.
3. **C: reconstruct ODS closure and the coverage denominator once.** Retain raw
   response bytes, request/transport versions, real times and native schema census.
   Share one immutable job-local dated identity/calendar dependency. Count source,
   admitted ODS and actual DWD separately by listing/day/interval/basis, then month/
   year, with bucket gaps and explicit inapplicable evidence. Inspect existing
   August/September5m and September28-30 before recollection. Source presence,
   min/max and successful download do not establish full census or R2.
4. **D: project by bounded affected partition.** Each domain consumes the same
   admitted ODS/supplement and shared prerequisites. Qualify and reuse prior parsed
   components only under identical source semantics; rebuild changed dependencies.
   Retain Decimal/null/zero/late, clocks, version keys, venue/calendar and global
   references. Unknown nonempty domains are explicit held scopes, not empty fillers.
5. **E: new opt-in reader and real OctoberDaily.** First exercise a representative
   complete day through real production code in isolation, then the actual frozen
   October range. New products bind exact Base identities, not relabelled old Daily.
   Cover September30/first-session boundary plus holiday/weekend/weekly events.
   Record success/failure per domain and target. Design CN02's T-day after-close
   collection/build path; actual qualified1d+5m should build/deliver immediately,
   without a fixed wait until T+1. T+1 handles unresolved required observations
   and real late revisions only. Official supplier availability times and a20:30
   candidate first attempt are received evidence/proposals, not a frozen success
   deadline or proof of CN02 actual completion. Keep production timers unchanged.
6. **F/G: whole acceptance and reviewable switch package.** Changed scope requires
   full physical/semantic validation, two independent relocations/cold processes
   and disposable damage negatives where required. Receipts separate physical,
   coverage, source, DWD admission, purpose, reader, Daily and delivery. Freeze
   actual CN02/CN01/OCI writer/reader SHA/config, drain order, paired rollback paths,
   cloud target identities and approved timing. Production execution is separate.

The new versioned timing table must cover first after-close attempt, retries,
day-end cutoff, exceptional recovery and delivery deadlines with measured source,
rating/build/transfer latency. Old10:30/13:00/13:20/14:00 T+1 times are historical,
not new T-day acceptance gates. Counters/limits cannot reset during migration.
CN02 must produce its own natural after-close receipts; historical recovery and
CN01 success are not substitutes. Record actual recovery only if it occurred.
Trial scope must first resolve single-writer/shared-source budget conflicts without
silently stopping production or collecting the same task twice.

Before any approved production handoff, drain and stop CN01's old active collection,
build/archive entrypoints, then enable CN02's corresponding single writer; preserve
paired rollback. Verify CN02 private/outbound delivery capability, OCI actual read,
one GDrive archive entry and stage-specific notices. Do not assume inbound public
SSH exists on CN02 or damage its unrelated marketplace service. Coordinate supplier
sessions/concurrency/request ceilings across historical, Daily and financial Jobs.
This data-writer direction does not move OCI business/platform services.

## Capacity and lifecycle

The same-UTC takeover receipt records both actual device identities, available
bytes/inodes and MemAvailable. Each device retains the existing8GiB free floor.
Combined safe headroom is less than1GiB and is not poolable across devices. The
REQ's40-50GiB ODS+DWD footprint is an unmeasured scenario, not a qualified peak.
Even its optimistic40GiB pair alone exceeds current safe headroom by more than
39GiB, before new raw responses, bounded scratch, archive and verification copies.
This is a capacity blocker for a full new pair, not a request to expand disk now.

Prepare an exact retention/recovery census: own successful disposable test/cold
TMP first, then only explicitly authorized recoverable duplicates without successor
dependencies. Preserve source originals, failed evidence, currently sealed versions
and optional1m archives. Do not assume removing1m from the default authorizes its
deletion. No removal or capacity relief was performed during takeover.

Before every write batch freeze sources/output partitions, expected keys/versions,
device/UTC space and inode snapshot, simultaneous live files, scratch/spill/RSS/time
caps and checkpoint recovery. Retained2GiB DuckDB/4GiB application RSS sample limits
are not a whole-job budget. ext4 reflink is unsupported; count actual independent
bytes. Use one immutable prerequisite TMP per Job and a bounded partition working
set. Acceptance-copy rotation needs its own actual device budget; do not count two
simultaneous copies when rotating, or delete either before its restoration proof.

## REQ implementation and acceptance matrix

| REQ | Owning stage / planned evidence | Takeover state |
| --- | --- | --- |
| 01 | A/B/E: dates,1d+native5m, historical listings and explicit index set | Product intent recorded; execution scope not frozen |
| 02 | A/C/D: field clocks and knowledge-cutoff manifest | Rules preserved; new input cutoff pending |
| 03 | C/D: exact33table/extension rules, entities and source/partition census | Existing models pinned in takeover; new closure pending |
| 04 | A/C/D: semantic reuse and producer/input digest receipts; ODS factual ancestry | Inventory saved; new ODS/DWD not built |
| 05 | B/C/D: legacy-gap to source/admission/DWD reconciliation | G01-G13 plus BASE24 entries in one ledger |
| 06 | B: finite historical/retired capability and cost report | Unproven; no bulk requests |
| 07 | B/C: native5m raw bytes, source basis/units/session semantics and fallback contracts | Existing adapter risks identified; new raw path pending |
| 08 | C/D: full frozen denominator, bucket/day/month/year counts and unexplained gaps | Not run |
| 09 | C/D: existing rating-policy digest; minimumR2 and required-denominator R3/unknown reconciliation | No tolerance changed; no new rating granted |
| 10 | D/E/F: separate physical/product/purpose/reader/Daily/target receipts | New profile/receipts pending; consumers not granted |
| 11 | C/F: immutable self-contained ODS originals, hashes and restore evidence | New ODS not built |
| 12 | D/E/F: precise ODS/supplement/code/model/basis identity plus real-reader results | New DWD/read path not run |
| 13 | A-G: stage ownership, checkpoint identity, cumulative budgets and TMP lifecycle | Old counters preserved; new execution budgets pending |
| 14 | E: actual isolated OctoberDaily including holiday/weekly/revision sources | Not run |
| 15 | E/G: CN02 T-day1d5m, T+1 recovery only; versioned timing/single writer/shared-source budgets and actual receipts | Received v1.3 intent; implementation/trial/deployment not accepted |
| 16 | A-F: per-device write/peak/copy budget and resource-conflict receipt | Snapshot captured; full-job capacity not qualified |
| 17 | A-G: independent phase-specific source/image/reader/switch conclusions | A preparation captured; B-G not accepted |
| 18 | E/F: listed negatives, T-day immediate delivery, controlled next-day recovery, single-writer/cross-Job budgets and engineering checks | Existing unchanged regressions retained; new profile regressions pending |
| 19 | A-G: sole live ledger, identity snapshots and separate completion counters | Existing authority retained; BASE24 entries appended |
| 20 | F/G: paired manifests, recovery, coverage, reader/Daily and rollback handoff | Final deliverables not yet created |

No overall2.4 completion percentage or fixed finish date is established by old
tests, capsule counts or the received size estimate. The first measurable result
is B's finite historical-source feasibility report; time and capacity estimates
are revised from that evidence, without silently dropping unserved years/securities.
Target GDrive layout remains `quanthus-ods/base/2.4` and
`quanthus-dwd/base/2.4`; verify exact folder identities and permissions only when
publication is separately ready/authorized. Final conclusions must distinguish
data-range acceptance, actual-reader/Daily acceptance and eligibility to request a
production switch. All three are currently unaccepted/not eligible for this version.

## Dot execution routing

The finite programme is split into source feasibility(010), historical denominator
and coverage(020), retainedAugust/September5m(030), source-grounded ODS/DWD pair(040),
CN02 reader/isolatedOctoberDaily and new timing contract(050), capacity/lifecycle(060),
whole cold recovery(070), paired single-writer switch/rollback preparation(080), and
natural after-close operational evidence(090). Actual issue URLs are recorded in the work-item
index; the parent issue embeds the exact receivedv1.3 requirements so
Dot can read them without assuming unpublished local commits exist on GitHub.

Start060's capacity planning and010/020's bounded preparation. Full writes cannot
start until their source/resource gates pass. Each issue owns outputs, input identities,
prerequisites, acceptance negatives, actual/remaining budgets and the precise next
action. A blocked expensive stage does not stop independent safe engineering.
The former unissued070publication-planning placeholder is split into070cold acceptance,
080switch preparation and090natural-run evidence; old takeover snapshots stay immutable.

Dot uses the existing sole live gap ledger and one workspace writer, restores exact
checkpoints/counters and reuses unchanged qualified outputs. No new generic scheduler
or per-small-change full rebuild. Report scope completion separately from admitted
data coverage, with remaining work share and evidence-based conditional ETA. Budget
exhaustion/deferment is not repaired data. Treat this issue creation as planning:
Dot is not started automatically and no deployment/schedule change is performed.

## Live GitHub execution map

Parent: [#527](https://github.com/rv198-star/Quanthus/issues/527), under permanent P10
#478; successor routing is also recorded on #525 without rewriting its evidence.

| Stable suffix | Issue | Scope |
| --- | --- | --- |
| 060 | [#528](https://github.com/rv198-star/Quanthus/issues/528) | 冻结每盘峰值容量、保留/回收与分片恢复方案 |
| 010 | [#529](https://github.com/rv198-star/Quanthus/issues/529) | 证明2020起原生5m与退市证券来源可行性并保全原始字节 |
| 020 | [#530](https://github.com/rv198-star/Quanthus/issues/530) | 闭合历史listing×session分母和逐桶/日/月/年覆盖账本 |
| 030 | [#531](https://github.com/rv198-star/Quanthus/issues/531) | 复用既有8/9月5m并修复9月底来源→DWD断点 |
| 040 | [#532](https://github.com/rv198-star/Quanthus/issues/532) | 构建新ODS闭包及完整33表DWD Base2.4实体配对 |
| 050 | [#533](https://github.com/rv198-star/Quanthus/issues/533) | 适配CN02 1d+5m读取器并生成真实隔离October Daily |
| 070 | [#534](https://github.com/rv198-star/Quanthus/issues/534) | 完成成对镜像恢复、全表冷验收与损坏失败关闭 |
| 080 | [#535](https://github.com/rv198-star/Quanthus/issues/535) | 准备CN01→CN02单writer交接、OCI/GDrive与成对回滚 |
| 090 | [#536](https://github.com/rv198-star/Quanthus/issues/536) | 取得CN02自然T日盘后、异常恢复及逐端真实运行证据 |
