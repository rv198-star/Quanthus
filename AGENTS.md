# Quanthus Engineering Rules

## Invariants

- Preserve point-in-time semantics. Features may only use information available at the
  declared `knowledge_cutoff`.
- Under the active `close-to-next-open-v1` profile, daily signals are produced after the T close
  and may first execute at the T+1 open. Any intraday profile requires versioned windows and PIT
  cutoffs; email delivery or manual operation never implies a fill.
- Model or representation outputs never bypass the expert and veto decision path.
- Missing or invalid data blocks new entries and raises an alert. It is not structural
  damage and must not force a sale.
- Every decision, rejection, fill, policy version, and data snapshot must be auditable.
- Do not silently change thresholds. Update the versioned policy and contract tests.

## Current scope

- P1 is conditionally accepted under `p1-c1-conditional-20260725-v1`: 4,173
  factor-visibility exceptions remain fail-closed and must not receive inferred cutoffs.
- P2 is conditionally accepted for `close-to-next-open-v1` under `config/p2-gates.v1.json`.
- P3 is accepted under `config/p3-gates.v1.json`; its governed candidate is a frozen capability
  fixture, not a strategy lineage. P4 may build the event library through the accepted daily
  profile without opening arbitration or blind partitions.
- The operator-assisted two-window profile, forward email transport, the full expert set, and the
  training layer remain blocked by their explicit phase and activation gates.

## Permanent data ownership — P10

- The Owner assigned acquisition and all shared data-quality/governance work to permanent P10 on
  2026-10-03. Read `docs/design/lld/12-permanent-data-line.md` and
  `config/data-ownership.v1.json`. P10 is a parallel standing line, not a sequential release gate.
- Non-data business lines may only register a bounded P10 issue for data quality/governance;
  they must not collect/backfill, hunt source archives, repair/supplement deficient inputs,
  reinterpret PIT times or maintain a temporary quality pipeline.
- After registration, choose and record a business bypass, temporary ignore or acceptance of a
  known limitation. Keep business work moving where its contract permits. Stop only the affected
  step that cannot meet an existing hard safety, PIT, partition or activation gate. A limitation
  is not repaired/qualified data; do not invent evidence or silently alter frozen policies/windows.
- Domain features, targets, scientific reference criteria, models, strategies and consumer
  algorithms stay with their business line; shared data consistency/repair stays with P10.
- P1 acceptance, frozen requests and prior temporary receipts remain immutable. Earlier P3/P5
  temporary-source approvals do not authorize future data repair/recovery outside P10.
- New data issues use `phase:P10`, `area:data`, `P10-DATA-*` ids. Preserve migrated numbers, titles,
  evidence and subgroup gates; closed historical issues are not moved/reopened. P10's standing
  epic stays open, and issue routing/closure never establishes consumer acceptance.
- Existing execution/deployment safeguards remain. The #424 SSH override is task-scoped, not a
  general P10 remote-access override.

### Warehouse quality iron law — Owner 2026-10-08

- **Business value over absolute precision.** Follow
  [ADR 0016](docs/architecture/adr-0016-value-proportional-warehouse-quality.md)
  and P10 LLD 12 §2A before changing warehouse admission, supplier qualification or
  data-release topology. A rule must name its real product, consumer, required resolution,
  material decision risk and cost before becoming a global blocking gate.
