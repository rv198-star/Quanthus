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

## Current active task handoff — P3-STRAT-003

This section is an operational recovery guide for interrupted Agent sessions. It explains the
current research line, why it exists, the accepted/blocked boundaries, and the exact next action.
It is not acceptance evidence by itself. When this section disagrees with frozen config, immutable
artifacts, Git history, or the live GitHub issue/PR state, verify the authoritative source first and
then update this section rather than reasoning from stale status.

### Why this work exists

P6 prospective validation is already activated and continues as a separate long-running evidence
collection line. Do not wait for the P6 observation budget to answer questions that historical
replay can answer now, and do not modify P6 to help this research.

P3-STRAT-003 / GitHub #317 asks one bounded strategy question: under the same accepted P4 candidate
rule, guardians, capital, costs, matching and P2 execution semantics, what incremental economic
value is associated with the existing Trend and Position Risk effects compared with a non-empty
deterministic P4 candidate baseline?

The frozen study uses exactly four variants: B = deterministic P4 pass-through control with Risk
off; T = existing Trend with Risk off; R = pass-through control with existing Risk on; TR = existing
Trend plus existing Risk. The headline comparison is TR-B. The result-bearing windows are frozen to
calendar 2025 and 2026 through 2026-09-18; the window must not roll forward or be shortened because
an input is difficult to reconstruct.

### Milestone dependency state

The intended sequence is A -> (B, C) -> D -> E.

- A / #318: accepted. The research question, variants, windows, metrics, budgets and pre-result stop
  rules are frozen in config/research/p3-strat-003/request.v1.json.
- C / #320: accepted. The minimal B/T/R/TR research runner is implemented without reading recent
  strategy results.
- C-R1 / #385: accepted. Recent-history candidate input is outcome-blind and no longer inherits the
  old Event+Outcome-Label outer Run binding.
- B / #319: ACTIVE AND NOT ACCEPTED. It owns recent-history input qualification, PIT/coverage
  evidence and the final input manifest for D.
- D / #321: blocked by B. D is the first result-bearing four-variant historical run.
- E / #322: blocked by D. E is the read-only evidence review and successor decision.

Do not reopen or rerun A, C or C-R1 merely because B is difficult. Do not treat an automatically
closed GitHub issue, merged PR, green unit test or transport success as B acceptance.

### Why the original B recovery path was stopped

B originally attempted to recover missing historical daily-DWD packages by reproducing exact
child-bound historical package/manifest identity. R9 terminated before any strategy/economic result
with p3_strat_003_prior_reconstruction_manifest_mismatch.

The root cause is established: the recovery code resolved a historical builder Git revision from
the package id, but executed the CURRENT build_daily_domain_increment implementation and passed the
historical revision only as identity metadata. The exposed 2026-08-27 package at historical
revision f8f8330 predates the 2026-09-01 9ca9135 builder change that records empty
financial_disclosures_delta as audit-only metadata. Thus package id can match while audit bytes,
content digest and manifest SHA differ. The old suffix path also reused one target revision across
multiple reconstructed dates, which is not valid per-date historical lineage.

Therefore do NOT start R10 on the old exact-manifest mechanism, replace the frozen expected hash,
claim current-builder bytes are the original historical package, or build a generic arbitrary
historical-Git execution platform just to make B pass.

### Approved B protocol correction

The owner approved a pre-result B correction after a four-perspective simulated review
(data-governance/PIT, research-methodology, platform-engineering, strategy-research). The corrected
question is whether a separately identified research reconstruction is semantically exact for the
data consumed by this frozen study, not whether it can impersonate the lost historical package.

The rules are:

1. Original historical package identity remains immutable evidence. Never rewrite it or claim its
   bytes were reproduced when unavailable.
2. A research reconstruction has its own identity and binds exact immutable ODS releases,
   Base/correction authority, PIT/calendar boundaries and frozen reconstruction implementation.
3. Equivalence is checked only on the daily-DWD payload actually consumed by this study:
   dwd_security_bar, dwd_tradability_state and dwd_trading_calendar.
4. Retained original packages on both sides of the known builder-change boundary are overlap
   sentinels. Each sentinel must be a real original prior -> child pair, with exact consumer-table
   file SHA, file size and row-count equality.
5. The missing 2026-08-27 boundary is allowed only under the frozen narrow f8f8330 -> 9ca9135
   compatibility claim and only when financial_disclosures_delta is exactly the preregistered empty
   input.
