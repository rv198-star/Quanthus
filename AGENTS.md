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
