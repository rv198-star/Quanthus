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
  P3 may implement the bounded rule-only historical vertical slice through that accepted profile.
- The operator-assisted two-window profile, forward email transport, the full expert set, and the
  training layer remain blocked by their explicit phase and activation gates.

## Development

- Support Python 3.10 or newer.
- Keep production logic under `src/quanthus`; notebooks may only call production modules.
- Do not commit market data, credentials, run artifacts, or local databases.
- Prefer deterministic ordering and explicit reason codes over implicit behavior.
- Run `make check` before a handoff.

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