6. Manifest, package-id and audit-only byte equality are not substitutes for consumer-payload
   equality and are not required when consumer payload is exact.
7. Any payload/PIT/source/implementation drift, or any need for materially broader historical
   execution infrastructure, closes B as insufficient admissible historical input evidence. Do not
   tune reconstruction, select a friendlier window, or inspect D results to repair B.

The frozen probe contract is config/research/p3-strat-003/semantic-equivalence-probe.v1.json.
The implementation is src/quanthus/expert_strategy/p3_strat_003_semantic_equivalence.py.

### Exact current interruption point

As of this handoff:

- GitHub #319 is reopened; its earlier automatic close was bookkeeping error, not acceptance.
- Branch issue-319-p3-strat-003-b-semantic-reconstruction was created from main
  5a8cb3b7eac99531629b8bf6fe5ea24c92474e14.
- Probe-only commit is 26229a0fe93f4f750fad06af965aefb056b34124.
- PR #397, "P3-STRAT-003-B: add pre-result semantic reconstruction probe", is OPEN.
- Probe code/config/event-worker route and focused tests are implemented.
- No real semantic-equivalence probe Job has run yet.
- No full B qualification has rerun under the corrected protocol.
- No D result-bearing run has started.
- P6 has not been modified by this work.

Before continuing, verify #397 and origin/main rather than assuming this status is unchanged.

### Next actions after an interrupted session

Resume in this order and stop at the first failed gate:

1. Read this section and docs/work-items/p3-strat-003.md; confirm #319/#397 and current main.
2. Review and merge PR #397 only if its probe-only scope and verification remain intact.
3. Deploy EVENT-WORKER ONLY for the merged probe revision. Do not deploy or mutate P6 replay-worker,
   P6 admission, ledger, budget or data pointer.
4. Submit exactly one governed P3-STRAT-003-B semantic-equivalence probe Job. It is input-only: no
   candidate scoring, Outcome Label, strategy return or economic result.
5. If the probe fails, preserve the failure. Fix only a demonstrated probe implementation defect
   without changing frozen equivalence semantics, or close B for insufficient admissible input
   evidence. Do not turn this into another R10/R11 loop.
6. If the probe passes, freeze its evidence and then complete the corrected B input manifest. Only
   after B is independently reviewed and accepted may D open.
7. D remains the first point at which frozen 2025/2026 strategy/economic results may be read.

### Anti-drift rules for this task

- Do not read 2025/2026 strategy returns or target outcomes during B.
- Do not roll the 2026 cutoff beyond 2026-09-18 or shrink requested windows.
- Do not change candidate, Trend, Risk, aggregator, guardian, P2, capital, cost or exit semantics to
  make the study easier to run or pass.
- Do not rerun already accepted A/C/C-R1 work as new progress.
- Do not resurrect the stopped exact-manifest retry sequence.
- Do not create a general historical execution platform without separate parent-level authority.
- Do not modify P6 production state from this research line.
- Negative, inconclusive or unavailable historical evidence is a valid outcome; technical
  completion must never be equated with strategy success.

## Development

- Support Python 3.10 or newer.
- Keep production logic under `src/quanthus`; notebooks may only call production modules.
- Do not commit market data, credentials, run artifacts, or local databases.
- Prefer deterministic ordering and explicit reason codes over implicit behavior.
- Run `make check` before a handoff.

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

- GitHub is the only normal code-distribution path. Hermes, CN01, and future nodes each use a
  distinct read-only deploy key and update a node-local Git checkout with `git pull --ff-only`.
  Do not use rsync, scp, or an OCI-built archive to distribute repository code.
- OCI audits Hermes through the reverse tunnel at host-loopback `127.0.0.1:2222`. The
  `hermes-ods-ro` forced-command account and `astock-ro` rrsync jail are read-only; a healthy
  tunnel does not grant shell, deployment, or root authority.
- Codex's default command sandbox may use a different network namespace from the OCI host. A
  failed sandbox `ss` check or `hermes-ods-schedule-check.sh` call is not evidence that the tunnel
  is down. Re-run the approved check in the host network context before reporting an outage.
- Do not probe Hermes root with unrelated keys. Deployment runs on Hermes itself from GitHub;
  OCI verifies exported evidence through the read-only channel.