- **Isolate noncritical defects.** Failure of optional/archive-only 1m data must not
  unconditionally block independently qualified daily-core data. Never describe unavailable,
  ambiguous or incomplete minute products as READY. Current legacy four-lane READY remains
  in force until a separately approved versioned product/consumer design and rollout (#496).
- **Hard truth still wins.** Preserve security/date/unit/price-basis identity, raw SHA/lineage,
  PIT and protected windows, source-observed versus derived, no fabricated minute OHLC or
  residual shares, and strict strategy/trading activation gates. No source-priority vote
  merely because vendor bars disagree; consumers requiring true high-precision input fail closed.
- **Cost- and impact-bounded investigation.** Record affected consumers, measurable signal
  impact, finite sampling/time/request budget and stop condition. If further precision has no
  demonstrated material beneficiary, retain the open quality debt and restore higher-value
  engineering. Never endlessly replay identical raw/source requests.
- P10 #495 governs quality tiers and traceable exceptions; #496 governs daily/minute release
  decoupling. Neither this documentation nor a diagnosed source issue authorizes changing
  sealed evidence, deployment, disabled timers, DWD/OCI publication or prior acceptances.

## Current active task routing — P3-STRAT-003

- `P3-STRAT-003` / #317 and B-R1 / #412 were reopened under the Owner's explicit continuation.
  The exact retained candidate and sealed industry sources were recovered through NexusAgent on
  OCI. The bounded inspection resolved 5,250 of 5,251 securities with zero source conflicts;
  the approved single-security source has now been composed into a temporary static mapping of all
  5,251 securities with zero missing/duplicate/conflicting identities. This is not full B acceptance.
  Data recovery/repair stays with P10 #470/#473/#474. B / #319 stays NO_PASS and unaccepted;
  D remains NOT_RUN.
  Preserve the earlier closeout as historical evidence, not the current routing instruction.
- Purpose: compare existing Trend + Position Risk against a non-empty deterministic P4 baseline
  while P6 continues independently; P6 is not a predecessor and must not be mutated.
- Preserved state: A / #318, C / #320 and C-R1 / #385 accepted; B not accepted
  (`NO_PASS_STATIC_INDUSTRY_INPUT_COVERAGE`). Issue closure does not satisfy B/D acceptance.
  See the work-item recovery checkpoint before the next bounded input action.
- Before working on this line, read `docs/work-items/p3-strat-003.md`. For interrupted-session
  recovery, start with its `Current recovery checkpoint`, then expand only the sections needed for
  the task at hand.
- Frozen authority lives in `config/research/p3-strat-003/request.v1.json`; live issue/PR status
  must be verified rather than trusted from cached prose.
- Hard boundaries: no 2025/2026 strategy/economic result read before D; no cutoff roll-forward or
  window shrink; no rerun of accepted A/C/C-R1 as new progress; no resurrection of the stopped
  exact-manifest retry sequence; no generic historical-execution platform without separate
  authority; no P6 production mutation.
- Negative, inconclusive, or unavailable historical evidence is a valid outcome. A green transport
  or unit test is not B acceptance.
- The earlier 2026-10-03 activation and completed temporary evidence remain sealed in
  `config/research/p3-p5-temporary-source-activation.v1.json`. P10 supersedes future P3 source
  recovery/repair. #412 now owns consumer admission/business disposition only. Temporary success
  never closes a data issue or accepts B; existing frozen gates still govern corrected B/D.

## Parallel research routing — P5-STAGE-001

- `P5-STAGE-001` / #400 and B / #402 were reopened under the Owner's explicit continuation for
  exact input integrity, schema and listing/session census. The three-table 2012–2021 file set is
  frozen in `config/research/p5-stage-001/input-census.v1.json` before row access.
  A remains accepted preregistration-only; B is unqualified; C preserves partial engineering.
  D / #404 is reopened only for synthetic orphan recovery under
  `config/research/p5-stage-001/engineering-continuation.v1.json`; full D acceptance remains open.
  E/F/G remain NOT_RUN. The prior closeout remains historical evidence.
- Purpose: compare the existing rule/Stage surface with fixed simple baselines and one bounded learned
  temporal assessment over the layered 1..12 research vocabulary; it is not a top-1 Stage classifier.
- Before working on this line, read `docs/work-items/p5-stage-001.md`, then the frozen request and
  target set under `config/research/p5-stage-001/`. After interruption, start with the work-item
  `Current recovery checkpoint` and expand only what the current gate needs.
- A permits docs/config/tests and synthetic contract cases only; that sealed predecessor boundary is
  unchanged by the later successor activation.
- A completion does not auto-start B/C/D. The current B continuation comes from the separate Owner
  instruction and live #402 activation, without authorizing training or widening source families.
  Earlier temporary activation and completed evidence remain sealed. P10 owns future source
  recovery/quality repair under #471/#472. #402 owns research input, split, independent-reference
  qualification and business disposition. Unresolved scope still blocks the required coordinate/
  window gate; a disclosed limitation does not accept B or open training.
- The successor receipt does not alter sealed A evidence. B must freeze an exact manifest before its
  first market-row read; C/D remain synthetic until their own gates. No protected 2022-2026 access,
  result-bearing training, P3-STRAT data-authority inheritance, P6 mutation, promotion or trading.
- Negative, partial, data-limited, compute-limited and inconclusive outcomes are all valid.

## Blueprint-linked work

- For learning-system work under #540–#548, read
  `docs/work-items/learning-blueprint-progress.md` and run
  `python3 scripts/blueprint_progress.py context --issue NUMBER` before starting.
  Register a new work item and its module contribution before expanding this tracked scope.
- At an accepted milestone, rejection, blocker, defer/stop, issue close or reopen, append one
  evidence-bound checkpoint through `scripts/blueprint_progress.py record --checkpoint PATH`.
  State the module/facet affected, exact scope, remaining gap and next action. An explicit
  no-module-change disposition is valid; routine logs need no checkpoint.
- Keep issue lifecycle, engineering, research quality, purpose value and consumer admission
  separate. Issue closure and green tests never promote a state head or grant execution.
  Preserve old checkpoints, failures and frozen research gates. TPlan tracks execution;
  the repository ledger owns the current reported module view, not scientific authority.
- Regenerate and check the blueprint in the same handoff. Reconcile exact live issue metadata
  after closure/reopening; a drift is a bookkeeping gap, not a new global data/research gate.
  Fix that gap before claiming the affected progress, while unrelated authorized work continues.
  The governing mechanism is `docs/design/blueprint-progress.md`.

## Development

- Support Python 3.10 or newer.
- Keep production logic under `src/quanthus`; notebooks may only call production modules.
- Do not commit market data, credentials, run artifacts, or local databases.
- Prefer deterministic ordering and explicit reason codes over implicit behavior.
- Run `make check` before a handoff.

### Local acceptance default — Owner 2026-10-29

- Treat GitHub-hosted CI as unavailable by default. Use local acceptance to continue
  authorized engineering and PR review; do not wait for account payment, buy runner
  capacity, repeatedly rerun billing-blocked jobs, or describe them as executed tests.
  GitHub remains the code-distribution and PR history channel.
- Run applicable focused regressions, lint/type checks and `make check` locally.
  For blueprint work also check immutable history against the exact PR base and
  reconcile affected live issue metadata. Run Web checks when the change affects Web;
  explicitly report unavailable runtime versions, optional dependencies and skipped checks.
- Bind evidence to the exact tested code/config/test closure, runtime, commands,
  results, logs and SHA256. A documentation/ledger-only follow-up may reuse an existing
  complete `make check` result after proving that its executable/config/test closure
  is byte-identical; rerun affected planning/documentation checks and record the reuse.
  Changed dependencies, test selection or semantic inputs invalidate that reuse.
  This does not replace a required independent construction or cold acceptance.
- Separate local pass, existing baseline failures, not-run checks and remote CI status.
  Review actual failures on their merits; unavailable CI is neither a code pass nor a
  new research/data gate. Retain scientific, PIT, consumer, production and merge authority.
- Use ordinary authorized PR/merge operations only. Do not forge hosted-CI success,
  use admin bypass, change branch protection/workflows/account settings, or start a
  self-hosted runner merely to replace unavailable hosted CI.

## Batch data construction

- Design warehouse, feature, replay, attribution, and other data-heavy batches as an explicit
  stage dependency graph. Before implementation, identify which deterministic prerequisites are
  shared, which stage owns each materialization, and which downstream stages consume it.
- Do not rebuild semantically identical prerequisite data more than once inside one Job. Build one
  immutable job-local intermediate and pass it to every in-Job consumer. Record stage elapsed time,
  row counts, bytes, and reuse/build disposition so repeated work is visible.
- Classify every materialized intermediate by lifecycle: `TMP` is job-local and removed with its
  staging root; a content-addressed cache may be reused across Jobs under an explicit retention and
  invalidation policy; `ADM` is a governed, published data product with stable downstream consumers.
  Do not promote temporary implementation data to ADM merely to avoid recomputation.
- A reusable identity must cover the complete semantic input closure, including the authoritative
  data/effective-view digest, configuration and policy digests, evaluator or code version, schema
  version, time/partition scope, and security universe or subset. A partial key, identity mismatch,
  or unreadable manifest is a cache miss or fail-closed error, never a reason to reuse stale data.
- When the same expensive prerequisite recurs across Jobs, measure its cost first and then choose
  the smallest sufficient reuse boundary: job-local sharing, content-addressed cache, or governed
  ADM. Preserve immutable publication, lineage, PIT rules, and sealed-partition boundaries at every
  level.
- Formal independent reproduction and cold-run acceptance must remain explicit modes. A cache hit
  may accelerate routine experiments, but it must not replace an independently rebuilt artifact
  when the governing acceptance contract requires independent construction.

## Deployment and remote access

- **NexusAgent is the default remote execution and control channel for Quanthus,
  except for an explicit task-scoped Owner override.** For the ODS/DWD reliability
  programme (#424), the Owner explicitly instructed on 2026-09-22: prefer existing
  passwordless SSH and do not use MCP for node management. Use the existing CN01,
  hermes and oci SSH entries with batch authentication and known-host verification;
  do not weaken host checks, change credentials, or infer deployment approval from
  successful connectivity. GitHub code distribution and release gates still apply.
  This control-channel rule does not retire the OCI data node: the Owner confirmed on 2026-09-22
  that the current data path remains Base V2.1 + Daily, with OCI mirroring CN01's verified DWD
  increments as defined in ADR 0007. Do not restore legacy OCI-specific control paths or use OCI
  as a substitute deployment/planning authority when a required NexusAgent connection is absent.
- GitHub remains the only normal code-distribution path. Remote execution through NexusAgent must
  consume versioned repository state; do not distribute ad-hoc source trees with rsync, scp, or
  retired OCI-built archives.
- Historical OCI ODS pull, dual-lane arbitration, paired DWD/DWS and ADS publication records are
  archival evidence, not current routing or recovery instructions. Distinguish those retired paths
  from the current CN01-to-OCI DWD mirror; the Base remains read-only.
- If NexusAgent is absent from the current conversation/tool context, report the execution-channel
  limitation precisely. Do not infer that Quanthus, its nodes, or GitHub are offline, and do not ask
  the Owner to restore a retired OCI control path.
- Preserve node-local read-only/deployment boundaries and current approved interfaces when
  NexusAgent operates on remote nodes. Do not revive retired OCI-specific tunnels, credentials,
  users, or host assumptions as a workaround.

### P10 daily-core implementation and rollout scope (2026-10-08)

Owner has explicitly authorized the new-standard restart with 2026-09-24 as baseline.
See `docs/work-items/p10-daily-core-20260924-recovery.md` (from repository root) and
`config/pipelines/warehouse-quality-policy.v1.json`. The opt-in `daily-core-v1` product
requires market and calendar/event ODS only, publishes seven DWD tables with 1d bars,
and uses `CURRENT_DAILY_CORE.json` plus immutable dated descriptors. It does not
change the old four-lane CURRENT, assert 1m/5m acceptance, or activate P6/training.
Dedicated CN01/Hermes daily-core services and OCI core-only mirroring are allowed
after their baseline, physical-source, contract and runtime verification.
