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

## Current active task routing — P3-STRAT-003

- Active line: `P3-STRAT-003` / #317, open and blocked. B / #319 closed with negative input
  qualification evidence; its acceptance gate remains unsatisfied. The bounded successor
  `P3-STRAT-003-B-R1` / #412 is the only active B continuation; it preserves #319's NO_PASS
  evidence and does not unlock D by itself.
- Purpose: compare existing Trend + Position Risk against a non-empty deterministic P4 baseline
  while P6 continues independently; P6 is not a predecessor and must not be mutated.
- Dependency state: A / #318 accepted; C / #320 accepted; C-R1 / #385 accepted; B not accepted
  (`NO_PASS_STATIC_INDUSTRY_INPUT_COVERAGE`); D / #321 and E / #322 remain open and blocked.
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

## Parallel research routing — P5-STAGE-001

- Parallel line: `P5-STAGE-001` / #400. A / #401 is accepted as preregistration-only. The
  2026-09-22 owner handoff separately activates B readiness, C synthetic/environment work, and D
  synthetic engineering under `config/research/p5-stage-001/successor-activation.v1.json`; E remains
  inactive until B/C/D acceptance.
- Purpose: compare the existing rule/Stage surface with fixed simple baselines and one bounded learned
  temporal assessment over the layered 1..12 research vocabulary; it is not a top-1 Stage classifier.
- Before working on this line, read `docs/work-items/p5-stage-001.md`, then the frozen request and
  target set under `config/research/p5-stage-001/`. After interruption, start with the work-item
  `Current recovery checkpoint` and expand only what the current gate needs.
- A permits docs/config/tests and synthetic contract cases only; that sealed predecessor boundary is
  unchanged by the later successor activation.
- A completion does not auto-start B/C/D; the current start comes from the separate bounded owner
  activation receipt.
- The successor receipt does not alter sealed A evidence. B must freeze an exact manifest before its
  first market-row read; C/D remain synthetic until their own gates. No protected 2022-2026 access,
  result-bearing training, P3-STRAT data-authority inheritance, P6 mutation, promotion or trading.
- Negative, partial, data-limited, compute-limited and inconclusive outcomes are all valid.

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
